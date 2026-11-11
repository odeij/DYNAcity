import numpy as np
import pytest
from fastapi.testclient import TestClient

from dynacity.contracts import TransitionAdjustment
from dynacity.planning import (
    KpiObjective,
    KpiTarget,
    LeverOption,
    PlanningRequest,
    _pareto_mask,
    plan,
)
from dynacity.service import create_app
from dynacity.status import CanonicalState

from test_models_engine_api import engine_fixture, request_fixture


def lever(intervention_id, target, deltas=(1.0, 2.0)) -> LeverOption:
    return LeverOption(
        lever=TransitionAdjustment(
            intervention_id=intervention_id,
            eligible_from_states=[CanonicalState.ACTIVE_CONSTRUCTION],
            target_state=target,
            log_odds_delta=1.0,
            effect_source="synthetic test assumption",
        ),
        deltas=list(deltas),
        steps=[1, 2],
    )


def planning_request(**overrides) -> PlanningRequest:
    fields = {
        "snapshot": request_fixture().snapshot,
        "horizon_years": 4,
        "levers": [
            lever("finish", CanonicalState.STABLE_BUILT),
            lever("stall", CanonicalState.STALLED_OR_CANCELLED),
        ],
        "objectives": [KpiObjective(kpi="completed_count", year_offset=4, direction="maximize")],
        "draws": 50,
        "seed": 3,
    } | overrides
    return PlanningRequest(**fields)


def test_exhaustive_search_covers_space_and_includes_bau():
    result = plan(engine_fixture(), planning_request())
    # Each lever: off + 2 deltas × 2 steps = 5 settings → 25 combinations.
    assert result.search == "exhaustive"
    assert result.space_size == result.evaluated == 25
    assert result.candidates[0].candidate_id == "bau" and result.candidates[0].intensity == 0
    assert result.evidence_level == "assumption_based_scenario"
    assert result.pareto_front and "bau" in result.pareto_front  # zero intensity is never dominated


def test_backcast_returns_least_intensive_combination_meeting_target():
    request = planning_request(
        targets=[KpiTarget(kpi="completed_count", year_offset=4, operator=">=", value=1)],
    )
    result = plan(engine_fixture(), request)
    by_id = {c.candidate_id: c for c in result.candidates}
    chosen = [by_id[i] for i in result.backcast]
    assert chosen and all(c.meets_targets for c in chosen)
    feasible = [c for c in result.candidates if c.meets_targets]
    assert chosen[0].intensity == min(c.intensity for c in feasible)


def test_unreachable_target_reports_closest_with_warning():
    request = planning_request(
        targets=[KpiTarget(kpi="completed_count", year_offset=4, operator=">=", value=99)],
    )
    result = plan(engine_fixture(), request)
    assert not any(c.meets_targets for c in result.candidates)
    assert result.backcast
    assert any("none meets every target" in w for w in result.warnings)


def test_sampled_search_is_seeded_and_keeps_single_lever_baselines():
    request = planning_request(max_evaluations=12)
    first, second = plan(engine_fixture(), request), plan(engine_fixture(), request)
    assert first.search == "sampled" and first.evaluated == 12
    assert [c.settings for c in first.candidates] == [c.settings for c in second.candidates]
    singles = [c for c in first.candidates if len(c.settings) == 1]
    assert len(singles) == 8  # every single-lever setting is always evaluated


def test_max_active_levers_limits_combinations():
    result = plan(engine_fixture(), planning_request(max_active_levers=1))
    assert all(len(c.settings) <= 1 for c in result.candidates)
    assert result.evaluated == 9


def test_pareto_mask_minimises_every_column():
    scores = np.array([[1.0, 5.0], [2.0, 2.0], [3.0, 3.0], [5.0, 1.0]])
    assert _pareto_mask(scores).tolist() == [True, True, False, True]


def test_request_validation():
    with pytest.raises(ValueError):
        planning_request(objectives=[], targets=[])
    with pytest.raises(ValueError):
        planning_request(objectives=[KpiObjective(kpi="completed_count", year_offset=6, direction="maximize")])
    with pytest.raises(ValueError):
        planning_request(objectives=[KpiObjective(kpi="rent_index", year_offset=2, direction="maximize")])


def test_plan_endpoint():
    client = TestClient(create_app(engine_fixture()))
    response = client.post("/v1/plan", json=planning_request(max_active_levers=1).model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["evaluated"] == 9
