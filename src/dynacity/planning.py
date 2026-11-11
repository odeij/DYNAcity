"""Backcasting and multi-objective search over scenario levers (Task 5.2).

The forward engine answers "what happens under this scenario?". This module
inverts it by search: given candidate levers, KPI targets, and objectives, it
runs the engine over lever combinations and reports

- the Pareto front — combinations no other evaluated combination beats on
  every objective at once, and
- the backcast — the least intensive combinations that meet every target.

Search is deterministic enumeration, not an optimiser and not a model. Lever
sets are small and discrete (a few strengths × a few activation steps per
lever), so exhaustive evaluation is usually affordable and exact; when the
space exceeds `max_evaluations`, a seeded sample is taken that always includes
business-as-usual and every single-lever option. Every candidate reuses the
request seed, so all of them see the same Monte Carlo randomness (common
random numbers) and differences between them are due to the levers, not to
sampling noise.

What this cannot do is make the answer causal. Each lever's effect size is
still an assumption carried in from `TransitionAdjustment`; the search only
finds which assumed levers, at which assumed strengths, would reach a target
in this model. Results are stamped `assumption_based_scenario` for that reason.
"""

from __future__ import annotations

import itertools
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    EvidenceLevel,
    ForecastRequest,
    KpiEstimate,
    ScenarioSpec,
    TransitionAdjustment,
    UrbanStateSnapshot,
)
from .engine import ForecastEngine
from .kpis import DEFAULT_KPIS

Statistic = Literal["p05", "p50", "p95"]


class LeverOption(BaseModel):
    """A lever template plus the settings the search may try for it.

    The template's own `log_odds_delta` and `active_step` are ignored; the
    search substitutes each (delta, step) pair in turn, and also tries the
    lever switched off.
    """

    model_config = ConfigDict(extra="forbid")

    lever: TransitionAdjustment
    deltas: list[float] = Field(default_factory=lambda: [0.5, 1.0, 2.0], min_length=1)
    steps: list[int] = Field(default_factory=lambda: [1], min_length=1)

    @model_validator(mode="after")
    def bounded(self) -> "LeverOption":
        if any(not -8.0 <= d <= 8.0 or d == 0 for d in self.deltas):
            raise ValueError("deltas must be non-zero and within ±8")
        if any(s not in (1, 2, 3) for s in self.steps):
            raise ValueError("steps must be 1, 2, or 3")
        return self


class KpiObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpi: str
    year_offset: Literal[2, 4, 6]
    direction: Literal["minimize", "maximize"]
    # p05/p95 let a planner optimise a pessimistic or optimistic bound rather
    # than the median.
    statistic: Statistic = "p50"


class KpiTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kpi: str
    year_offset: Literal[2, 4, 6]
    operator: Literal[">=", "<="]
    value: float
    statistic: Statistic = "p50"


class PlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: UrbanStateSnapshot
    scenario_id: str = "plan"
    horizon_years: Literal[2, 4, 6] = 6
    levers: list[LeverOption] = Field(min_length=1, max_length=8)
    objectives: list[KpiObjective] = Field(default_factory=list, max_length=4)
    targets: list[KpiTarget] = Field(default_factory=list)
    # Adds "total intervention intensity" as a minimised objective, so the
    # front shows what each extra unit of KPI costs in assumed intervention.
    include_intensity_objective: bool = True
    max_active_levers: int | None = Field(default=None, ge=1)
    max_evaluations: int = Field(default=128, ge=1, le=4096)
    draws: int = Field(default=200, ge=50, le=5000)
    seed: int = Field(default=42, ge=0)
    evidence_bundle_id: str | None = None

    @model_validator(mode="after")
    def coherent(self) -> "PlanningRequest":
        if not self.objectives and not self.targets:
            raise ValueError("give at least one objective or target")
        for item in [*self.objectives, *self.targets]:
            if item.kpi not in DEFAULT_KPIS:
                raise ValueError(f"unsupported KPI {item.kpi!r}")
            if item.year_offset > self.horizon_years:
                raise ValueError(f"{item.kpi} at +{item.year_offset}y is beyond the {self.horizon_years}-year horizon")
        ids = [option.lever.intervention_id for option in self.levers]
        if len(ids) != len(set(ids)):
            raise ValueError("lever intervention_id values must be unique")
        return self


class LeverSetting(BaseModel):
    intervention_id: str
    log_odds_delta: float
    active_step: int


class CandidateResult(BaseModel):
    candidate_id: str
    settings: list[LeverSetting]
    # Σ |delta| × buildings in scope: a size measure for "least intervention".
    intensity: float
    kpis: list[KpiEstimate]
    meets_targets: bool
    # Signed distance to each target at the requested statistic; negative =
    # short of the target.
    target_margins: dict[str, float] = Field(default_factory=dict)
    pareto: bool = False


class PlanningResult(BaseModel):
    baseline_snapshot_id: str
    model_version: str
    evidence_level: EvidenceLevel = EvidenceLevel.ASSUMPTION_BASED_SCENARIO
    evidence_bundle_id: str | None = None
    search: Literal["exhaustive", "sampled"]
    space_size: int
    evaluated: int
    business_as_usual_id: str
    pareto_front: list[str]
    backcast: list[str]
    candidates: list[CandidateResult]
    warnings: list[str] = Field(default_factory=list)


Setting = tuple[float, int] | None


def _choices(option: LeverOption, horizon_years: int) -> list[Setting]:
    max_step = horizon_years // 2
    return [None] + [(d, s) for d in option.deltas for s in option.steps if s <= max_step]


def _scope_size(lever: TransitionAdjustment, snapshot: UrbanStateSnapshot) -> int:
    ids = set(lever.object_ids)
    states = set(lever.eligible_from_states)
    return sum(
        1
        for item in snapshot.objects
        if (not ids or item.object_id in ids) and (not states or item.state in states)
    )


def _combinations(request: PlanningRequest) -> tuple[list[tuple[Setting, ...]], int, str]:
    per_lever = [_choices(option, request.horizon_years) for option in request.levers]
    space_size = int(np.prod([len(c) for c in per_lever]))

    def allowed(combo: tuple[Setting, ...]) -> bool:
        active = sum(setting is not None for setting in combo)
        return request.max_active_levers is None or active <= request.max_active_levers

    if space_size <= request.max_evaluations:
        return [c for c in itertools.product(*per_lever) if allowed(c)], space_size, "exhaustive"

    # Sampled: BAU and every single-lever setting first, so the one-lever
    # baseline for each lever is always visible, then seeded random fill.
    bau: tuple[Setting, ...] = tuple(None for _ in per_lever)
    chosen: list[tuple[Setting, ...]] = [bau]
    for index, choices in enumerate(per_lever):
        for setting in choices[1:]:
            combo = list(bau)
            combo[index] = setting
            chosen.append(tuple(combo))
    seen = set(chosen)
    rng = np.random.default_rng(request.seed)
    attempts = 0
    while len(chosen) < request.max_evaluations and attempts < request.max_evaluations * 50:
        attempts += 1
        combo = tuple(choices[rng.integers(len(choices))] for choices in per_lever)
        if combo not in seen and allowed(combo):
            seen.add(combo)
            chosen.append(combo)
    return [c for c in chosen if allowed(c)][: request.max_evaluations], space_size, "sampled"


def _value(kpis: list[KpiEstimate], kpi: str, year: int, statistic: Statistic) -> float:
    for estimate in kpis:
        if estimate.kpi == kpi and estimate.year_offset == year:
            return float(getattr(estimate, statistic))
    raise KeyError(f"{kpi} at +{year}y missing from forecast")


def _pareto_mask(scores: np.ndarray) -> np.ndarray:
    """Non-dominated rows of a (candidates, objectives) matrix, all minimised."""

    mask = np.ones(len(scores), dtype=bool)
    for i in range(len(scores)):
        others = np.delete(scores, i, axis=0)
        dominated = np.any(np.all(others <= scores[i], axis=1) & np.any(others < scores[i], axis=1))
        mask[i] = not dominated
    return mask


def plan(engine: ForecastEngine, request: PlanningRequest, *, backcast_size: int = 5) -> PlanningResult:
    combos, space_size, search = _combinations(request)
    scopes = [_scope_size(option.lever, request.snapshot) for option in request.levers]
    needed_kpis = sorted({item.kpi for item in [*request.objectives, *request.targets]})
    warnings = [
        "Lever effect sizes are assumptions; the search finds combinations that reach targets "
        "in this model, not interventions proven to reach them.",
    ]
    if search == "sampled":
        warnings.append(
            f"Search sampled {len(combos)} of {space_size} combinations; the front and backcast "
            "are best among those evaluated, not guaranteed global optima."
        )

    candidates: list[CandidateResult] = []
    for index, combo in enumerate(combos):
        interventions, settings, intensity = [], [], 0.0
        for option, setting, scope in zip(request.levers, combo, scopes, strict=True):
            if setting is None:
                continue
            delta, step = setting
            interventions.append(option.lever.model_copy(update={"log_odds_delta": delta, "active_step": step}))
            settings.append(LeverSetting(
                intervention_id=option.lever.intervention_id, log_odds_delta=delta, active_step=step
            ))
            intensity += abs(delta) * scope
        forecast = engine.forecast(ForecastRequest(
            snapshot=request.snapshot,
            scenario=ScenarioSpec(
                scenario_id=f"{request.scenario_id}-{index:04d}",
                baseline_snapshot_id=request.snapshot.snapshot_id,
                horizon_years=request.horizon_years,
                interventions=interventions,
                requested_kpis=needed_kpis,
                evidence_bundle_id=request.evidence_bundle_id,
            ),
            draws=request.draws,
            seed=request.seed,
        ))
        margins = {}
        for target in request.targets:
            value = _value(forecast.kpis, target.kpi, target.year_offset, target.statistic)
            margin = value - target.value if target.operator == ">=" else target.value - value
            margins[f"{target.kpi}@{target.year_offset}y {target.operator} {target.value:g} ({target.statistic})"] = margin
        candidates.append(CandidateResult(
            candidate_id="bau" if not settings else f"c{index:04d}",
            settings=settings,
            intensity=round(intensity, 6),
            kpis=forecast.kpis,
            meets_targets=all(m >= 0 for m in margins.values()),
            target_margins=margins,
        ))

    feasible = [c for c in candidates if c.meets_targets] if request.targets else candidates
    if request.objectives or request.include_intensity_objective:
        pool = feasible or candidates
        columns = []
        for objective in request.objectives:
            sign = 1.0 if objective.direction == "minimize" else -1.0
            columns.append([
                sign * _value(c.kpis, objective.kpi, objective.year_offset, objective.statistic)
                for c in pool
            ])
        if request.include_intensity_objective:
            columns.append([c.intensity for c in pool])
        mask = _pareto_mask(np.asarray(columns, dtype=float).T)
        for candidate, on_front in zip(pool, mask, strict=True):
            candidate.pareto = bool(on_front)
        if not feasible and request.targets:
            warnings.append("No evaluated combination meets every target; the front is over all candidates.")

    ranked: list[CandidateResult] = []
    if request.targets and feasible:
        # Backcast = the least intervention that still reaches every target.
        ranked = sorted(feasible, key=lambda c: (c.intensity, len(c.settings)))
    elif request.targets:
        # Nothing reaches the targets: rank by total shortfall so the closest
        # attempts are still reported, clearly labelled as not meeting them.
        def shortfall(candidate: CandidateResult) -> float:
            return -sum(min(margin, 0.0) for margin in candidate.target_margins.values())

        ranked = sorted(candidates, key=lambda c: (shortfall(c), c.intensity))
        warnings.append("Backcast lists the closest combinations; none meets every target.")

    return PlanningResult(
        baseline_snapshot_id=request.snapshot.snapshot_id,
        model_version=engine.bundle.model_version,
        evidence_bundle_id=request.evidence_bundle_id,
        search=search,
        space_size=space_size,
        evaluated=len(candidates),
        business_as_usual_id="bau",
        pareto_front=[c.candidate_id for c in candidates if c.pareto],
        backcast=[c.candidate_id for c in ranked[:backcast_size]],
        candidates=candidates,
        warnings=warnings,
    )
