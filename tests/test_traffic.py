from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient

from dynacity.contracts import HazardEvent, HazardKind, UrbanObjectState, UrbanStateSnapshot
from dynacity.service import create_app
from dynacity.traffic import (
    RoadDisruption,
    TrafficModel,
    build_traffic_model,
    road_disruption,
    road_graph,
    traffic_impact,
    traffic_payload,
)
from dynacity.viewer import utm_to_lonlat

from test_models_engine_api import engine_fixture

# A 3×3 grid of junctions 200 m apart in Beirut's UTM zone, plus a spur.
X0, Y0, STEP = 733_000.0, 3_750_000.0, 200.0
CRS = "EPSG:32636"


def node_id(col: int, row: int) -> int:
    return 100 + row * 3 + col


def node_xy(node: int) -> tuple[float, float]:
    if node == 999:  # spur end, east of the middle-right junction
        return X0 + 3 * STEP, Y0 + STEP
    k = node - 100
    return X0 + (k % 3) * STEP, Y0 + (k // 3) * STEP


def way(way_id: int, nodes: list[int], highway: str = "secondary", **tags) -> dict:
    geometry = []
    for n in nodes:
        lon, lat = utm_to_lonlat(*node_xy(n), 36, True)
        geometry.append({"lon": lon, "lat": lat})
    return {"type": "way", "id": way_id, "nodes": nodes, "geometry": geometry, "tags": {"highway": highway, **tags}}


def overpass() -> dict:
    rows = [way(1 + r, [node_id(c, r) for c in range(3)]) for r in range(3)]
    cols = [way(10 + c, [node_id(c, r) for r in range(3)], highway="residential") for c in range(3)]
    spur = [way(20, [node_id(2, 1), 999], highway="tertiary")]
    footpath = [{**way(30, [100, 104]), "tags": {"highway": "footway"}}]
    return {"elements": rows + cols + spur + footpath}


def write_velocities(path) -> None:
    # Same header as the Tari'ak file, stray tab included.
    lines = ['"Date","Time","Coordinate\t (Lon, Lat)","Course","Velocity","OSM ID"']
    for k in range(12):
        lines.append(f'"2016-05-0{1 + k % 5}","08:{k:02d}:00","35.5,33.9",90,10,"way/1"')
        lines.append(f'"2016-05-0{1 + k % 5}","03:{k:02d}:00","35.5,33.9",90,50,"way/1"')
        # Standing still: ignored, or the 3 a.m. median would collapse.
        lines.append(f'"2016-05-0{1 + k % 5}","03:{k:02d}:30","35.5,33.9",90,0,"way/1"')
    lines.append('"2016-05-01","09:00:00","35.5,33.9",90,40,"way/77"')  # outside the network
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def building(object_id: str, x: float, y: float, floors: float = 3.0) -> UrbanObjectState:
    return UrbanObjectState(
        object_id=object_id, state="stable_built", footprint_area_m2=100, floors=floors, height_m=floors * 3.2,
        building_use="Residential", centroid_x_m=x, centroid_y_m=y,
    )


def snapshot() -> UrbanStateSnapshot:
    return UrbanStateSnapshot(
        snapshot_id="grid", as_of=date(2024, 4, 30), data_version="synthetic", crs=CRS,
        objects=[
            # Right beside the middle of the bottom row's first block.
            building("kerb", X0 + 100, Y0 + 8),
            building("block", X0 + 300, Y0 + 300),
        ],
    )


@pytest.fixture
def model(tmp_path) -> TrafficModel:
    csv = tmp_path / "velocities.csv"
    write_velocities(csv)
    return build_traffic_model(snapshot(), csv, overpass=overpass())


def roads_of_way(model: TrafficModel, way_id: int) -> list[int]:
    return [i for i, w in enumerate(model.way_id) if w == way_id]


def test_ways_split_at_shared_nodes_and_footways_dropped():
    graph = road_graph(overpass(), CRS)
    # 3 rows × 2 + 3 columns × 2 + the spur; the footway is not drivable.
    assert len(graph["geometry"]) == 13
    assert 30 not in graph["way_id"]
    assert graph["length_m"] == pytest.approx(np.full(13, STEP), abs=0.5)


def test_hourly_speeds_use_moving_readings_and_fall_back_to_class(model):
    own = roads_of_way(model, 1)
    assert model.speed_kmh[own, 8] == pytest.approx(10)
    assert model.speed_kmh[own, 3] == pytest.approx(50)
    assert not model.estimated[own].any()
    assert model.readings[own[0]] == 24  # the zero-speed readings are dropped
    other = roads_of_way(model, 2)
    assert model.estimated[other].all()


def test_model_round_trips(model, tmp_path):
    model.save(tmp_path / "t.json")
    loaded = TrafficModel.load(tmp_path / "t.json")
    assert loaded.speed_kmh == pytest.approx(model.speed_kmh)
    assert list(loaded.u) == list(model.u) and loaded.highway == model.highway


def test_flood_closes_roads_in_reach_only(model):
    flood = HazardEvent(hazard_id="f", kind=HazardKind.FLOOD, magnitude=1.0,
                        center_x_m=X0, center_y_m=Y0, radius_m=150)
    out = road_disruption([flood], snapshot(), model)
    assert out.closed.any()
    assert {out.reason[i] for i in np.flatnonzero(out.closed)} == {"water"}
    # Nothing at the far corner is touched.
    far = [i for i, g in enumerate(model.geometry) if min(x for x, _ in g) >= X0 + 2 * STEP - 1]
    assert not out.closed[far].any()


def test_rubble_closes_the_street_beside_a_destroyed_building(model):
    tornado = HazardEvent(hazard_id="t", kind=HazardKind.TORNADO, magnitude=5,
                          path_m=[(X0 + 100, Y0 - 50), (X0 + 100, Y0 + 50)], radius_m=30)
    out = road_disruption([tornado], snapshot(), model)
    closed = np.flatnonzero(out.closed)
    assert len(closed) == 1 and out.reason[closed[0]] == "rubble"
    assert model.way_id[closed[0]] == 1


def test_rain_slows_without_closing(model):
    rain = HazardEvent(hazard_id="r", kind=HazardKind.RAIN, citywide=True)
    out = road_disruption([rain], snapshot(), model)
    assert not out.closed.any()
    assert out.speed_factor == pytest.approx(np.full(len(model), 0.85))


def test_impact_counts_cut_off_and_delayed_trips(model):
    untouched = RoadDisruption(closed=np.zeros(len(model), bool), speed_factor=np.ones(len(model)),
                               reason=[None] * len(model))
    calm = traffic_impact(model, untouched, origins=6, per_origin=6)
    assert calm["affected"] == 0 and calm["cut_off"] == 0 and calm["closed"] == []

    everything = RoadDisruption(closed=np.ones(len(model), bool), speed_factor=np.ones(len(model)),
                                reason=["cordon"] * len(model))
    blocked = traffic_impact(model, everything, origins=6, per_origin=6)
    assert blocked["affected"] == blocked["trips"] > 0
    assert blocked["cut_off"] == blocked["trips"]

    slow = RoadDisruption(closed=np.zeros(len(model), bool), speed_factor=np.full(len(model), 0.5),
                          reason=[None] * len(model))
    slowed = traffic_impact(model, slow, origins=6, per_origin=6)
    assert slowed["cut_off"] == 0 and slowed["delay_min"]["p50"] > 0


def test_payload_is_aggregate_and_in_lonlat(model):
    payload = traffic_payload(model)
    road = payload["roads"][0]
    assert set(road) == {"path", "len", "cls", "oneway", "speed", "free", "n", "est"}
    assert len(road["speed"]) == 24
    lon, lat = road["path"][0]
    assert 35 < lon < 36 and 33 < lat < 35
    assert "Tari'ak" in payload["source"]


def test_dropped_hazard_reports_road_impact(model):
    located = snapshot()
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=located, viewer_traffic=model))
    lon, lat = utm_to_lonlat(X0 + 100, Y0, 36, True)
    response = client.post("/v1/viewer/hazard", json={
        "hazards": [{"kind": "flood", "points": [[lon, lat]], "severity": "severe", "radius_m": 150}],
        "hour": 3,
    })
    assert response.status_code == 200
    traffic = response.json()["traffic"]
    assert traffic["hour"] == 3 and traffic["closed_roads"] > 0
    assert traffic["closed_by"]["water"] == traffic["closed_roads"]


def test_dropped_hazard_without_traffic_has_none():
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot()))
    lon, lat = utm_to_lonlat(X0, Y0, 36, True)
    response = client.post("/v1/viewer/hazard", json={"hazards": [{"kind": "explosion", "points": [[lon, lat]]}]})
    assert response.status_code == 200 and response.json()["traffic"] is None


def test_viewer_page_embeds_roads(model, bbed_collection):
    from dynacity.snapshot import snapshot_from_bbed
    from dynacity.viewer import build_viewer_payload, render_viewer

    located = snapshot_from_bbed(bbed_collection, snapshot_id="bbed", as_of=date(2024, 4, 30), data_version="t")
    page = render_viewer(build_viewer_payload(bbed_collection, located, traffic=traffic_payload(model)))
    assert '"schema":"dynacity-roads/1"' in page
    assert 'id="t-traffic"' in page
