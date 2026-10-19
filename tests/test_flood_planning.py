"""Synthetic adapter tests; values are fixtures, not Beirut measurements."""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from dynacity.flood_planning import (
    BASELINE, Budget, CostAssumption, Evaluation, Evidence, ExperimentInputs,
    InterventionChoice, InterventionKind, Metric, MetricValue, Parameter, Plan,
    PlanningChallenge, compare_plans,
)


def inputs():
    return ExperimentInputs.from_inputs(
        forcing={"rainfall_id": "synthetic-storm", "duration_s": 3600},
        baseline={"terrain_id": "synthetic-grid", "crs": "local metres"},
        settings={"engine_version": "fixture-1"}, seed=17,
    )


def plan(name="a", cost="10.10", currency="USD"):
    return Plan(plan_id=name, choices=(InterventionChoice(
        choice_id=name, kind=InterventionKind.RETENTION, target_ids=("fixture-cell",),
        parameters=(Parameter(name="capacity", value=10, unit="m3"),),
        cost=CostAssumption(amount=cost, currency=currency, source="test assumption"),
    ),))


def metric(key, value, **kw):
    return MetricValue(metric=key, value=value, definition="fixture threshold/time/aggregation",
                       coverage="fixture inventory v1", uncertainty="not quantified", **kw)


class FixtureEvaluator:
    evaluator_id = "synthetic-test-adapter-v1"

    def __init__(self, values=None, evidence=Evidence.SYNTHETIC):
        self.calls = []
        self.values = values or {
            "baseline": (metric(Metric.EXPOSED_BUILDINGS, 10),
                         metric(Metric.CLOSURE_DURATION, 4), metric(Metric.ACCESS_FRACTION, .5)),
            "a": (metric(Metric.EXPOSED_BUILDINGS, 6),
                  metric(Metric.CLOSURE_DURATION, 2), metric(Metric.ACCESS_FRACTION, .8)),
        }
        self.evidence = evidence

    def evaluate(self, context, candidate):
        self.calls.append((context, candidate))
        return Evaluation(inputs_fingerprint=context.fingerprint,
                          plan_fingerprint=candidate.fingerprint,
                          evaluator_id=self.evaluator_id, evidence=self.evidence,
                          provenance="synthetic fixture, never a physical result",
                          metrics=self.values[candidate.plan_id])


OBJECTIVES = (Metric.EXPOSED_BUILDINGS, Metric.CLOSURE_DURATION, Metric.ACCESS_FRACTION)


def compare(evaluator=None, **kw):
    return compare_plans(inputs=inputs(), plans=(plan(),), budget=Budget(amount="10.10", currency="USD"),
                         evaluator=evaluator or FixtureEvaluator(), objectives=OBJECTIVES, **kw)


def test_exact_budget_boundary_and_no_evaluation_of_infeasible_plans():
    evaluator = FixtureEvaluator()
    result = compare_plans(inputs=inputs(), plans=(plan("over", "10.100001"), plan()),
                           budget=Budget(amount="10.10", currency="USD"), evaluator=evaluator)
    assert [r.plan.plan_id for r in result.results] == ["a", "over"]
    assert result.results[0].feasible
    assert result.results[0].assumed_cost == Decimal("10.10")
    assert not result.results[1].feasible
    assert result.results[1].evaluation is None
    assert result.results[1].objectives == ()
    assert [p.plan_id for _, p in evaluator.calls] == ["baseline", "a"]


def test_decimal_costs_sum_without_binary_float_rounding():
    first, second = plan("first", ".1"), plan("second", ".2")
    combined = Plan(plan_id="a", choices=first.choices + second.choices)
    report = compare_plans(inputs=inputs(), plans=(combined,), budget=Budget(amount=".3", currency="USD"),
                           evaluator=FixtureEvaluator())
    assert report.results[0].feasible
    assert report.results[0].assumed_cost == Decimal(".3")


def test_currency_mismatch_is_rejected_before_any_model_run():
    evaluator = FixtureEvaluator()
    with pytest.raises(ValueError, match="currency"):
        compare_plans(inputs=inputs(), plans=(plan(currency="LBP"),),
                      budget=Budget(amount=100, currency="USD"), evaluator=evaluator)
    assert evaluator.calls == []


def test_objective_directions_and_native_units():
    result = compare().results[0]
    assert [o.delta for o in result.objectives] == [-4, -2, pytest.approx(.3)]
    assert [o.improvement for o in result.objectives] == [4, 2, pytest.approx(.3)]
    assert [o.direction for o in result.objectives] == ["minimize", "minimize", "maximize"]
    assert [o.unit for o in result.objectives] == ["buildings", "h", "fraction"]
    assert all(o.status == "synthetic_fixture" for o in result.objectives)
    assert compare().point_estimate_pareto == ()


def test_missing_metrics_never_become_zero_and_incomplete_plans_excluded():
    evaluator = FixtureEvaluator(evidence=Evidence.MODELLED)
    evaluator.values["a"] = evaluator.values["a"][:1]
    report = compare(evaluator)
    objective = report.results[0].objectives[1]
    assert objective.status == "unavailable"
    assert objective.delta is objective.improvement is objective.candidate is None
    assert report.point_estimate_pareto == ()


@pytest.mark.parametrize("field,value", [("definition", "different depth threshold"),
                                         ("coverage", "different road inventory")])
def test_incompatible_metrics_are_not_compared(field, value):
    evaluator = FixtureEvaluator()
    evaluator.values["a"] = (evaluator.values["a"][0].model_copy(update={field: value}),)
    assert compare(evaluator).results[0].objectives[0].status == "unavailable"


def test_uncertainty_ranges_can_include_harm_despite_better_point_estimate():
    evaluator = FixtureEvaluator()
    evaluator.values["baseline"] = (metric(Metric.EXPOSED_BUILDINGS, 10, lower=8, upper=12),)
    evaluator.values["a"] = (metric(Metric.EXPOSED_BUILDINGS, 9, lower=7, upper=11),)
    change = compare(evaluator).results[0].objectives[0]
    assert change.improvement == 1
    assert (change.improvement_lower, change.improvement_upper) == (-3, 5)


def test_access_uncertainty_has_correct_maximizing_direction():
    evaluator = FixtureEvaluator()
    evaluator.values["baseline"] = (metric(Metric.ACCESS_FRACTION, .4, lower=.3, upper=.5),)
    evaluator.values["a"] = (metric(Metric.ACCESS_FRACTION, .7, lower=.6, upper=.8),)
    change = compare(evaluator).results[0].objectives[2]
    assert change.improvement_lower == pytest.approx(.1)
    assert change.improvement_upper == pytest.approx(.5)


def test_identical_context_seed_and_immutable_inputs_for_every_run():
    evaluator = FixtureEvaluator()
    report = compare(evaluator)
    assert all(context is report.inputs for context, _ in evaluator.calls)
    assert all(context.seed == 17 for context, _ in evaluator.calls)
    with pytest.raises(ValidationError):
        report.inputs.seed = 1


def test_input_and_report_fingerprints_are_reproducible_and_sensitive():
    a = ExperimentInputs.from_inputs(forcing={"b": 1, "a": 2}, baseline={}, settings={})
    b = ExperimentInputs.from_inputs(forcing={"a": 2, "b": 1}, baseline={}, settings={})
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != b.model_copy(update={"seed": 3}).fingerprint
    assert compare().fingerprint == compare().fingerprint
    changed = FixtureEvaluator()
    changed.values["a"] = (metric(Metric.EXPOSED_BUILDINGS, 8),)
    assert compare().fingerprint != compare(changed).fingerprint


@pytest.mark.parametrize("field", ["inputs_fingerprint", "plan_fingerprint", "evaluator_id"])
def test_mismatched_evaluator_echo_is_rejected(field):
    class BadEvaluator(FixtureEvaluator):
        def evaluate(self, context, candidate):
            return super().evaluate(context, candidate).model_copy(update={field: "wrong"})
    with pytest.raises(ValueError, match="mismatched"):
        compare(BadEvaluator())


def test_unavailable_solver_does_not_get_fake_outputs():
    class MissingEvaluator(FixtureEvaluator):
        def evaluate(self, context, candidate):
            return Evaluation(inputs_fingerprint=context.fingerprint,
                              plan_fingerprint=candidate.fingerprint, evaluator_id=self.evaluator_id,
                              evidence=Evidence.UNAVAILABLE, provenance="adapter configuration",
                              unavailable_reason="solver not installed")
    report = compare(MissingEvaluator())
    assert report.baseline.unavailable_reason == "solver not installed"
    assert all(o.improvement is None for o in report.results[0].objectives)
    assert report.point_estimate_pareto == ()


def test_solver_failure_propagates_without_visual_fallback():
    class BrokenEvaluator(FixtureEvaluator):
        def evaluate(self, context, candidate):
            raise RuntimeError("solver unavailable")
    with pytest.raises(RuntimeError, match="solver unavailable"):
        compare(BrokenEvaluator())


def test_pareto_preserves_tradeoffs_and_excludes_dominated_candidate():
    evaluator = FixtureEvaluator(evidence=Evidence.MODELLED)  # exercise modelled branch with fixtures
    evaluator.values["b"] = (metric(Metric.EXPOSED_BUILDINGS, 5),
                              metric(Metric.CLOSURE_DURATION, 3), metric(Metric.ACCESS_FRACTION, .7))
    evaluator.values["c"] = (metric(Metric.EXPOSED_BUILDINGS, 9),
                              metric(Metric.CLOSURE_DURATION, 4), metric(Metric.ACCESS_FRACTION, .5))
    report = compare_plans(inputs=inputs(), plans=(plan("c"), plan("b"), plan()),
                           budget=Budget(amount=20, currency="USD"), evaluator=evaluator,
                           objectives=OBJECTIVES)
    assert report.point_estimate_pareto == ("a", "b")
    again = compare_plans(inputs=inputs(), plans=(plan(), plan("b"), plan("c")),
                         budget=report.budget, evaluator=evaluator, objectives=OBJECTIVES)
    assert report.fingerprint == again.fingerprint


def test_challenge_records_hypothesis_synthetic_limits_and_missing_metrics():
    challenge = PlanningChallenge(challenge_id="practice", inputs=inputs(),
                                  budget=Budget(amount=20, currency="USD"), objectives=OBJECTIVES)
    next_round = challenge.iterate(hypothesis="Test retention", plans=(plan(),), evaluator=FixtureEvaluator())
    assert not challenge.iterations
    assert len(next_round.iterations) == 1
    assert any("synthetic practice" in n for n in next_round.iterations[0].learning_notes)
    evaluator = FixtureEvaluator()
    evaluator.values["a"] = ()
    last = next_round.iterate(hypothesis="Check coverage", plans=(plan(),), evaluator=evaluator)
    assert any("missing or comparable" in n for n in last.iterations[-1].learning_notes)
    assert len(last.iterations) == 2


def test_challenge_rejects_changed_baseline_and_empty_hypothesis():
    challenge = PlanningChallenge(challenge_id="practice", inputs=inputs(),
                                  budget=Budget(amount=20, currency="USD"))
    with pytest.raises(ValueError, match="hypothesis"):
        challenge.iterate(hypothesis="  ", plans=(plan(),), evaluator=FixtureEvaluator())
    challenge = challenge.iterate(hypothesis="First", plans=(plan(),), evaluator=FixtureEvaluator())
    evaluator = FixtureEvaluator()
    evaluator.values["baseline"] = ()
    with pytest.raises(ValueError, match="baseline changed"):
        challenge.iterate(hypothesis="Second", plans=(plan(),), evaluator=evaluator)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_metric_values_rejected(value):
    with pytest.raises(ValidationError):
        metric(Metric.EXPOSED_BUILDINGS, value)


def test_invalid_bounds_fraction_and_illustrative_evidence_rejected():
    for kw in ({"lower": 1}, {"lower": 3, "upper": 4}):
        with pytest.raises(ValidationError):
            metric(Metric.EXPOSED_BUILDINGS, 2, **kw)
    with pytest.raises(ValidationError):
        metric(Metric.ACCESS_FRACTION, 1.1)
    with pytest.raises(ValidationError):
        Evaluation(inputs_fingerprint="x", plan_fingerprint="x", evaluator_id="visual",
                   evidence="illustrative", provenance="water slider")


def test_duplicate_and_reserved_plan_ids_and_objectives_rejected():
    for plans in ((plan(), plan()), (BASELINE,)):
        with pytest.raises(ValueError, match="plan IDs"):
            compare_plans(inputs=inputs(), plans=plans, budget=Budget(amount=20, currency="USD"),
                          evaluator=FixtureEvaluator())
    for objectives in ((), (Metric.EXPOSED_AREA, Metric.EXPOSED_AREA)):
        with pytest.raises(ValueError, match="objectives"):
            compare_plans(inputs=inputs(), plans=(plan(),), budget=Budget(amount=20, currency="USD"),
                          evaluator=FixtureEvaluator(), objectives=objectives)


def test_baseline_dominates_harmful_plans_even_when_no_other_plan_is_better():
    evaluator = FixtureEvaluator(evidence=Evidence.MODELLED)
    evaluator.values["a"] = (metric(Metric.EXPOSED_BUILDINGS, 11),
                              metric(Metric.CLOSURE_DURATION, 5), metric(Metric.ACCESS_FRACTION, .4))
    assert compare(evaluator).point_estimate_pareto == ()


def test_mixed_evidence_does_not_establish_a_comparable_effect():
    class MixedEvaluator(FixtureEvaluator):
        def evaluate(self, context, candidate):
            result = super().evaluate(context, candidate)
            return result.model_copy(update={"evidence": Evidence.MEASURED if candidate == BASELINE
                                             else Evidence.MODELLED})
    report = compare(MixedEvaluator())
    assert all(o.status == "unavailable" for o in report.results[0].objectives)
    assert report.point_estimate_pareto == ()


def test_challenge_reports_model_tradeoffs_and_unquantified_uncertainty():
    evaluator = FixtureEvaluator(evidence=Evidence.MODELLED)
    evaluator.values["a"] = (metric(Metric.EXPOSED_BUILDINGS, 6),
                              metric(Metric.CLOSURE_DURATION, 5), metric(Metric.ACCESS_FRACTION, .8))
    challenge = PlanningChallenge(challenge_id="tradeoffs", inputs=inputs(), objectives=OBJECTIVES,
                                  budget=Budget(amount=20, currency="USD"))
    result = challenge.iterate(hypothesis="Exposure may fall but closures may lengthen",
                               plans=(plan(),), evaluator=evaluator)
    notes = result.iterations[0].learning_notes
    assert any("tradeoff" in n for n in notes)
    assert any("not quantified" in n for n in notes)


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity"])
def test_invalid_cost_assumptions_rejected(value):
    with pytest.raises(ValidationError):
        CostAssumption(amount=value, currency="USD", source="caller")


def test_manifest_mutation_does_not_change_fixed_inputs():
    forcing = {"rainfall": [1, 2]}
    context = ExperimentInputs.from_inputs(forcing=forcing, baseline={}, settings={})
    original = context.fingerprint
    forcing["rainfall"].append(3)
    assert context.fingerprint == original


def test_json_handoff_roundtrip_preserves_audit_digest():
    from dynacity.flood_planning import ComparisonReport
    report = compare()
    restored = ComparisonReport.model_validate_json(report.model_dump_json())
    assert restored == report
    assert restored.fingerprint == report.fingerprint


def test_challenge_accepts_new_run_provenance_when_baseline_outputs_stay_fixed():
    class RunLogEvaluator(FixtureEvaluator):
        def evaluate(self, context, candidate):
            return super().evaluate(context, candidate).model_copy(update={"provenance": "new run log"})
    challenge = PlanningChallenge(challenge_id="rerun", inputs=inputs(), objectives=OBJECTIVES,
                                  budget=Budget(amount=20, currency="USD"))
    first = challenge.iterate(hypothesis="First run", plans=(plan(),), evaluator=FixtureEvaluator())
    second = first.iterate(hypothesis="Repeat run", plans=(plan(),), evaluator=RunLogEvaluator())
    assert len(second.iterations) == 2
    assert first.iterations[0].report.fingerprint != second.iterations[1].report.fingerprint
