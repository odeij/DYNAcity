import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from dynacity.cli import main
from dynacity.contracts import ForecastRequest, ScenarioSpec
from dynacity.service import create_app
from dynacity.snapshot import snapshot_from_bbed
from dynacity.viewer import build_viewer_payload, render_viewer, utm_to_lonlat

from test_models_engine_api import engine_fixture
from test_scenario_compiler import FakeClient, draft


def snapshot_for(collection):
    return snapshot_from_bbed(collection, snapshot_id="zone", as_of=date(2024, 4, 30), data_version="synthetic")


def bau(snapshot):
    return engine_fixture().forecast(ForecastRequest(
        snapshot=snapshot,
        scenario=ScenarioSpec(scenario_id="bau", baseline_snapshot_id=snapshot.snapshot_id, horizon_years=4),
        draws=50,
    ))


def test_utm_inverse_matches_reference_projection():
    # Reference values from pyproj (EPSG:32636 → EPSG:4326) around Beirut.
    for (x, y), (lon, lat) in {
        (732346.89, 3752967.64): (35.5125, 33.8915),
        (733799.56, 3754113.14): (35.5285, 33.9015),
        (820000.0, 3800000.0): (36.4763153, 34.2919409),
    }.items():
        got = utm_to_lonlat(x, y, 36)
        assert got == pytest.approx((lon, lat), abs=1e-6)


def test_payload_joins_geometry_to_states_in_lonlat(bbed_collection):
    snapshot = snapshot_for(bbed_collection)
    payload = build_viewer_payload(bbed_collection, snapshot, bau(snapshot))
    assert payload["coverage"] == {"drawn": 2, "missing_geometry": 0, "estimated_height": 0}
    first = payload["buildings"][0]
    assert first["id"] == snapshot.objects[0].object_id and first["state"] == "stable_built"
    assert first["rings"][0][0] == pytest.approx(list(utm_to_lonlat(0.0, 0.0, 36)), abs=1e-7)
    (west, south), (east, north) = payload["bounds"]
    assert west < east and south < north
    assert payload["basemap"]["context_tiles"].endswith(".mvt")
    assert set(payload["forecast"]["p"]) == {o.object_id for o in snapshot.objects}


def test_basemap_can_be_disabled_and_crs_must_be_utm(bbed_collection):
    snapshot = snapshot_for(bbed_collection)
    assert build_viewer_payload(bbed_collection, snapshot, basemap="none")["basemap"] is None
    with pytest.raises(ValueError):
        build_viewer_payload(bbed_collection, snapshot, basemap="google")
    with pytest.raises(ValueError):
        build_viewer_payload(bbed_collection, snapshot.model_copy(update={"crs": "EPSG:3857"}))


def test_payload_never_embeds_object_features(bbed_collection):
    snapshot = snapshot_for(bbed_collection)
    snapshot.objects[0].features["lidar_roof_height_p90_m"] = 31.337
    html = render_viewer(build_viewer_payload(bbed_collection, snapshot))
    assert "31.337" not in html and "lidar_" not in html


def test_missing_geometry_and_estimated_heights_are_counted(bbed_collection):
    bbed_collection["features"][1]["geometry"] = None
    bbed_collection["features"][0]["properties"]["Building_Hight_m"] = None
    payload = build_viewer_payload(bbed_collection, snapshot_for(bbed_collection))
    assert payload["coverage"] == {"drawn": 1, "missing_geometry": 1, "estimated_height": 1}
    assert payload["buildings"][0]["est"] is True and payload["buildings"][0]["h"] == pytest.approx(4 * 3.2 + 3.2)


def test_forecast_for_other_snapshot_is_rejected(bbed_collection):
    snapshot = snapshot_for(bbed_collection)
    other = bau(snapshot).model_copy(update={"baseline_snapshot_id": "elsewhere"})
    with pytest.raises(ValueError):
        build_viewer_payload(bbed_collection, snapshot, other)


def test_rendered_page_cannot_be_closed_by_data(bbed_collection):
    bbed_collection["features"][0]["properties"]["Sector"] = "</script><script>alert(1)</script>"
    html = render_viewer(build_viewer_payload(bbed_collection, snapshot_for(bbed_collection)))
    assert "</script><script>alert(1)" not in html
    assert "__DYNACITY_TITLE__" not in html and "/*__DYNACITY_DATA__*/null" not in html


def test_served_viewer_runs_compile_and_forecast_loop(bbed_collection):
    snapshot = snapshot_for(bbed_collection)
    html = render_viewer(build_viewer_payload(bbed_collection, snapshot, bau(snapshot), api=True))
    lever = draft(
        selector={"sectors": ["Test Sector"]},
        eligible_from_states=["stable_built"],
        effect_source="unsourced planner assumption",
    )
    client = TestClient(create_app(
        engine_fixture(), scenario_client=FakeClient(lever), viewer_html=html, viewer_snapshot=snapshot
    ))
    assert '"api":true' in client.get("/").text
    body = client.post("/v1/viewer/scenario", json={"text": "renovate the test sector"}).json()
    assert body["compilation"]["scenario"]["interventions"][0]["object_ids"]
    assert body["forecast"]["evidence_level"] == "assumption_based_scenario"


def test_viewer_routes_absent_without_viewer_mode():
    client = TestClient(create_app(engine_fixture()))
    assert client.get("/").status_code == 404
    assert client.post("/v1/viewer/scenario", json={"text": "x"}).status_code == 404


def test_export_viewer_cli(tmp_path, bbed_collection):
    bbed = tmp_path / "bbed.geojson"
    bbed.write_text(json.dumps(bbed_collection))
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(snapshot_for(bbed_collection).model_dump_json())
    output = tmp_path / "out" / "viewer.html"
    main(["export-viewer", "--bbed", str(bbed), "--snapshot", str(snapshot), "--output", str(output)])
    page = output.read_text()
    assert "deck.gl@9.4.0" in page and "maplibre-gl@5.24.0" in page
