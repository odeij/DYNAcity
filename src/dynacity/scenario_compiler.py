"""Natural-language scenario compiler: the model proposes, deterministic code disposes.

A planner describes a scenario in prose ("heritage grant pushes stalled
buildings in Mar Mikhael toward renovation"). A language model translates that
into a `ScenarioDraft`; everything after that is deterministic:

    request text → ScenarioDraft (model) → check_draft (code) → resolve_draft (code) → ScenarioSpec

The split is the point of the module, not an implementation detail. The model
is good at reading intent and bad at knowing what exists, so it is given no
authority over anything that must be true:

- Scope. The model writes a *selector* (sectors, building uses, explicit ids);
  `resolve_draft` turns it into object ids against the snapshot. A sector that
  does not exist is a hard issue, not a silently empty intervention.
- Effect size. The model picks a qualitative `effect_strength`; the numeric
  log-odds delta comes from `EFFECT_STRENGTH_LOG_ODDS`, a reviewable constant.
  A numeric delta is accepted only when that number appears in the planner's
  own text.
- Provenance. A lever cites either a source in the frozen `EvidenceBundle`
  (`evidence_source_id`, checked for existence, supersession and effective
  dates), a source quoted from the planner's text, or the explicit `UNSOURCED`
  marker. A source the model introduced itself is rejected — that is an
  invented citation, the failure this module exists to prevent.

Hard issues reject the draft and are fed back for one repair round; soft issues
travel with the compiled scenario. The compiled `ScenarioSpec` is then an
ordinary input to `ForecastEngine`, so anything it produces is still stamped
`assumption_based_scenario`.

Only aggregate, allowlisted BBED attributes (state counts, sector and building
use names) are sent to the model. Per-object features — including the private
AUB point-cloud morphology — never leave the process.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ConfidenceGrade, ScenarioSpec, TransitionAdjustment, UrbanStateSnapshot
from .evidence import EvidenceBundle, citation_problems
from .providers import FAILURE_MESSAGES, AnthropicProvider, DraftProvider, resolve_provider
from .kpis import DEFAULT_KPIS
from .status import CanonicalState

UNSOURCED = "unsourced planner assumption"

# Qualitative strength → log-odds shift. Kept deliberately modest: the contract
# allows ±8, but a delta of 2 already multiplies the target's odds by ~7.4,
# which is a strong claim for an unevidenced lever. Change these numbers here,
# in review, never in the prompt.
EFFECT_STRENGTH_LOG_ODDS: dict[str, float] = {
    "weak": 0.5,
    "moderate": 1.0,
    "strong": 2.0,
}

STATE_DESCRIPTIONS: dict[CanonicalState, str] = {
    CanonicalState.STABLE_BUILT: "complete, inhabited or in use",
    CanonicalState.ACTIVE_CONSTRUCTION: "under construction or an active site",
    CanonicalState.STALLED_OR_CANCELLED: "construction on hold or cancelled",
    CanonicalState.RENOVATED: "renovated",
    CanonicalState.VACANT_OR_EVICTED: "evicted, threatened with eviction, or uninhabited",
    CanonicalState.EMPTY_OR_PARKING: "empty lot or parking lot",
    CanonicalState.DEMOLISHED: "demolished",
    CanonicalState.UNKNOWN: "status not surveyed (not a valid intervention target)",
}


class ObjectSelector(BaseModel):
    """Which buildings a lever applies to, resolved by code, never by the model.

    All filters combine with AND. An entirely empty selector means city-wide,
    matching `TransitionAdjustment`'s broad-by-default semantics — and is
    flagged as a soft issue so a planner sees that the lever is not local.
    """

    model_config = ConfigDict(extra="forbid")

    sectors: list[str] = Field(default_factory=list)
    building_uses: list[str] = Field(default_factory=list)
    object_ids: list[str] = Field(default_factory=list)


class DraftIntervention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intervention_id: str
    description: str
    selector: ObjectSelector
    eligible_from_states: list[CanonicalState] = Field(default_factory=list)
    target_state: CanonicalState
    effect_strength: Literal["weak", "moderate", "strong"]
    # Only honoured when the planner wrote this number; see check_draft.
    explicit_log_odds_delta: float | None = None
    # 1-based 2-year rollout step on which the lever acts.
    active_step: int = 1
    # A source_id from the evidence bundle, when the lever rests on one.
    evidence_source_id: str | None = None
    effect_source: str
    confidence_grade: ConfidenceGrade = ConfidenceGrade.LOW


class ScenarioDraft(BaseModel):
    """The model's entire output. Nothing here is trusted until checked."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    horizon_years: Literal[2, 4, 6]
    interventions: list[DraftIntervention] = Field(default_factory=list)
    requested_kpis: list[str] = Field(default_factory=list)
    # Parts of the request this schema cannot express (e.g. traffic, rents).
    # Surfaced as soft issues so they are visibly dropped, not silently lost.
    unsupported_requests: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["hard", "soft"]
    message: str
    refs: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "hard" for issue in self.issues)

    def hard(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "hard"]


class CompileAttempt(BaseModel):
    draft: ScenarioDraft | None
    report: ValidationReport
    # The model that actually answered (a provider may fall back to another).
    model: str | None = None


class CompilationResult(BaseModel):
    """Everything needed to audit a compile, including rejected attempts."""

    request_text: str
    baseline_snapshot_id: str
    provider: str
    model: str
    scenario: ScenarioSpec | None
    attempts: list[CompileAttempt]

    @property
    def ok(self) -> bool:
        return self.scenario is not None


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _magnitudes_in(text: str) -> set[float]:
    # Magnitudes, because "reduce the odds by 1.5" legitimately becomes -1.5.
    return {float(match) for match in re.findall(r"\d+(?:\.\d+)?", text)}


# A lever may cover the whole city only when the planner said so. Found in
# live testing: asked about a place missing from the data (Karantina), a model
# dropped the place and applied a demolition lever to all 3,144 eligible
# buildings. Conservative on purpose — a missed phrase costs a rephrase, a false
# match costs a wrong city-wide scenario. English, French, and Arabic.
_CITYWIDE = re.compile(
    r"city[- ]?wide|across (the )?(city|beirut)|all (of )?beirut|(the )?whole (city|of beirut)|"
    r"throughout (the city|beirut)|everywhere|all buildings|every building|"
    r"toute la ville|tout beyrouth|l'ensemble de (la ville|beyrouth)|partout|"
    r"كل بيروت|كامل بيروت|جميع أنحاء بيروت|كل المدينة|كامل المدينة|في جميع أنحاء المدينة",
    re.IGNORECASE,
)


def _asks_citywide(request_text: str) -> bool:
    return bool(_CITYWIDE.search(request_text))


def _selected_ids(
    selector: ObjectSelector,
    eligible_from_states: list[CanonicalState],
    snapshot: UrbanStateSnapshot,
) -> list[str]:
    sectors = {_norm(value) for value in selector.sectors}
    uses = {_norm(value) for value in selector.building_uses}
    ids = set(selector.object_ids)
    states = set(eligible_from_states)
    return [
        item.object_id
        for item in snapshot.objects
        if (not sectors or _norm(item.sector) in sectors)
        and (not uses or _norm(item.building_use) in uses)
        and (not ids or item.object_id in ids)
        and (not states or item.state in states)
    ]


def check_draft(
    draft: ScenarioDraft,
    snapshot: UrbanStateSnapshot,
    request_text: str,
    evidence: EvidenceBundle | None = None,
) -> ValidationReport:
    """Deterministically decide whether a draft may become a ScenarioSpec.

    Hard issues name the offending value and, where useful, the valid choices,
    so the same messages double as repair instructions for the model.
    """

    issues: list[ValidationIssue] = []
    text = _norm(request_text)
    known_sectors = {_norm(item.sector): item.sector for item in snapshot.objects if item.sector}
    known_uses = {
        _norm(item.building_use): item.building_use for item in snapshot.objects if item.building_use
    }
    known_ids = {item.object_id for item in snapshot.objects}
    max_step = draft.horizon_years // 2

    for kpi in draft.requested_kpis:
        if kpi not in DEFAULT_KPIS:
            issues.append(ValidationIssue(
                code="kpi.unknown", severity="hard",
                message=f"KPI {kpi!r} is not supported; choose from {list(DEFAULT_KPIS)}",
                refs=[kpi],
            ))

    seen_ids: set[str] = set()
    for lever in draft.interventions:
        ref = lever.intervention_id
        if ref in seen_ids:
            issues.append(ValidationIssue(
                code="intervention.duplicate_id", severity="hard",
                message=f"intervention_id {ref!r} is used more than once", refs=[ref],
            ))
        seen_ids.add(ref)

        if lever.target_state == CanonicalState.UNKNOWN:
            issues.append(ValidationIssue(
                code="intervention.unknown_target", severity="hard",
                message=f"{ref}: 'unknown' is a survey gap, not a state a policy can push toward",
                refs=[ref],
            ))
        if lever.target_state in lever.eligible_from_states:
            issues.append(ValidationIssue(
                code="intervention.target_in_origin", severity="soft",
                message=f"{ref}: target {lever.target_state.value!r} is also an eligible origin state, "
                "so part of the lever only reinforces staying put",
                refs=[ref],
            ))
        if not 1 <= lever.active_step <= max_step:
            issues.append(ValidationIssue(
                code="intervention.step_out_of_horizon", severity="hard",
                message=f"{ref}: active_step {lever.active_step} is outside 1..{max_step} "
                f"for a {draft.horizon_years}-year horizon",
                refs=[ref],
            ))

        for sector in lever.selector.sectors:
            if _norm(sector) not in known_sectors:
                issues.append(ValidationIssue(
                    code="selector.unknown_sector", severity="hard",
                    message=f"{ref}: sector {sector!r} is not in snapshot {snapshot.snapshot_id}; "
                    f"known sectors: {sorted(known_sectors.values())}",
                    refs=[ref, sector],
                ))
        for use in lever.selector.building_uses:
            if _norm(use) not in known_uses:
                issues.append(ValidationIssue(
                    code="selector.unknown_building_use", severity="hard",
                    message=f"{ref}: building use {use!r} is not in the snapshot; "
                    f"known uses: {sorted(known_uses.values())}",
                    refs=[ref, use],
                ))
        for object_id in lever.selector.object_ids:
            if object_id not in known_ids:
                issues.append(ValidationIssue(
                    code="selector.unknown_object", severity="hard",
                    message=f"{ref}: object {object_id!r} is not in snapshot {snapshot.snapshot_id}",
                    refs=[ref, object_id],
                ))

        scoped = _selected_ids(lever.selector, lever.eligible_from_states, snapshot)
        if not scoped:
            issues.append(ValidationIssue(
                code="selector.empty_scope", severity="hard",
                message=f"{ref}: selector and eligible_from_states match no buildings in the snapshot",
                refs=[ref],
            ))
        elif lever.selector == ObjectSelector():
            if _asks_citywide(request_text):
                issues.append(ValidationIssue(
                    code="selector.citywide", severity="soft",
                    message=f"{ref}: no spatial filter; applies to all {len(scoped)} eligible buildings",
                    refs=[ref],
                ))
            else:
                issues.append(ValidationIssue(
                    code="selector.citywide_not_requested", severity="hard",
                    message=f"{ref}: the lever has no spatial filter and would cover all {len(scoped)} eligible "
                    "buildings, but the request does not ask for the whole city; scope it to listed sectors "
                    "or ids, or drop the lever and list the place under unsupported_requests",
                    refs=[ref],
                ))
        if scoped and lever.selector.building_uses:
            unfiltered = _selected_ids(
                lever.selector.model_copy(update={"building_uses": []}), lever.eligible_from_states, snapshot
            )
            if len(unfiltered) > len(scoped):
                issues.append(ValidationIssue(
                    code="selector.use_filter_narrows", severity="soft",
                    message=f"{ref}: the building-use filter {lever.selector.building_uses} leaves out "
                    f"{len(unfiltered) - len(scoped)} of {len(unfiltered)} buildings otherwise in scope",
                    refs=[ref],
                ))

        if lever.explicit_log_odds_delta is not None:
            delta = lever.explicit_log_odds_delta
            if abs(delta) not in _magnitudes_in(request_text):
                issues.append(ValidationIssue(
                    code="effect.delta_not_in_request", severity="hard",
                    message=f"{ref}: explicit_log_odds_delta {delta} does not appear in the request; "
                    "use effect_strength unless the planner stated the number",
                    refs=[ref],
                ))
            elif not -8.0 <= delta <= 8.0:
                issues.append(ValidationIssue(
                    code="effect.delta_out_of_bounds", severity="hard",
                    message=f"{ref}: log-odds delta {delta} is outside the contract's ±8 bound",
                    refs=[ref],
                ))

        source = _norm(lever.effect_source)
        if lever.evidence_source_id is not None:
            if evidence is None:
                issues.append(ValidationIssue(
                    code="evidence.no_bundle", severity="hard",
                    message=f"{ref}: evidence_source_id given but no evidence bundle was supplied",
                    refs=[ref],
                ))
            else:
                cited = evidence.get(lever.evidence_source_id)
                if cited is not None and not any(_norm(name) in text for name in cited.names()):
                    # Found in live testing: a model attached a registered law
                    # to a grant the planner never linked to it.
                    issues.append(ValidationIssue(
                        code="evidence.not_requested", severity="hard",
                        message=f"{ref}: the request does not refer to {lever.evidence_source_id!r} "
                        f"(known as {cited.names()}); cite a source only when the planner names it",
                        refs=[ref, lever.evidence_source_id],
                    ))
                for code, message in citation_problems(evidence, lever.evidence_source_id, snapshot.as_of):
                    issues.append(ValidationIssue(
                        code=code, severity="soft" if code.endswith(".unverified") else "hard",
                        message=f"{ref}: {message}", refs=[ref, lever.evidence_source_id],
                    ))
        elif source == UNSOURCED:
            issues.append(ValidationIssue(
                code="effect.unsourced", severity="soft",
                message=f"{ref}: no source given for the effect size; confidence forced to low",
                refs=[ref],
            ))
        elif len(source) < 3 or source not in text:
            issues.append(ValidationIssue(
                code="effect.source_not_in_request", severity="hard",
                message=f"{ref}: effect_source {lever.effect_source!r} is not quoted from the request; "
                f"quote the planner's source verbatim or use {UNSOURCED!r}",
                refs=[ref],
            ))

    for item in draft.unsupported_requests:
        issues.append(ValidationIssue(
            code="request.unsupported", severity="soft",
            message=f"not expressible as a building-state lever and dropped: {item}",
        ))
    return ValidationReport(issues=issues)


def _provenance(
    lever: DraftIntervention, evidence: EvidenceBundle | None
) -> tuple[str, ConfidenceGrade]:
    """Effect source string and the confidence the evidence can actually carry."""

    if lever.evidence_source_id is not None and evidence is not None:
        cited = evidence.get(lever.evidence_source_id)
        grade = lever.confidence_grade if cited and cited.verified else ConfidenceGrade.LOW
        return evidence.reference(lever.evidence_source_id), grade
    if _norm(lever.effect_source) == UNSOURCED:
        return UNSOURCED, ConfidenceGrade.LOW
    return lever.effect_source.strip(), lever.confidence_grade


def resolve_draft(
    draft: ScenarioDraft, snapshot: UrbanStateSnapshot, evidence: EvidenceBundle | None = None
) -> ScenarioSpec:
    """Turn a checked draft into the engine contract. Call only when check_draft is ok."""

    interventions = []
    for lever in draft.interventions:
        delta = (
            lever.explicit_log_odds_delta
            if lever.explicit_log_odds_delta is not None
            else EFFECT_STRENGTH_LOG_ODDS[lever.effect_strength]
        )
        effect_source, grade = _provenance(lever, evidence)
        interventions.append(TransitionAdjustment(
            intervention_id=lever.intervention_id,
            # Resolved against eligible states too, so the id list is exactly
            # the set of buildings the planner can be shown as "affected".
            object_ids=_selected_ids(lever.selector, lever.eligible_from_states, snapshot),
            eligible_from_states=lever.eligible_from_states,
            target_state=lever.target_state,
            log_odds_delta=delta,
            active_step=lever.active_step,
            effect_source=effect_source,
            confidence_grade=grade,
        ))
    return ScenarioSpec(
        scenario_id=draft.scenario_id,
        baseline_snapshot_id=snapshot.snapshot_id,
        horizon_years=draft.horizon_years,
        interventions=interventions,
        requested_kpis=draft.requested_kpis,
        evidence_bundle_id=evidence.bundle_id if evidence is not None else None,
    )


def evidence_context(evidence: EvidenceBundle) -> list[dict[str, Any]]:
    """Citable sources as the model sees them: identity and status, no judgement."""

    return [
        {
            "source_id": s.source_id,
            "title": s.title,
            "kind": s.kind,
            "aliases": s.aliases,
            "status": s.status,
            "superseded_by": evidence.superseded_by(s.source_id),
            "effective_from": s.effective_from.isoformat() if s.effective_from else None,
            "effective_until": s.effective_until.isoformat() if s.effective_until else None,
        }
        for s in evidence.sources
    ]


def snapshot_context(snapshot: UrbanStateSnapshot) -> dict[str, Any]:
    """The only view of the city the model receives: allowlisted aggregates.

    No per-object rows and no `features`, so private point-cloud morphology and
    anything else outside the BBED allowlist stays local.
    """

    return {
        "snapshot_id": snapshot.snapshot_id,
        "as_of": snapshot.as_of.isoformat(),
        "building_count": len(snapshot.objects),
        "state_counts": dict(sorted(Counter(item.state.value for item in snapshot.objects).items())),
        "sectors": dict(sorted(Counter(item.sector for item in snapshot.objects if item.sector).items())),
        "building_uses": dict(
            sorted(Counter(item.building_use for item in snapshot.objects if item.building_use).items())
        ),
    }


SYSTEM_PROMPT = f"""You translate an urban planner's scenario for Beirut into a ScenarioDraft for a \
building-state forecasting engine. The engine moves each building between canonical states in \
2-year steps; a scenario can only nudge the odds of a target state for a chosen set of buildings.

Canonical states:
{chr(10).join(f"- {state.value}: {text}" for state, text in STATE_DESCRIPTIONS.items())}

Supported KPIs (leave requested_kpis empty for all): {", ".join(DEFAULT_KPIS)}

How to fill the draft:
- Scope buildings with selector.sectors / selector.building_uses using names exactly as listed \
in the snapshot context, and selector.object_ids only for ids the planner wrote. Use \
eligible_from_states to restrict which current states the lever acts on.
- Choose effect_strength (weak / moderate / strong) from the planner's wording. Set \
explicit_log_odds_delta only when the planner states a log-odds number.
- When the planner refers to a source listed under "Citable evidence" (by title or alias), set \
evidence_source_id to its source_id and effect_source to its title. Never attach a source the \
planner did not mention, and never cite one marked as superseded.
- If the planner names a place that is not in the sector list, do not widen the lever to the \
whole city: leave that lever out and list the place under unsupported_requests. A lever with no \
spatial filter is only for requests that explicitly cover the whole city.
- Otherwise effect_source must be copied verbatim from the planner's text (a law, report, or \
study they named). If they named none, write exactly "{UNSOURCED}". Never supply a source yourself.
- confidence_grade reflects only what the planner claims about that source; default to low.
- horizon_years is 2, 4, or 6; default to 6 when unstated. active_step counts 2-year steps from 1.
- Anything that cannot be expressed as a building-state lever (traffic, rents, population, \
infrastructure capacity) goes in unsupported_requests rather than being approximated.
- scenario_id is a short kebab-case slug."""


def _user_message(
    request_text: str, snapshot: UrbanStateSnapshot, evidence: EvidenceBundle | None = None
) -> str:
    sources = (
        f"Citable evidence (bundle {evidence.bundle_id}):\n"
        f"{json.dumps(evidence_context(evidence), indent=2)}\n\n"
        if evidence is not None
        else "Citable evidence: none supplied.\n\n"
    )
    return (
        f"Snapshot context:\n{json.dumps(snapshot_context(snapshot), indent=2)}\n\n"
        f"{sources}"
        f"Planner's scenario:\n<scenario>\n{request_text}\n</scenario>"
    )


def _repair_message(
    request_text: str,
    snapshot: UrbanStateSnapshot,
    draft: ScenarioDraft,
    report: ValidationReport,
    evidence: EvidenceBundle | None = None,
) -> str:
    problems = "\n".join(f"- [{issue.code}] {issue.message}" for issue in report.hard())
    return (
        f"{_user_message(request_text, snapshot, evidence)}\n\n"
        f"Your previous draft was rejected by the validator:\n{draft.model_dump_json(indent=2)}\n\n"
        f"Hard issues:\n{problems}\n\n"
        "Return a corrected draft. Fix only what the issues require; if a lever cannot be made "
        "valid, remove it and list it in unsupported_requests."
    )


def compile_scenario(
    request_text: str,
    snapshot: UrbanStateSnapshot,
    *,
    provider: DraftProvider | None = None,
    client: Any | None = None,
    model: str | None = None,
    max_repairs: int = 1,
    evidence: EvidenceBundle | None = None,
) -> CompilationResult:
    """Compile planner prose into a ScenarioSpec, or report why it could not be.

    Every attempt — accepted or rejected — is kept in the result so a reviewer
    can see what the model proposed and exactly which rule rejected it. Each
    call is stateless: the repair round restates the request, the rejected
    draft, and the issues rather than continuing a conversation.

    The model comes from `provider` if given; a bare `client` is treated as an
    Anthropic client (kept for callers that inject one); otherwise
    `providers.resolve_provider` picks from the environment.
    """

    if provider is None:
        provider = AnthropicProvider(client, model) if client is not None else resolve_provider(model=model)

    attempts: list[CompileAttempt] = []
    content = _user_message(request_text, snapshot, evidence)
    scenario = None
    for _ in range(max_repairs + 1):
        draft, failure = provider.propose(SYSTEM_PROMPT, content, ScenarioDraft)
        served_by = getattr(provider, "last_model", provider.model)
        if draft is None:
            attempts.append(CompileAttempt(draft=None, model=served_by, report=ValidationReport(issues=[
                ValidationIssue(code=failure or "model.no_output", severity="hard",
                                message=FAILURE_MESSAGES.get(failure or "", "the model returned no usable draft")),
            ])))
            break
        report = check_draft(draft, snapshot, request_text, evidence)
        attempts.append(CompileAttempt(draft=draft, report=report, model=served_by))
        if report.ok:
            scenario = resolve_draft(draft, snapshot, evidence)
            break
        content = _repair_message(request_text, snapshot, draft, report, evidence)

    return CompilationResult(
        request_text=request_text,
        baseline_snapshot_id=snapshot.snapshot_id,
        provider=provider.name,
        model=provider.model,
        scenario=scenario,
        attempts=attempts,
    )
