"""The 2D street-planning table: its road list and its routes."""
import pytest
from fastapi.testclient import TestClient

from dynacity.planner import planner_page, planner_roads
from dynacity.service import create_app
from dynacity.traffic import TrafficModel, build_traffic_model, lane_counts

from test_models_engine_api import engine_fixture
from test_traffic import bus_overpass, overpass, snapshot, write_velocities


@pytest.fixture
def model(tmp_path) -> TrafficModel:
    csv = tmp_path / "velocities.csv"
    write_velocities(csv)
    return build_traffic_model(snapshot(), csv, overpass=overpass(), bus_overpass=bus_overpass())


def test_road_list_uses_road_indices_and_carries_only_planning_fields(model):
    roads = planner_roads(model)
    features = roads["features"]
    assert roads["type"] == "FeatureCollection" and len(features) == len(model)
    lanes = lane_counts(model)
    for i, feature in enumerate(features):
        # The page sends these ids straight to the planning API.
        assert feature["id"] == i
        props = feature["properties"]
        assert set(props) <= {"name", "cls", "lanes", "oneway", "free", "lvl"}
        assert props["lanes"] == int(lanes[i]) and props["cls"] == model.highway[i]
        coords = feature["geometry"]["coordinates"]
        assert len(coords) == len(model.geometry[i])
        # Longitude/latitude around Beirut, not UTM metres.
        assert all(35 < lon < 36 and 33 < lat < 35 for lon, lat in coords)


def test_planner_routes_serve_the_page_and_roads(model):
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot(), viewer_traffic=model))
    page = client.get("/planner")
    assert page.status_code == 200 and "<title>Beirut Street Planner</title>" in page.text
    roads = client.get("/v1/planner/roads").json()
    assert len(roads["features"]) == len(model)
    # The page drives the existing SUMO routes.
    assert "/v1/viewer/traffic/simulation" in page.text


def test_planner_needs_a_traffic_model():
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot()))
    assert client.get("/planner").status_code == 404
    assert client.get("/v1/planner/roads").status_code == 404


def test_page_keeps_typewriter_fonts_out():
    # Repository preference: no Courier or typewriter-style monospace labels.
    assert "courier" not in planner_page().lower()


def test_page_loads_right_to_left_text_before_the_map():
    # Without MapLibre's RTL plugin, Arabic map labels render backwards.
    page = planner_page()
    assert "setRTLTextPlugin(" in page and "mapbox-gl-rtl-text@" in page
    assert page.index("setRTLTextPlugin(") < page.index("new maplibregl.Map(")


def test_viewer_opens_the_planner_in_place_from_its_rail():
    # The 3D page carries a 2D button and the hand-over code, and opens the planner
    # embedded at its own centre and zoom (the viewer's hash keys).
    from dynacity.viewer import render_viewer
    page = render_viewer({})
    assert 'id="t-plan"' in page and "function wirePlan2d" in page and "wirePlan2d();" in page
    assert "/planner?embed=1#lon=" in page
    # Each frame's layers pass through the flattening step.
    assert "plan2dFlatten([...baseLayers" in page
    # Only a served page with traffic has a planner to open.
    assert "DATA.api&&DATA.traffic" in page


def test_planner_lines_up_with_the_viewer_and_talks_only_to_its_own_origin():
    page = planner_page()
    # The embed flag is set before the stylesheet applies, so nothing flashes.
    assert page.index("classList.add('embed')") < page.index("<style>")
    # Full-bleed map: the same centre and zoom give the same scale as the full-screen 3D view.
    assert "#map { position: absolute; inset: 0; }" in page
    assert "p.get('lon')" in page and "p.get('lat')" in page and "p.get('zoom')" in page
    # Messages go to and come from this origin only.
    import re
    posts = re.findall(r"postMessage\((.*?)\);", page)
    assert posts and all(p.endswith("location.origin") for p in posts)
    assert "e.origin !== location.origin" in page
    # Standalone, "3D city" opens the viewer here and lets the city rise.
    assert "location.href = `/#${cameraHash()}&pitch=0&bearing=0&rise=1`" in page


def test_both_pages_can_hide_their_interface_and_share_that_choice():
    from dynacity.viewer import render_viewer
    viewer, planner = render_viewer({}), planner_page()
    # A rail button and H hide the panels; one button stays to bring them back.
    assert 'id="t-bare"' in viewer and 'id="unbare"' in viewer and "body.bare #unbare" in viewer
    # The credits line the basemap licence needs stays visible.
    assert "body.bare .bottom > :not(.attrib)" in viewer
    assert 'id="bare"' in planner and ".bare #panel" in planner
    # The choice travels into the planner and back out with it.
    assert "bare:bareView()" in viewer and "typeof m.bare === 'boolean'" in planner
    assert "bare: root.classList.contains('bare')" in planner
