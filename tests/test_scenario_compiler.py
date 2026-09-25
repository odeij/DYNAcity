from datetime import date
from types import SimpleNamespace

from fastapi.testclient import TestClient

from dynacity.contracts import ForecastRequest, UrbanObjectState, UrbanStateSnapshot
from dynacity.scenario_compiler import (
    EFFECT_STRENGTH_LOG_ODDS,
    UNSOURCED,
    DraftIntervention,
    ObjectSelector,
    ScenarioDraft,
    check_draft,
    compile_scenario,
    resolve_draft,
)
from dynacity.service import create_app
from dynacity.status import CanonicalState

from test_models_engine_api import engine_fixture

REQUEST = (
    "Under Law 194/2020, push stalled buildings in Mar Mikhael toward renovation "
    "over the next six years."
)


def snapshot() -> UrbanStateSnapshot:
    def building(object_id, state, sector, use="Residential"):
        return UrbanObjectState(
            object_id=object_id,
            state=state,
            sector=sector,
            building_use=use,
            footprint_area_m2=150,
            floors=4,
            height_m=14,
            features={"lidar_available": 1, "lidar_roof_height_p90_m": 17.25},
        )

    return UrbanStateSnapshot(
        snapshot_id="zone",
        as_of=date(2024, 4, 30),
        data_version="synthetic",
        objects=[
            building("1", CanonicalState.STALLED_OR_CANCELLED, "Mar Mikhael"),
            building("2", CanonicalState.STALLED_OR_CANCELLED, "Mar Mikhael"),
            building("3", CanonicalState.STABLE_BUILT, "Mar Mikhael"),
            building("4", CanonicalState.STALLED_OR_CANCELLED, "Gemmayzeh"),
            building("5", CanonicalState.ACTIVE_CONSTRUCTION, "Gemmayzeh", use="Commercial"),
        ],
    )


def draft(**lever_overrides) -> ScenarioDraft:
    lever = {
        "intervention_id": "heritage-grant",
        "description": "Law 194 support for stalled buildings",
        "selector": ObjectSelector(sectors=["Mar Mikhael"]),
        "eligible_from_states": [CanonicalState.STALLED_OR_CANCELLED],
        "target_state": CanonicalState.RENOVATED,
        "effect_strength": "moderate",
        "effect_source": "Law 194/2020",
    } | lever_overrides
    return ScenarioDraft(
        scenario_id="heritage-grant", horizon_years=6, interventions=[DraftIntervention(**lever)]
    )


class FakeClient:
    """Stands in for anthropic.Anthropic: returns queued drafts, records prompts."""

    def __init__(self, *outputs, stop_reason="end_turn"):
        self.outputs = list(outputs)
        self.calls = []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=self.outputs.pop(0))


def codes(report):
    return {issue.code for issue in report.issues}


def test_valid_draft_resolves_scope_and_effect_size_in_code():
    report = check_draft(draft(), snapshot(), REQUEST)
    assert report.ok, report.issues
    spec = resolve_draft(draft(), snapshot())
    lever = spec.interventions[0]
    # Sector AND eligible state: building 3 (stable) and 4 (other sector) are excluded.
    assert lever.object_ids == ["1", "2"]
    assert lever.log_odds_delta == EFFECT_STRENGTH_LOG_ODDS["moderate"]
    assert spec.baseline_snapshot_id == "zone"


def test_compiled_scenario_runs_through_engine_as_assumption_based():
    spec = resolve_draft(draft(), snapshot())
    result = engine_fixture().forecast(ForecastRequest(snapshot=snapshot(), scenario=spec, draws=50))
    assert result.evidence_level == "assumption_based_scenario"


def test_source_not_in_request_is_rejected_as_invented_citation():
    report = check_draft(draft(effect_source="World Bank RDNA 2020"), snapshot(), REQUEST)
    assert "effect.source_not_in_request" in codes(report)
    assert not report.ok


def test_unsourced_lever_is_allowed_but_forced_to_low_confidence():
    bold = draft(effect_source=UNSOURCED, confidence_grade="high")
    report = check_draft(bold, snapshot(), "Push stalled Mar Mikhael buildings toward renovation.")
    assert report.ok
    assert "effect.unsourced" in codes(report)
    assert resolve_draft(bold, snapshot()).interventions[0].confidence_grade == "low"


def test_unknown_sector_lists_valid_choices_for_repair():
    report = check_draft(draft(selector=ObjectSelector(sectors=["Achrafieh"])), snapshot(), REQUEST)
    hard = {issue.code: issue.message for issue in report.hard()}
    assert "selector.unknown_sector" in hard
    assert "Gemmayzeh" in hard["selector.unknown_sector"]
    assert "selector.empty_scope" in hard


def test_explicit_delta_must_be_stated_by_planner():
    invented = check_draft(draft(explicit_log_odds_delta=3.0), snapshot(), REQUEST)
    assert "effect.delta_not_in_request" in codes(invented)
    stated = REQUEST + " Assume a log-odds shift of 1.5."
    assert check_draft(draft(explicit_log_odds_delta=1.5), snapshot(), stated).ok
    assert resolve_draft(draft(explicit_log_odds_delta=1.5), snapshot()).interventions[0].log_odds_delta == 1.5


def test_step_outside_horizon_and_unknown_kpi_are_hard():
    bad = draft(active_step=3)
    bad.horizon_years = 2
    bad.requested_kpis = ["rent_index"]
    report = check_draft(bad, snapshot(), REQUEST)
    assert {"intervention.step_out_of_horizon", "kpi.unknown"} <= codes(report)


def test_repair_round_feeds_issues_back_and_keeps_audit_trail():
    client = FakeClient(draft(effect_source="UN-Habitat 2021"), draft())
    result = compile_scenario(REQUEST, snapshot(), client=client)
    assert result.ok
    assert [attempt.report.ok for attempt in result.attempts] == [False, True]
    repair_prompt = client.calls[1]["messages"][0]["content"]
    assert "effect.source_not_in_request" in repair_prompt
    assert "UN-Habitat 2021" in repair_prompt


def test_compile_gives_up_after_max_repairs():
    bad = draft(selector=ObjectSelector(sectors=["Hamra"]))
    result = compile_scenario(REQUEST, snapshot(), client=FakeClient(bad, bad))
    assert not result.ok
    assert len(result.attempts) == 2


def test_refusal_is_reported_not_raised():
    result = compile_scenario(REQUEST, snapshot(), client=FakeClient(None, stop_reason="refusal"))
    assert not result.ok
    assert codes(result.attempts[0].report) == {"model.refusal"}


def test_prompt_contains_aggregates_only_never_object_features():
    client = FakeClient(draft())
    compile_scenario(REQUEST, snapshot(), client=client)
    call = client.calls[0]
    prompt = call["system"] + call["messages"][0]["content"]
    assert "Mar Mikhael" in prompt
    assert "lidar" not in prompt and "17.25" not in prompt
    assert call["fallbacks"] == "default"


def test_http_compile_endpoint_returns_audit_trail():
    app = create_app(engine_fixture(), scenario_client=FakeClient(draft()))
    response = TestClient(app).post(
        "/v1/scenarios/compile",
        json={"snapshot": snapshot().model_dump(mode="json"), "text": REQUEST},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scenario"]["interventions"][0]["object_ids"] == ["1", "2"]


def test_citywide_lever_needs_an_explicit_citywide_request():
    # Live finding: an unknown place (Karantina) was silently widened to the city.
    wide = draft(selector=ObjectSelector(), effect_source=UNSOURCED)
    rejected = check_draft(wide, snapshot(), "A demolition wave hits Karantina after a new decree.")
    assert "selector.citywide_not_requested" in {i.code for i in rejected.hard()}
    for text in ("Speed up renovation across Beirut.", "Encourager la rénovation dans toute la ville.",
                 "دعم الترميم في كل بيروت"):
        report = check_draft(wide.model_copy(deep=True), snapshot(), text)
        assert "selector.citywide" in codes(report) and report.ok


def test_use_filter_that_narrows_scope_is_flagged():
    narrowed = draft(selector=ObjectSelector(sectors=["Gemmayzeh"], building_uses=["Commercial"]),
                     eligible_from_states=["stalled_or_cancelled", "active_construction"])
    report = check_draft(narrowed, snapshot(), REQUEST)
    assert "selector.use_filter_narrows" in codes(report)
