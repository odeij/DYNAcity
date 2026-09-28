import io
import json
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from dynacity.contracts import EvidenceLevel, ForecastResult, ObjectTransitionForecast
from dynacity.google_tiles import GoogleTilesProxy, TilesError
from dynacity.limits import RequestBudget
from dynacity.map_query import MapQuery, ask_map, check_query, run_query
from dynacity.providers import UNAVAILABLE
from dynacity.service import create_app
from dynacity.status import CanonicalState as S

from test_models_engine_api import engine_fixture
from test_scenario_compiler import snapshot

QUESTION = "Which stalled buildings in Mar Mikhael are most likely to change?"


def forecast(snap=None) -> ForecastResult:
    snap = snap or snapshot()
    # Building 1 keeps a 40% chance of staying stalled, building 2 a 90% chance.
    stay = {"1": 0.4, "2": 0.9, "3": 0.95, "4": 0.7, "5": 0.5}
    return ForecastResult(
        scenario_id="bau",
        baseline_snapshot_id=snap.snapshot_id,
        model_version="test-markov",
        data_version="synthetic",
        evidence_level=EvidenceLevel.EMPIRICAL_BAU,
        first_step_transitions=[
            ObjectTransitionForecast(
                object_id=item.object_id,
                probabilities={item.state: stay[item.object_id], S.RENOVATED if item.state != S.RENOVATED
                               else S.STABLE_BUILT: 1 - stay[item.object_id]},
            )
            for item in snap.objects
        ],
        kpis=[],
        out_of_distribution_score=0.0,
    )


def query(**overrides) -> MapQuery:
    return MapQuery(**({"kind": "rank", "sectors": ["Mar Mikhael"], "states": [S.STALLED_OR_CANCELLED]} | overrides))


class FakeProvider:
    name, model = "fake", "fake-model"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def propose(self, system, content, output):
        self.calls.append(content)
        item = self.outputs.pop(0)
        return (None, item) if isinstance(item, str) else (item, None)


def codes(report):
    return {issue.code for issue in report.issues}


def test_rank_orders_by_change_probability_computed_from_the_forecast():
    answer = run_query(query(), snapshot(), forecast())
    assert [r.object_id for r in answer.rows] == ["1", "2"]
    assert [round(r.value, 2) for r in answer.rows] == [0.6, 0.1]
    assert answer.highlight_ids == ["1", "2"]
    assert answer.matched == 2
    # The sentence is written by code, with the computed numbers.
    assert answer.text.startswith("2 stalled or cancelled buildings in Mar Mikhael.")
    assert "60%" in answer.text


def test_threshold_must_be_stated_and_is_applied():
    report = check_query(query(threshold=0.5), snapshot(), QUESTION, forecast())
    assert "query.threshold_not_in_question" in codes(report)
    stated = "Which stalled buildings in Mar Mikhael have over 50% chance of changing?"
    assert check_query(query(threshold=0.5), snapshot(), stated, forecast()).ok
    assert [r.object_id for r in run_query(query(threshold=0.5), snapshot(), forecast()).rows] == ["1"]


def test_unknown_sector_and_missing_target_are_hard_with_valid_choices():
    report = check_query(query(sectors=["Karantina"], metric="target_probability"), snapshot(), QUESTION, forecast())
    assert {"query.unknown_sector", "query.target_missing"} <= codes(report)
    unknown = next(i for i in report.issues if i.code == "query.unknown_sector")
    assert "Gemmayzeh" in unknown.message and "Mar Mikhael" in unknown.message


def test_probability_questions_need_a_forecast_but_height_does_not():
    assert "query.no_forecast" in codes(check_query(query(), snapshot(), QUESTION, None))
    assert check_query(query(metric="height_m"), snapshot(), QUESTION, None).ok


def test_count_and_by_sector_answers():
    count = run_query(MapQuery(kind="count", states=[S.STALLED_OR_CANCELLED]), snapshot(), forecast())
    assert count.matched == 3 and set(count.highlight_ids) == {"1", "2", "4"}
    groups = run_query(MapQuery(kind="by_sector", states=[S.STALLED_OR_CANCELLED]), snapshot(), forecast())
    # Mar Mikhael averages (0.6 + 0.1) / 2 = 0.35, Gemmayzeh 0.3.
    assert [(g.sector, g.buildings, round(g.mean_value, 2)) for g in groups.sectors] == [
        ("Mar Mikhael", 2, 0.35), ("Gemmayzeh", 1, 0.3)
    ]


def test_no_match_is_an_answer_not_an_error():
    answer = run_query(query(states=[S.DEMOLISHED]), snapshot(), forecast())
    assert answer.matched == 0 and answer.rows == [] and answer.text.startswith("No demolished buildings")


def test_ask_repairs_once_and_keeps_the_audit_trail():
    provider = FakeProvider(query(sectors=["Karantina"]), query())
    result = ask_map(QUESTION, snapshot(), forecast(), provider=provider)
    assert result.answer is not None and len(result.attempts) == 2
    assert "query.unknown_sector" in codes(result.attempts[0].report)
    assert "Karantina" in provider.calls[1]


def test_ask_reports_model_failure_instead_of_raising():
    result = ask_map(QUESTION, snapshot(), forecast(), provider=FakeProvider(UNAVAILABLE))
    assert result.answer is None and "model.unavailable" in codes(result.attempts[0].report)


def test_prompt_contains_aggregates_only_never_object_rows():
    provider = FakeProvider(query())
    ask_map(QUESTION, snapshot(), forecast(), provider=provider)
    assert "lidar_roof_height_p90_m" not in provider.calls[0]
    assert '"object_id"' not in provider.calls[0]


def test_viewer_ask_endpoint_and_request_budget():
    app = create_app(
        engine_fixture(), scenario_provider=FakeProvider(query(), query()),
        viewer_snapshot=snapshot(), viewer_forecast=forecast(),
        llm_budget=RequestBudget(total=1),
    )
    client = TestClient(app)
    first = client.post("/v1/viewer/ask", json={"question": QUESTION})
    assert first.status_code == 200 and first.json()["answer"]["highlight_ids"] == ["1", "2"]
    second = client.post("/v1/viewer/ask", json={"question": QUESTION})
    assert second.status_code == 429 and "limit of 1" in second.json()["detail"]


def test_ask_route_absent_without_a_viewer_forecast():
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot()))
    assert client.post("/v1/viewer/ask", json={"question": QUESTION}).status_code == 404


def test_budget_per_minute_window_slides():
    now = [0.0]
    budget = RequestBudget(per_minute=2, clock=lambda: now[0])
    assert budget.take() is None and budget.take() is None
    assert "a minute" in budget.take()
    now[0] = 61.0
    assert budget.take() is None


class FakeResponse(io.BytesIO):
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_tiles_proxy_adds_key_server_side_and_drops_browser_key():
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return FakeResponse(b'{"root": {}}')

    proxy = GoogleTilesProxy("secret-key", opener=opener)
    tile = proxy.fetch("v1/3dtiles/datasets/abc/files/x.json", {"session": "s1", "key": "from-browser"})
    assert tile.body == b'{"root": {}}'
    request = seen[0]
    assert request.full_url == "https://tile.googleapis.com/v1/3dtiles/datasets/abc/files/x.json?session=s1"
    assert request.get_header("X-goog-api-key") == "secret-key"


def test_tiles_proxy_refuses_other_paths_missing_key_and_extra_sessions():
    proxy = GoogleTilesProxy("k", sessions=RequestBudget(total=1), opener=lambda r, timeout: FakeResponse(b"{}"))
    for bad in ("v1/other/x", "v1/3dtiles/../../x", "https://evil.example/v1/3dtiles/x"):
        with pytest.raises(TilesError) as exc:
            proxy.fetch(bad, {})
        assert exc.value.status == 404
    proxy.fetch("v1/3dtiles/root.json", {})
    with pytest.raises(TilesError) as exc:
        proxy.fetch("v1/3dtiles/root.json", {})
    assert exc.value.status == 429
    with pytest.raises(TilesError) as exc:
        GoogleTilesProxy("").fetch("v1/3dtiles/root.json", {})
    assert exc.value.status == 503


def test_tiles_endpoint_passes_google_errors_through():
    def opener(request, timeout):
        raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"Map Tiles API has not been used"))

    client = TestClient(create_app(engine_fixture(), tiles_proxy=GoogleTilesProxy("secret-xyz", opener=opener)))
    response = client.get("/v1/3dtiles/root.json")
    assert response.status_code == 403 and "Map Tiles API" in response.json()["detail"]
    assert "secret-xyz" not in json.dumps(response.json())


def test_answer_text_names_what_was_left_out():
    dropped = MapQuery(kind="count", states=[S.STALLED_OR_CANCELLED], unsupported_requests=["Karantina is not in the data"])
    answer = run_query(dropped, snapshot(), forecast())
    assert answer.text.startswith("3 stalled or cancelled buildings across the surveyed area.")
    assert answer.text.endswith("Not answered: Karantina is not in the data.")
