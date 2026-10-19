"""End-to-end contracts with explicit test providers, never a live LLM substitute."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dynacity.contracts import ForecastRequest, ScenarioSpec
from dynacity.providers import REFUSED
from dynacity.scenario_compiler import ScenarioDraft, compile_scenario, resolve_draft
from dynacity.service import create_app

from test_models_engine_api import engine_fixture
from test_scenario_compiler import REQUEST, FakeClient, draft, snapshot


@pytest.mark.parametrize('text',['run flood','simulate flooding here','flood this area','فيضان'])
def test_flood_prompt_refused_before_model_or_budget_spend(text):
    from dynacity.limits import RequestBudget
    budget=RequestBudget(total=1)
    class NeverCall:
        def draft(self,*args,**kwargs):raise AssertionError('Flood must not call the language model')
    client=TestClient(create_app(engine_fixture(),viewer_snapshot=snapshot(),
                                 scenario_provider=NeverCall(),llm_budget=budget))
    response=client.post('/v1/viewer/scenario',json={'text':text})
    assert response.status_code==422
    assert 'Flood simulation is not running' in response.json()['detail']
    assert budget.take() is None


def test_compile_forecast_compare_matches_all_rollout_settings():
    engine = engine_fixture()
    city = snapshot()
    scenario = resolve_draft(draft().model_copy(update={"requested_kpis": ["completed_count"]}), city)
    client = TestClient(create_app(engine, scenario_client=FakeClient(draft())))
    compiled = client.post("/v1/scenarios/compile", json={"snapshot": city.model_dump(mode="json"), "text": REQUEST})
    assert compiled.json()["status"] == "accepted"
    request = ForecastRequest(snapshot=city, scenario=scenario, draws=50, seed=123)
    response = client.post("/v1/forecast/compare", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    expected = engine.forecast(request.model_copy(update={"scenario": ScenarioSpec(
        scenario_id="business-as-usual", baseline_snapshot_id=city.snapshot_id,
        horizon_years=6, requested_kpis=["completed_count"],
    )}))
    assert body["baseline"]["kpis"] == [k.model_dump() for k in expected.kpis]
    assert body["baseline"]["evidence_level"] == "empirical_bau"
    assert body["forecast"]["evidence_level"] == "assumption_based_scenario"
    assert body["comparison"]["draws"] == 50 and body["comparison"]["seed"] == 123
    for change, base, scen in zip(body["comparison"]["kpis"], body["baseline"]["kpis"], body["forecast"]["kpis"], strict=True):
        assert change["median_change"] == pytest.approx(scen["p50"] - base["p50"])


def test_viewer_works_without_a_preloaded_baseline_and_with_partial_request():
    partial = draft().model_copy(update={"unsupported_requests": ["Reduce rents by 20%"]})
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot(),
                                  scenario_client=FakeClient(partial), viewer_draws=50))
    body = client.post("/v1/viewer/scenario", json={"text": REQUEST + " Reduce rents by 20%."}).json()
    assert body["compilation"]["status"] == "partial"
    assert len(body["baseline"]["kpis"]) == len(body["forecast"]["kpis"])
    assert body["comparison"]["horizon_years"] == 6
    assert "uncertainty in assumed effect" in body["comparison"]["uncertainty_note"]


def test_unsupported_only_and_ambiguous_empty_drafts_never_run_forecast():
    for unsupported in ([], ["affordable housing prices"]):
        empty = ScenarioDraft(scenario_id="empty", horizon_years=2, unsupported_requests=unsupported)
        client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot(),
                                      scenario_client=FakeClient(empty, empty), viewer_draws=50))
        body = client.post("/v1/viewer/scenario", json={"text": "Make it better"}).json()
        assert body["compilation"]["status"] == "rejected"
        assert body["forecast"] is None and body["baseline"] is None and body["comparison"] is None
        assert any(i["code"] == "request.no_action" for i in body["compilation"]["attempts"][-1]["report"]["issues"])


def test_explicit_bau_request_has_zero_changes():
    empty = ScenarioDraft(scenario_id="bau", horizon_years=2)
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot(),
                                  scenario_client=FakeClient(empty), viewer_draws=50))
    body = client.post("/v1/viewer/scenario", json={"text": "Show business as usual"}).json()
    assert body["compilation"]["status"] == "accepted"
    assert all(k["median_change"] == 0 for k in body["comparison"]["kpis"])


def test_refusal_and_missing_configuration_are_visible_without_substitution(monkeypatch):
    class RefusingProvider:
        name, model = "test-contract", "refusal"
        def propose(self, system, content, output):
            return None, REFUSED

    result = compile_scenario(REQUEST, snapshot(), provider=RefusingProvider())
    assert result.status == "rejected"
    assert result.attempts[0].report.issues[0].code == REFUSED
    def unavailable():
        raise RuntimeError("No key configured")
    monkeypatch.setattr("dynacity.service.resolve_provider", unavailable)
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot()))
    response = client.post("/v1/viewer/scenario", json={"text": REQUEST})
    assert response.status_code == 503
    assert "no language-model provider configured" in response.json()["detail"]


def test_direct_forecasts_reject_inert_or_mistargeted_levers():
    city = snapshot()
    scenario = resolve_draft(draft(), city)
    with pytest.raises(ValidationError, match="beyond the scenario horizon"):
        ScenarioSpec.model_validate({**scenario.model_dump(), "horizon_years": 2,
                                    "interventions": [scenario.interventions[0].model_copy(update={"active_step": 3})]})
    scenario.interventions[0].object_ids = ["invented"]
    with pytest.raises(ValidationError, match="outside the snapshot"):
        ForecastRequest(snapshot=city, scenario=scenario)


def test_anthropic_malformed_output_becomes_validation_feedback():
    from types import SimpleNamespace
    from dynacity.providers import AnthropicProvider, INVALID_OUTPUT

    def malformed(**kwargs):
        return ScenarioDraft.model_validate({"scenario_id": "missing-horizon"})
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=malformed)))
    result = compile_scenario(REQUEST, snapshot(), provider=AnthropicProvider(client))
    assert result.status == "rejected"
    assert result.attempts[0].report.issues[0].code == INVALID_OUTPUT


def test_cli_comparison_artifact_contains_matching_forecast(tmp_path):
    import json
    from dynacity.cli import main

    engine = engine_fixture()
    city = snapshot()
    model = engine.bundle.save(tmp_path / "model.joblib")
    request = ForecastRequest(snapshot=city, scenario=resolve_draft(draft(), city), draws=50)
    request_path = tmp_path / "request.json"
    request_path.write_text(request.model_dump_json())
    output, compared = tmp_path / "forecast.json", tmp_path / "comparison.json"
    main(["forecast", "--model", str(model), "--request", str(request_path),
          "--output", str(output), "--comparison-output", str(compared)])
    result = json.loads(compared.read_text())
    assert result["forecast"] == json.loads(output.read_text())
    assert result["baseline"]["evidence_level"] == "empirical_bau"
