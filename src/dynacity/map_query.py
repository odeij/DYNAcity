"""Ask the map: planner questions answered from the forecast, never by the model.

A planner asks "which stalled buildings in Hamra are most likely to change?".
A language model translates the question into a `MapQuery`; everything after
that is deterministic:

    question → MapQuery (model) → check_query (code) → run_query (code) → MapAnswer

This is the scenario compiler's split applied to reading instead of writing,
and the same reasoning holds. The model is good at reading intent and has no
access to the numbers, so it is given no authority over anything that must be
true: sector and building-use names are checked against the snapshot, a
threshold is accepted only when the planner wrote that number, and every figure
in the answer — counts, probabilities, heights, and the sentence that states
them — is computed and phrased by code from the snapshot and the forecast. A
model that misreads a question produces a visibly wrong query in the audit
trail, never an invented number.

Answers describe the business-as-usual forecast's next 2-year step, the only
per-building probabilities the engine produces. Horizon KPIs, causes, owners,
rents, and anything else outside the snapshot are listed as unsupported rather
than approximated.

As with scenarios, the model sees only allowlisted aggregates (state counts,
sector and building-use names). Per-object rows and features never leave the
process.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import ForecastResult, UrbanStateSnapshot
from .providers import FAILURE_MESSAGES, DraftProvider, resolve_provider
from .scenario_compiler import (
    STATE_DESCRIPTIONS,
    ValidationIssue,
    ValidationReport,
    _magnitudes_in,
    _norm,
    snapshot_context,
)
from .status import CanonicalState

Metric = Literal["change_probability", "target_probability", "height_m", "floors", "footprint_area_m2"]
PROBABILITY_METRICS = {"change_probability", "target_probability"}
MAX_ROWS = 25

STATE_LABELS: dict[CanonicalState, str] = {
    CanonicalState.STABLE_BUILT: "stable / built",
    CanonicalState.ACTIVE_CONSTRUCTION: "under construction",
    CanonicalState.STALLED_OR_CANCELLED: "stalled or cancelled",
    CanonicalState.RENOVATED: "renovated",
    CanonicalState.VACANT_OR_EVICTED: "vacant or evicted",
    CanonicalState.EMPTY_OR_PARKING: "empty lot or parking",
    CanonicalState.DEMOLISHED: "demolished",
    CanonicalState.UNKNOWN: "not surveyed",
}


class MapQuery(BaseModel):
    """The model's entire output. Nothing here is trusted until checked."""

    model_config = ConfigDict(extra="forbid")

    # rank: the top buildings by metric; count: how many match; by_sector:
    # matching buildings grouped per sector and ranked by the sector average.
    kind: Literal["rank", "count", "by_sector"]
    sectors: list[str] = Field(default_factory=list)
    building_uses: list[str] = Field(default_factory=list)
    # Filter on the building's current (observed) state.
    states: list[CanonicalState] = Field(default_factory=list)
    metric: Metric = "change_probability"
    # Required for target_probability: the state whose chance is measured.
    target_state: CanonicalState | None = None
    # Keeps buildings whose metric is at least / at most this value. Only
    # honoured when the planner wrote the number; probabilities are 0-1.
    threshold: float | None = None
    threshold_direction: Literal["at_least", "at_most"] = "at_least"
    order: Literal["highest", "lowest"] = "highest"
    limit: int = 10
    # Parts of the question the snapshot and forecast cannot answer.
    unsupported_requests: list[str] = Field(default_factory=list)


class AnswerRow(BaseModel):
    object_id: str
    sector: str | None
    building_use: str | None
    state: CanonicalState
    value: float | None


class SectorRow(BaseModel):
    sector: str
    buildings: int
    mean_value: float | None


class MapAnswer(BaseModel):
    text: str
    kind: Literal["rank", "count", "by_sector"]
    metric: Metric
    metric_label: str
    matched: int
    rows: list[AnswerRow] = Field(default_factory=list)
    sectors: list[SectorRow] = Field(default_factory=list)
    highlight_ids: list[str] = Field(default_factory=list)
    basis: str


class AskAttempt(BaseModel):
    query: MapQuery | None
    report: ValidationReport
    model: str | None = None


class AskResult(BaseModel):
    """Everything needed to audit an answer, including rejected attempts."""

    question: str
    baseline_snapshot_id: str
    provider: str
    model: str
    query: MapQuery | None
    answer: MapAnswer | None
    attempts: list[AskAttempt]


def metric_label(query: MapQuery) -> str:
    if query.metric == "change_probability":
        return "chance of leaving the current state in the next 2 years"
    if query.metric == "target_probability":
        target = STATE_LABELS.get(query.target_state, "the target state") if query.target_state else "the target state"
        return f"chance of becoming {target} in the next 2 years"
    return {"height_m": "height", "floors": "floor count", "footprint_area_m2": "footprint area"}[query.metric]


def _threshold_in_question(value: float, question: str) -> bool:
    stated = _magnitudes_in(question)
    return any(abs(abs(value) - s) < 1e-9 or abs(abs(value) * 100 - s) < 1e-6 for s in stated)


def check_query(
    query: MapQuery,
    snapshot: UrbanStateSnapshot,
    question: str,
    forecast: ForecastResult | None,
) -> ValidationReport:
    """Deterministically decide whether a query may run.

    Hard issues name the offending value and the valid choices, so the same
    messages double as repair instructions for the model.
    """

    issues: list[ValidationIssue] = []
    known_sectors = {_norm(item.sector): item.sector for item in snapshot.objects if item.sector}
    known_uses = {_norm(item.building_use): item.building_use for item in snapshot.objects if item.building_use}

    for sector in query.sectors:
        if _norm(sector) not in known_sectors:
            issues.append(ValidationIssue(
                code="query.unknown_sector", severity="hard",
                message=f"sector {sector!r} is not in snapshot {snapshot.snapshot_id}; "
                f"known sectors: {sorted(known_sectors.values())}",
                refs=[sector],
            ))
    for use in query.building_uses:
        if _norm(use) not in known_uses:
            issues.append(ValidationIssue(
                code="query.unknown_building_use", severity="hard",
                message=f"building use {use!r} is not in the snapshot; known uses: {sorted(known_uses.values())}",
                refs=[use],
            ))
    if query.metric == "target_probability":
        if query.target_state is None:
            issues.append(ValidationIssue(
                code="query.target_missing", severity="hard",
                message="metric target_probability needs target_state (the state whose chance is measured)",
            ))
        elif query.target_state == CanonicalState.UNKNOWN:
            issues.append(ValidationIssue(
                code="query.unknown_target", severity="hard",
                message="'unknown' is a survey gap, not a state a building can move to",
            ))
    if query.metric in PROBABILITY_METRICS and forecast is None:
        issues.append(ValidationIssue(
            code="query.no_forecast", severity="hard",
            message="this question needs the forecast, and none is loaded on the server",
        ))
    if query.threshold is not None:
        if not _threshold_in_question(query.threshold, question):
            issues.append(ValidationIssue(
                code="query.threshold_not_in_question", severity="hard",
                message=f"threshold {query.threshold} does not appear in the question; "
                "set a threshold only when the planner states the number",
            ))
        elif query.metric in PROBABILITY_METRICS and not 0.0 <= query.threshold <= 1.0:
            issues.append(ValidationIssue(
                code="query.threshold_out_of_range", severity="hard",
                message=f"probability threshold {query.threshold} must be a fraction between 0 and 1 "
                "(write 50% as 0.5)",
            ))
    if not 1 <= query.limit <= MAX_ROWS:
        issues.append(ValidationIssue(
            code="query.limit_clamped", severity="soft",
            message=f"limit {query.limit} is outside 1..{MAX_ROWS}; showing at most {MAX_ROWS}",
        ))
    for item in query.unsupported_requests:
        issues.append(ValidationIssue(
            code="request.unsupported", severity="soft",
            message=f"not answerable from the snapshot and forecast, so left out: {item}",
        ))
    return ValidationReport(issues=issues)


def _probabilities(forecast: ForecastResult | None) -> dict[str, dict[CanonicalState, float]]:
    if forecast is None:
        return {}
    return {item.object_id: item.probabilities for item in forecast.first_step_transitions}


def _value(query: MapQuery, item: Any, probabilities: dict[str, dict[CanonicalState, float]]) -> float | None:
    if query.metric == "change_probability":
        p = probabilities.get(item.object_id)
        return None if p is None else 1.0 - p.get(item.state, 0.0)
    if query.metric == "target_probability":
        p = probabilities.get(item.object_id)
        return None if p is None or query.target_state is None else p.get(query.target_state, 0.0)
    value = getattr(item, query.metric)
    return None if value is None else float(value)


def _format(query: MapQuery, value: float | None) -> str:
    if value is None:
        return "n/a"
    if query.metric in PROBABILITY_METRICS:
        return f"{value * 100:.0f}%"
    if query.metric == "height_m":
        return f"{value:.1f} m"
    if query.metric == "footprint_area_m2":
        return f"{value:,.0f} m²"
    return f"{value:.0f}"


def _scope(query: MapQuery) -> str:
    states = " or ".join(STATE_LABELS[s] for s in query.states)
    uses = " or ".join(query.building_uses)
    noun = " ".join(part for part in (states, uses.lower(), "buildings") if part)
    where = f" in {', '.join(query.sectors)}" if query.sectors else " across the surveyed area"
    return noun + where


def _threshold_phrase(query: MapQuery) -> str:
    if query.threshold is None:
        return ""
    bound = "at least" if query.threshold_direction == "at_least" else "at most"
    return f" with {metric_label(query)} {bound} {_format(query, query.threshold)}"


def run_query(
    query: MapQuery, snapshot: UrbanStateSnapshot, forecast: ForecastResult | None
) -> MapAnswer:
    """Execute a checked query. Every number and every sentence comes from here.

    Parts of the question the query could not express are named in the answer
    text itself. Found in live testing: asked about vacant buildings in a place
    missing from the data (Karantina), a model dropped the place and the answer
    covered the whole surveyed area — correct numbers for a different question
    unless the sentence says so.
    """

    answer = _run(query, snapshot, forecast)
    if query.unsupported_requests:
        answer.text += f" Not answered: {'; '.join(query.unsupported_requests)}."
    return answer


def _run(query: MapQuery, snapshot: UrbanStateSnapshot, forecast: ForecastResult | None) -> MapAnswer:

    probabilities = _probabilities(forecast)
    sectors = {_norm(s) for s in query.sectors}
    uses = {_norm(u) for u in query.building_uses}
    states = set(query.states)
    limit = max(1, min(query.limit, MAX_ROWS))

    matched: list[tuple[Any, float | None]] = []
    for item in snapshot.objects:
        if sectors and _norm(item.sector) not in sectors:
            continue
        if uses and _norm(item.building_use) not in uses:
            continue
        if states and item.state not in states:
            continue
        value = _value(query, item, probabilities)
        if query.threshold is not None:
            if value is None:
                continue
            if query.threshold_direction == "at_least" and value < query.threshold:
                continue
            if query.threshold_direction == "at_most" and value > query.threshold:
                continue
        matched.append((item, value))

    descending = query.order == "highest"
    valued = sorted(
        (pair for pair in matched if pair[1] is not None),
        key=lambda pair: (pair[1], pair[0].object_id),
        reverse=descending,
    )
    label = metric_label(query)
    scope = _scope(query) + _threshold_phrase(query)
    basis = (
        f"Snapshot {snapshot.snapshot_id} ({snapshot.as_of.isoformat()})"
        + (f"; business-as-usual forecast, model {forecast.model_version}" if forecast is not None
           and query.metric in PROBABILITY_METRICS else "")
    )

    def row(item: Any, value: float | None) -> AnswerRow:
        return AnswerRow(object_id=item.object_id, sector=item.sector, building_use=item.building_use,
                         state=item.state, value=value)

    if not matched:
        return MapAnswer(text=f"No {scope}.", kind=query.kind, metric=query.metric, metric_label=label,
                         matched=0, basis=basis)

    missing = len(matched) - len(valued)
    missing_note = f" {missing} of them have no value for this measure." if missing else ""

    if query.kind == "by_sector":
        groups: dict[str, list[float | None]] = {}
        for item, value in matched:
            groups.setdefault(item.sector or "no sector", []).append(value)
        sector_rows = []
        for sector, values in groups.items():
            present = [v for v in values if v is not None]
            sector_rows.append(SectorRow(
                sector=sector, buildings=len(values),
                mean_value=sum(present) / len(present) if present else None,
            ))
        sector_rows.sort(
            key=lambda r: (r.mean_value is not None, r.mean_value if r.mean_value is not None else 0.0, r.buildings),
            reverse=descending,
        )
        shown = sector_rows[:limit]
        top = ", ".join(f"{r.sector} {_format(query, r.mean_value)} ({r.buildings})" for r in shown[:3])
        text = (f"{len(matched):,} {scope}, in {len(sector_rows)} sector{'s' if len(sector_rows) != 1 else ''}. "
                f"{'Highest' if descending else 'Lowest'} average {label}: {top}.{missing_note}")
        keep = {r.sector for r in shown}
        highlight = [item.object_id for item, _ in matched if (item.sector or "no sector") in keep]
        return MapAnswer(text=text, kind=query.kind, metric=query.metric, metric_label=label,
                         matched=len(matched), sectors=shown, highlight_ids=highlight, basis=basis)

    rows = [row(item, value) for item, value in valued[:limit]]
    if query.kind == "count":
        present = [value for _, value in valued]
        mean = f" Average {label}: {_format(query, sum(present) / len(present))}." if present else ""
        text = f"{len(matched):,} {scope}.{mean}{missing_note}"
        highlight = [item.object_id for item, _ in matched]
    else:
        top = ", ".join(
            f"{r.sector or 'no sector'} · {r.building_use or 'use n/a'} {_format(query, r.value)}" for r in rows[:3]
        )
        text = (f"{len(matched):,} {scope}. {'Highest' if descending else 'Lowest'} {label}: {top}."
                if rows else f"{len(matched):,} {scope}.") + missing_note
        highlight = [r.object_id for r in rows]
    return MapAnswer(text=text, kind=query.kind, metric=query.metric, metric_label=label,
                     matched=len(matched), rows=rows, highlight_ids=highlight, basis=basis)


SYSTEM_PROMPT = f"""You translate an urban planner's question about Beirut buildings into a MapQuery. \
Code runs the query against the building snapshot and the business-as-usual forecast and writes \
the answer; you never state numbers yourself.

Canonical states (a building's current state; also the possible targets):
{chr(10).join(f"- {state.value}: {text}" for state, text in STATE_DESCRIPTIONS.items())}

Metrics:
- change_probability: chance a building leaves its current state in the next 2-year step
- target_probability: chance it becomes target_state in the next 2-year step (set target_state)
- height_m, floors, footprint_area_m2: from the survey

How to fill the query:
- kind is rank for "which / top / most / least" questions, count for "how many", and by_sector \
for comparisons between sectors or "where".
- Use sector and building-use names exactly as listed in the snapshot context. Leave sectors \
empty for the whole surveyed area.
- states filters on the building's current state (e.g. "stalled buildings" → stalled_or_cancelled).
- Set threshold only when the planner states a number ("over 50%" → 0.5 with at_least; \
"taller than 30 m" → 30). Probabilities are fractions between 0 and 1.
- order is highest unless the planner asks for the least / lowest. limit defaults to 10, at most {MAX_ROWS}.
- The per-building forecast covers only the next 2-year step. Anything else — longer horizons, \
city totals, causes, owners, rents, residents, traffic — goes in unsupported_requests rather \
than being approximated. If nothing is answerable, still return a query with the closest \
answerable part, or kind count with no filters, and list the rest as unsupported."""


def _user_message(question: str, snapshot: UrbanStateSnapshot) -> str:
    return (
        f"Snapshot context:\n{json.dumps(snapshot_context(snapshot), indent=2)}\n\n"
        f"Planner's question:\n<question>\n{question}\n</question>"
    )


def _repair_message(question: str, snapshot: UrbanStateSnapshot, query: MapQuery, report: ValidationReport) -> str:
    problems = "\n".join(f"- [{issue.code}] {issue.message}" for issue in report.hard())
    return (
        f"{_user_message(question, snapshot)}\n\n"
        f"Your previous query was rejected by the validator:\n{query.model_dump_json(indent=2)}\n\n"
        f"Hard issues:\n{problems}\n\n"
        "Return a corrected query. Fix only what the issues require; if part of the question cannot be "
        "made valid, drop it and list it in unsupported_requests."
    )


def ask_map(
    question: str,
    snapshot: UrbanStateSnapshot,
    forecast: ForecastResult | None,
    *,
    provider: DraftProvider | None = None,
    max_repairs: int = 1,
) -> AskResult:
    """Answer a planner's question from the snapshot and forecast, or report why not.

    Every attempt is kept so a reviewer can see which query the model proposed
    and which rule rejected it.
    """

    if provider is None:
        provider = resolve_provider()
    if forecast is not None and forecast.baseline_snapshot_id != snapshot.snapshot_id:
        raise ValueError("forecast was produced for a different snapshot")

    attempts: list[AskAttempt] = []
    content = _user_message(question, snapshot)
    accepted: MapQuery | None = None
    for _ in range(max_repairs + 1):
        query, failure = provider.propose(SYSTEM_PROMPT, content, MapQuery)
        served_by = getattr(provider, "last_model", provider.model)
        if query is None:
            attempts.append(AskAttempt(query=None, model=served_by, report=ValidationReport(issues=[
                ValidationIssue(code=failure or "model.no_output", severity="hard",
                                message=FAILURE_MESSAGES.get(failure or "", "the model returned no usable query")),
            ])))
            break
        report = check_query(query, snapshot, question, forecast)
        attempts.append(AskAttempt(query=query, report=report, model=served_by))
        if report.ok:
            accepted = query
            break
        content = _repair_message(question, snapshot, query, report)

    return AskResult(
        question=question,
        baseline_snapshot_id=snapshot.snapshot_id,
        provider=provider.name,
        model=provider.model,
        query=accepted,
        answer=run_query(accepted, snapshot, forecast) if accepted is not None else None,
        attempts=attempts,
    )
