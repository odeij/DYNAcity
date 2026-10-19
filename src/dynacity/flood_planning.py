"""Budget-constrained flood experiments; no hydraulic solver or causal optimizer.

Evaluators must use the supplied immutable inputs and return their fingerprints.
This contract detects mismatched runs, not dishonest or incorrect model adapters.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class InterventionKind(str, Enum):
    DRAIN_MAINTENANCE = "drain_maintenance"
    RETENTION = "retention"
    PERMEABLE_SURFACE = "permeable_surface"
    ACCESS_PROTECTION = "access_protection"


class Parameter(FrozenModel):
    name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)


class CostAssumption(FrozenModel):
    """Caller-supplied total cost for one choice, always an assumption here."""
    amount: Decimal = Field(ge=0, max_digits=24, decimal_places=6)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    source: str = Field(min_length=1)
    status: str = Field(default="assumed", pattern="^assumed$")


class InterventionChoice(FrozenModel):
    choice_id: str = Field(min_length=1)
    kind: InterventionKind
    target_ids: tuple[str, ...] = Field(min_length=1)
    parameters: tuple[Parameter, ...] = ()
    cost: CostAssumption

    @model_validator(mode="after")
    def unique_parameters(self) -> InterventionChoice:
        if len({p.name for p in self.parameters}) != len(self.parameters):
            raise ValueError("parameter names must be unique")
        if any(not target.strip() for target in self.target_ids):
            raise ValueError("target IDs must be nonblank")
        return self


class Plan(FrozenModel):
    plan_id: str = Field(min_length=1)
    choices: tuple[InterventionChoice, ...] = ()

    @model_validator(mode="after")
    def unique_choices(self) -> Plan:
        if len({c.choice_id for c in self.choices}) != len(self.choices):
            raise ValueError("choice IDs must be unique within a plan")
        return self

    @property
    def fingerprint(self) -> str:
        return _digest(self.model_dump(mode="json"))


BASELINE = Plan(plan_id="baseline")


class Budget(FrozenModel):
    amount: Decimal = Field(ge=0, max_digits=24, decimal_places=6)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ExperimentInputs(FrozenModel):
    """JSON manifests must include data IDs, units, time, spatial references.

    The baseline manifest describes the fixed existing physical system. Choices
    modify that system only inside the evaluator. Include engine settings and
    input content checksums, not just mutable paths, for reproducibility.
    """
    forcing_json: str
    baseline_json: str
    settings_json: str
    seed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def canonical_manifests(self) -> ExperimentInputs:
        for name in ("forcing_json", "baseline_json", "settings_json"):
            value = json.loads(getattr(self, name))
            if not isinstance(value, dict):
                raise ValueError("input manifests must be JSON objects")
            object.__setattr__(self, name, _json(value))
        return self

    @classmethod
    def from_inputs(cls, *, forcing: dict[str, Any], baseline: dict[str, Any],
                    settings: dict[str, Any], seed: int = 0) -> ExperimentInputs:
        return cls(forcing_json=_json(forcing), baseline_json=_json(baseline),
                   settings_json=_json(settings), seed=seed)

    @property
    def fingerprint(self) -> str:
        return _digest(self.model_dump(mode="json"))


class Metric(str, Enum):
    EXPOSED_BUILDINGS = "exposed_buildings"
    EXPOSED_AREA = "exposed_area_m2"
    CLOSURE_DURATION = "closure_duration_hours"
    ACCESS_FRACTION = "accessible_services_fraction"

    @property
    def unit(self) -> str:
        return {self.EXPOSED_BUILDINGS: "buildings", self.EXPOSED_AREA: "m2",
                self.CLOSURE_DURATION: "h", self.ACCESS_FRACTION: "fraction"}[self]

    @property
    def direction(self) -> str:
        return "maximize" if self == self.ACCESS_FRACTION else "minimize"


class MetricValue(FrozenModel):
    metric: Metric
    value: float = Field(ge=0)
    # Same definition and coverage required for a valid comparison. Include
    # thresholds, aggregation, time window and asset/service inventory version.
    definition: str = Field(min_length=1)
    coverage: str = Field(min_length=1)
    uncertainty: str = Field(min_length=1)
    lower: float | None = Field(default=None, ge=0)
    upper: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_bounds(self) -> MetricValue:
        if (self.lower is None) != (self.upper is None):
            raise ValueError("supply both uncertainty bounds or neither")
        if self.lower is not None and not self.lower <= self.value <= self.upper:
            raise ValueError("bounds must contain value")
        if self.metric == Metric.ACCESS_FRACTION and max(self.value, self.upper or 0) > 1:
            raise ValueError("access fraction must be in [0, 1]")
        return self


class Evidence(str, Enum):
    MODELLED = "modelled"
    MEASURED = "measured"
    SYNTHETIC = "synthetic_fixture"
    UNAVAILABLE = "unavailable"


class Evaluation(FrozenModel):
    inputs_fingerprint: str
    plan_fingerprint: str
    evaluator_id: str = Field(min_length=1)
    evidence: Evidence
    provenance: str = Field(min_length=1)
    metrics: tuple[MetricValue, ...] = ()
    warnings: tuple[str, ...] = ()
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def coherent(self) -> Evaluation:
        if len({m.metric for m in self.metrics}) != len(self.metrics):
            raise ValueError("duplicate metrics")
        if self.evidence == Evidence.UNAVAILABLE:
            if self.metrics or not self.unavailable_reason:
                raise ValueError("unavailable evaluations require a reason and no metrics")
        return self


class Evaluator(Protocol):
    """Use identical forcing, baseline, settings and seed for every plan.

    Identity includes engine/version/metric implementation. Unsupported choices
    must fail explicitly or return UNAVAILABLE, never approximate visual depths.
    Stochastic engines must honor seed/common random inputs where applicable.
    """
    @property
    def evaluator_id(self) -> str: ...

    def evaluate(self, inputs: ExperimentInputs, plan: Plan) -> Evaluation: ...


class ObjectiveChange(FrozenModel):
    metric: Metric
    unit: str
    direction: str
    status: str
    baseline: float | None = None
    candidate: float | None = None
    delta: float | None = None  # candidate minus baseline, native units
    improvement: float | None = None  # positive always means preferred direction
    improvement_lower: float | None = None
    improvement_upper: float | None = None
    reason: str | None = None


class PlanResult(FrozenModel):
    plan: Plan
    assumed_cost: Decimal
    feasible: bool
    evaluation: Evaluation | None = None
    objectives: tuple[ObjectiveChange, ...] = ()
    reason: str | None = None


class ComparisonReport(FrozenModel):
    inputs: ExperimentInputs
    budget: Budget
    baseline: Evaluation
    results: tuple[PlanResult, ...]
    objectives: tuple[Metric, ...]
    point_estimate_pareto: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        """Audit digest of complete report; rerun equality requires same outputs."""
        return _digest(self.model_dump(mode="json"))


def _changes(baseline: Evaluation, candidate: Evaluation,
             objectives: tuple[Metric, ...]) -> tuple[ObjectiveChange, ...]:
    before = {m.metric: m for m in baseline.metrics}
    after = {m.metric: m for m in candidate.metrics}
    changes = []
    for metric in objectives:
        a, b = before.get(metric), after.get(metric)
        common = dict(metric=metric, unit=metric.unit, direction=metric.direction)
        reason = None
        if a is None or b is None:
            reason = "Metric unavailable in baseline or candidate; no benefit inferred."
        elif baseline.evidence != candidate.evidence:
            reason = "Evidence kinds differ; outcomes are not comparable."
        elif (a.definition, a.coverage) != (b.definition, b.coverage):
            reason = "Metric definition or coverage differs; outcomes are not comparable."
        if reason:
            changes.append(ObjectiveChange(**common, status="unavailable", reason=reason))
            continue
        sign = 1 if metric.direction == "maximize" else -1
        delta = b.value - a.value
        low = high = None
        if a.lower is not None and b.lower is not None:
            endpoints = (sign * (b.lower - a.upper), sign * (b.upper - a.lower))
            low, high = min(endpoints), max(endpoints)
        changes.append(ObjectiveChange(
            **common, status=candidate.evidence.value, baseline=a.value,
            candidate=b.value, delta=delta, improvement=sign * delta,
            improvement_lower=low, improvement_upper=high,
        ))
    return tuple(changes)


def compare_plans(*, inputs: ExperimentInputs, plans: tuple[Plan, ...],
                  budget: Budget, evaluator: Evaluator,
                  objectives: tuple[Metric, ...] = tuple(Metric)) -> ComparisonReport:
    """Evaluate feasible supplied plans in stable ID order. No search or scoring.

    Over-budget plans never reach the evaluator. Currency mismatches fail; costs
    use exact decimal addition. Evaluator exceptions propagate (no fake fallback).
    """
    if not objectives or len(set(objectives)) != len(objectives):
        raise ValueError("provide nonempty, unique objectives")
    if len({p.plan_id for p in plans}) != len(plans) or any(p.plan_id == "baseline" for p in plans):
        raise ValueError("plan IDs must be unique and cannot use reserved 'baseline'")
    # Validate costs before running anything; no implicit currency conversion.
    for plan in plans:
        if any(c.cost.currency != budget.currency for c in plan.choices):
            raise ValueError("all costs must use the budget currency")
    evaluator_id = evaluator.evaluator_id

    def run(plan: Plan) -> Evaluation:
        result = evaluator.evaluate(inputs, plan)
        if (result.inputs_fingerprint != inputs.fingerprint
                or result.plan_fingerprint != plan.fingerprint
                or result.evaluator_id != evaluator_id
                or evaluator.evaluator_id != evaluator_id):
            raise ValueError("evaluator returned mismatched inputs, plan or engine identity")
        return result

    baseline = run(BASELINE)
    results = []
    for plan in sorted(plans, key=lambda p: p.plan_id):
        cost = sum((c.cost.amount for c in plan.choices), Decimal(0))
        if cost > budget.amount:
            results.append(PlanResult(plan=plan, assumed_cost=cost, feasible=False,
                                      reason="Caller-supplied assumed cost exceeds budget."))
        else:
            result = run(plan)
            results.append(PlanResult(plan=plan, assumed_cost=cost, feasible=True,
                                      evaluation=result, objectives=_changes(baseline, result, objectives)))
    # Pareto membership is only over complete measured/modelled point estimates.
    # Synthetic fixtures and incomplete plans never earn a performance result.
    eligible = [r for r in results if r.feasible and r.evaluation.evidence in
                (Evidence.MODELLED, Evidence.MEASURED) and
                all(o.improvement is not None for o in r.objectives)]
    front = tuple(r.plan.plan_id for r in eligible
                  if not (all(o.improvement <= 0 for o in r.objectives)
                          and any(o.improvement < 0 for o in r.objectives))
                  and not any(
        all(b.improvement >= a.improvement for a, b in zip(r.objectives, other.objectives))
        and any(b.improvement > a.improvement for a, b in zip(r.objectives, other.objectives))
        for other in eligible if other is not r))
    return ComparisonReport(
        inputs=inputs, budget=budget, baseline=baseline, results=tuple(results),
        objectives=objectives, point_estimate_pareto=front,
        warnings=("Costs are supplied assumptions; no market prices are inferred.",
                  "Pareto membership uses supplied point estimates, includes baseline dominance, excludes cost, and is not a safety or causal claim.",
                  "Uncertainty bounds are conservative differences of supplied ranges, not confidence intervals for effects.",
                  "Missing metrics remain unavailable; model outputs are not Beirut calibration."),
    )


class ChallengeIteration(FrozenModel):
    hypothesis: str = Field(min_length=1)
    report: ComparisonReport
    learning_notes: tuple[str, ...]


class PlanningChallenge(FrozenModel):
    """Immutable educational journal with a fixed experiment and budget.

    Each iteration compares a learner's choices with a freshly evaluated fixed
    baseline. The journal records hypotheses and evidence, with no reward score.
    """
    challenge_id: str = Field(min_length=1)
    inputs: ExperimentInputs
    budget: Budget
    objectives: tuple[Metric, ...] = tuple(Metric)
    iterations: tuple[ChallengeIteration, ...] = ()

    def iterate(self, *, hypothesis: str, plans: tuple[Plan, ...],
                evaluator: Evaluator) -> PlanningChallenge:
        if not hypothesis.strip():
            raise ValueError("record a hypothesis before evaluating")
        report = compare_plans(inputs=self.inputs, plans=plans, budget=self.budget,
                               evaluator=evaluator, objectives=self.objectives)
        if self.iterations:
            # Run IDs/log locations may change; physical results and engine may not.
            exclude = {"provenance", "warnings"}
            original = self.iterations[0].report.baseline.model_dump(exclude=exclude)
            if report.baseline.model_dump(exclude=exclude) != original:
                raise ValueError("baseline changed across iterations; start a new challenge")
        notes = ["Compare the hypothesis with each supplied outcome; no causal benefit is established."]
        for result in report.results:
            if not result.feasible:
                notes.append(f"{result.plan.plan_id}: revise choices to meet the assumed budget.")
            elif any(o.status == "unavailable" for o in result.objectives):
                notes.append(f"{result.plan.plan_id}: obtain missing or comparable metrics before drawing conclusions.")
            elif result.evaluation.evidence == Evidence.SYNTHETIC:
                notes.append(f"{result.plan.plan_id}: synthetic practice only; no demonstrated flood benefit.")
            else:
                signs = [o.improvement for o in result.objectives]
                if any(v > 0 for v in signs) and any(v < 0 for v in signs):
                    notes.append(f"{result.plan.plan_id}: examine the tradeoff between objectives.")
                if any(o.improvement_lower is None for o in result.objectives):
                    notes.append(f"{result.plan.plan_id}: effect uncertainty is not quantified for every objective.")
        iteration = ChallengeIteration(hypothesis=hypothesis, report=report, learning_notes=tuple(notes))
        return self.model_copy(update={"iterations": (*self.iterations, iteration)})
