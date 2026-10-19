from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient

from dynacity.contracts import HazardEvent, HazardKind, UrbanObjectState, UrbanStateSnapshot
from dynacity.service import create_app
from dynacity.traffic import (
    COUNT_STATIONS,
    RoadDisruption,
    CRASH_SLOWDOWN,
    class_peak_volumes,
    combine_disruptions,
    estimated_flow,
    match_stations,
    TrafficModel,
    build_traffic_model,
    road_disruption,
    road_events,
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
    # Named like two of the CDR count stations, so volumes can be matched.
    rows[0]["tags"]["name:en"] = "Saëb Salam Boulevard"
    rows[1]["tags"].update({"highway": "trunk", "name": "Corniche Pierre Gemayel", "lanes": "6"})
    rows[2]["tags"].update({"oneway": "yes", "lanes": "3", "name": "Rue Gouraud"})
    cols = [way(10 + c, [node_id(c, r) for r in range(3)], highway="residential") for c in range(3)]
    spur = [way(20, [node_id(2, 1), 999], highway="tertiary")]
    footpath = [{**way(30, [100, 104]), "tags": {"highway": "footway"}}]
    return {"elements": rows + cols + spur + footpath}


def bus_overpass() -> dict:
    # Members only, as the light query returns them: geometry comes from the roads.
    return {"elements": [{
        "type": "relation", "id": 7, "tags": {"route": "bus", "ref": "2", "operator": "LCC"},
        "members": [{"type": "way", "ref": 1, "role": ""}, {"type": "way", "ref": 999999, "role": ""}],
    }]}


def write_velocities(path) -> None:
    # Same header as the Tari'ak file, stray tab included.
    lines = ['"Date","Time","Coordinate\t (Lon, Lat)","Course","Velocity","OSM ID"']
    for k in range(12):
        lines.append(f'"2016-05-0{1 + k % 5}","08:{k:02d}:00","35.5,33.9",90,10,"way/1"')
        lines.append(f'"2016-05-0{1 + k % 5}","03:{k:02d}:00","35.5,33.9",90,50,"way/1"')
        lines.append(f'"2016-05-0{1 + k % 5}","09:{k:02d}:00","35.5,33.9",90,20,"way/1"')
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
    return build_traffic_model(snapshot(), csv, overpass=overpass(), bus_overpass=bus_overpass())


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
    assert model.readings[own[0]] == 36  # the zero-speed readings are dropped
    other = roads_of_way(model, 2)
    assert model.estimated[other].all()


def test_model_round_trips(model, tmp_path):
    model.save(tmp_path / "t.json")
    loaded = TrafficModel.load(tmp_path / "t.json")
    assert loaded.speed_kmh == pytest.approx(model.speed_kmh)
    assert list(loaded.u) == list(model.u) and loaded.highway == model.highway
    assert loaded.name == model.name and loaded.class_hour_readings == model.class_hour_readings


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
    assert set(road) == {"path", "len", "cls", "oneway", "u", "v", "speed", "free", "n", "est", "name", "lanes", "width_m", "qf"}
    assert road["width_m"] > 0
    assert (road["u"], road["v"]) == (int(model.u[0]), int(model.v[0]))
    assert len(road["speed"]) == 24
    lon, lat = road["path"][0]
    assert 35 < lon < 36 and 33 < lat < 35
    assert "Tari'ak" in payload["source"]


def test_illustrative_flood_drop_cannot_invent_road_closures(model):
    located = snapshot()
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=located, viewer_traffic=model))
    lon, lat = utm_to_lonlat(X0 + 100, Y0, 36, True)
    response = client.post("/v1/viewer/hazard", json={
        "hazards": [{"kind": "flood", "points": [[lon, lat]], "severity": "severe", "radius_m": 150}],
        "hour": 3,
    })
    assert response.status_code == 422
    assert "documented hydraulic" in response.json()["detail"]


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


def test_count_stations_match_by_name_and_class(model):
    stations = {st["label"]: st for st in match_stations(model)}
    assert stations["Saeb Salem Road"]["class"] == "secondary"
    assert stations["Saeb Salem Road"]["roads"] == roads_of_way(model, 1)
    assert stations["Charles Helou Road"]["roads"] == []  # not on this map
    both = [st for st in stations.values() if st["label"].startswith("Pierre Gemayel")]
    assert all(st["class"] == "trunk" for st in both)
    assert len(stations) == len(COUNT_STATIONS)


def test_class_volumes_and_daily_flow(model):
    volumes = class_peak_volumes(model)
    assert volumes["secondary"]["vph"] == pytest.approx((2284 + 2378) / 2, abs=1)
    trunk = np.median([(2366 + 1744) / 2, (2852 + 3033) / 2])
    assert volumes["trunk"]["vph"] == pytest.approx(trunk, abs=1)
    assert volumes["primary"]["interpolated"]
    flow = estimated_flow(model)
    own = roads_of_way(model, 1)
    # Readings on secondary roads: 12 at 03:00, 12 at 09:00, 12 at 08:00.
    assert flow[own[0], 9] == pytest.approx(volumes["secondary"]["vph"])
    assert flow[own[0], 3] == pytest.approx(volumes["secondary"]["vph"])
    assert flow[own[0], 12] == 0
    assert np.isnan(flow[roads_of_way(model, 10)[0]]).all()  # residential: not counted


def test_impact_names_the_busiest_closed_counted_road(model):
    closed = np.zeros(len(model), bool)
    closed[roads_of_way(model, 1)[0]] = True
    out = traffic_impact(model, RoadDisruption(closed=closed, speed_factor=np.ones(len(model)),
                                               reason=["cordon" if c else None for c in closed]),
                         hour=9, origins=6, per_origin=6)
    assert out["busiest_closed_vph"] == round(class_peak_volumes(model)["secondary"]["vph"], -1)


def test_hand_closures_and_crashes(model):
    spur = roads_of_way(model, 20)[0]
    crash = roads_of_way(model, 2)[0]
    out = road_events(model, closed=[spur], crashes=[crash])
    assert out.reason[spur] == "manual" and out.reason[crash] == "crash"
    assert out.closed.sum() == 2
    # The queue: roads touching the crash slow down, the far corner does not.
    assert out.speed_factor.min() == pytest.approx(CRASH_SLOWDOWN, abs=0.05)
    assert (out.speed_factor < 1).sum() > 1
    with pytest.raises(ValueError):
        road_events(model, closed=[len(model)])


def test_disruptions_combine(model):
    rain = road_disruption([HazardEvent(hazard_id="r", kind=HazardKind.RAIN, citywide=True)], snapshot(), model)
    spur = roads_of_way(model, 20)[0]
    both = combine_disruptions(rain, road_events(model, closed=[spur]))
    assert both.closed.sum() == 1 and both.reason[spur] == "manual"
    assert both.speed_factor.max() == pytest.approx(0.85)


def test_traffic_endpoint_measures_roads_without_a_forecast(model):
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot(), viewer_traffic=model))
    spur = roads_of_way(model, 20)[0]
    crash = roads_of_way(model, 2)[0]
    body = client.post("/v1/viewer/traffic", json={"closed": [spur], "crashes": [crash], "hour": 3}).json()
    assert body["traffic"]["hour"] == 3
    assert body["traffic"]["closed_by"]["manual"] == 1 and body["traffic"]["closed_by"]["crash"] == 1
    flood = {"hazard_id": "f", "kind": "flood", "magnitude": 1.0, "center_x_m": X0, "center_y_m": Y0, "radius_m": 150}
    with_flood = client.post("/v1/viewer/traffic", json={"hazards": [flood], "closed": [spur]}).json()["traffic"]
    assert with_flood["closed_by"]["water"] > 0 and with_flood["closed_by"]["manual"] == 1
    assert client.post("/v1/viewer/traffic", json={}).json()["traffic"] is None
    assert client.post("/v1/viewer/traffic", json={"closed": [10_000]}).status_code == 422


def test_traffic_endpoint_needs_a_traffic_model():
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot()))
    assert client.post("/v1/viewer/traffic", json={"closed": [0]}).status_code == 404


def test_lanes_come_from_osm_per_direction(model):
    from dynacity.traffic import lanes_of

    lanes = lanes_of(model)
    assert lanes[roads_of_way(model, 2)[0]] == 3       # two-way "6" → 3 each way
    assert lanes[roads_of_way(model, 3)[0]] == 3       # one-way "3"
    assert lanes[roads_of_way(model, 1)[0]] == 2       # untagged secondary: class default
    assert lanes[roads_of_way(model, 10)[0]] == 1      # untagged residential


def test_pm_count_anchors_the_afternoon():
    from dynacity.traffic import _pm_correction

    shape = np.ones(24)
    corr = _pm_correction(shape, am=1000, pm=1500)
    assert corr[9] == 1 and corr[16] == pytest.approx(1.5) and corr[20] == pytest.approx(1.5)
    assert 1 < corr[12] < 1.5


def test_bus_routes_take_geometry_from_the_roads_and_report_cuts(model):
    assert len(model.bus_routes) == 1
    route = model.bus_routes[0]
    assert route["label"] == "2" and route["ways"] == [1]  # the way outside the map is dropped
    closed = np.zeros(len(model), bool)
    closed[roads_of_way(model, 1)[0]] = True
    out = traffic_impact(model, RoadDisruption(closed=closed, speed_factor=np.ones(len(model)),
                                               reason=["manual" if c else None for c in closed]),
                         origins=6, per_origin=6)
    assert [b["label"] for b in out["bus_routes_hit"]] == ["2"]
    assert traffic_payload(model)["bus_routes"][0]["paths"]


def test_detour_congestion_only_adds_delay(model):
    closed = np.zeros(len(model), bool)
    closed[roads_of_way(model, 2)] = True  # the middle row: its traffic must detour
    disruption = RoadDisruption(closed=closed, speed_factor=np.ones(len(model)),
                                reason=["manual" if c else None for c in closed])
    plain = traffic_impact(model, disruption, hour=9, origins=6, per_origin=8, congestion=False)
    jammed = traffic_impact(model, disruption, hour=9, origins=6, per_origin=8)
    assert jammed["diverted_vph"] > 0 and jammed["vph_per_trip"] > 0
    assert jammed["delay_min"]["mean"] >= plain["delay_min"]["mean"]
    assert jammed["delay_min_uncongested"] == plain["delay_min"]
    assert plain["jammed"] == [] and plain["jammed_km"] == 0


def test_typed_road_events_close_the_named_street(model):
    from dynacity.scenario_compiler import DraftRoadEvent, ScenarioDraft

    from test_scenario_compiler import FakeClient

    draft = ScenarioDraft(scenario_id="close-it", horizon_years=2, road_events=[
        DraftRoadEvent(event_id="c1", kind="closure", street="Saeb Salam"),
        DraftRoadEvent(event_id="x1", kind="crash", street="Pierre Gemayel"),
    ])
    client = TestClient(create_app(engine_fixture(), scenario_client=FakeClient(draft),
                                   viewer_snapshot=snapshot(), viewer_traffic=model))
    body = client.post("/v1/viewer/scenario", json={
        "text": "Close Saeb Salam and put a crash on Pierre Gemayel", "hour": 9,
    }).json()
    assert body["compilation"]["scenario"] is not None
    events = {e["event_id"]: e for e in body["compilation"]["road_events"]}
    assert events["c1"]["roads"] == roads_of_way(model, 1)
    assert body["road_events"]["closed"] == roads_of_way(model, 1)
    assert len(body["road_events"]["crashes"]) == 1
    assert body["traffic"]["closed_by"]["manual"] == 2 and body["traffic"]["closed_by"]["crash"] == 1


def test_several_samples_report_their_range(model):
    closed = np.zeros(len(model), bool)
    closed[roads_of_way(model, 2)] = True
    disruption = RoadDisruption(closed=closed, speed_factor=np.ones(len(model)),
                                reason=["manual" if c else None for c in closed])
    out = traffic_impact(model, disruption, hour=9, origins=6, per_origin=8, samples=3)
    assert out["samples"] == 3
    lo, hi = out["ranges"]["delay_p50"]
    assert lo <= out["delay_min"]["p50"] <= hi
    assert set(out["ranges"]) == {"affected", "cut_off", "delay_p50", "delay_p90", "diverted_vph", "spillover_trips"}
    assert traffic_impact(model, disruption, origins=6, per_origin=8)["samples"] == 1
