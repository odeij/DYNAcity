import json
import math

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from dynacity.access import (
    RAILBED_USES, AccessModel, Homes, Place, build_access, destinations, homes_from_pack, limit_by_size,
    local_size_rules, osm_service, sites_from_bbed, sites_from_geojson, sites_from_line, sites_from_osm_parking,
    walk_graph,
)
from dynacity.cli import main
from dynacity.viewer import utm_to_lonlat

CRS = "EPSG:32636"
X0, Y0 = 733000.0, 3752000.0
AREA = box(X0 - 500, Y0 - 500, X0 + 2500, Y0 + 500)


def geo(x, y):
    lon, lat = utm_to_lonlat(x, y, 36, True)
    return {"lon": lon, "lat": lat}


def way(identifier, xs, tags, y=Y0, first_node=None):
    first = first_node if first_node is not None else identifier * 100
    return {"type": "way", "id": identifier, "nodes": [first + i for i in range(len(xs))],
            "geometry": [geo(X0 + x, y) for x in xs], "tags": tags}


def street():
    """A 2 km street along y = Y0 with a node every 100 m (node ids 100..120)."""

    return {"elements": [way(1, [100 * i for i in range(21)], {"highway": "residential"})]}


def test_walkable_ways_follow_the_rules():
    osm = {"elements": [
        way(1, [0, 100, 200], {"highway": "residential"}),
        way(2, [200, 300], {"highway": "trunk"}, first_node=102),  # shares node 102
        way(3, [300, 400], {"highway": "motorway"}, first_node=201),
        way(4, [400, 500], {"highway": "motorway", "foot": "yes"}, first_node=400),
        way(5, [500, 600], {"highway": "footway", "foot": "no"}, first_node=500),
        way(6, [0, 5000], {"highway": "residential"}, y=Y0 + 50),  # second node outside the area
    ]}
    graph = walk_graph(osm, AREA, CRS)
    lengths = sorted(round(float(v)) for v in graph.length[::2])
    assert lengths == [100, 100, 100, 100], "residential, trunk and foot=yes motorway kept; motorway, foot=no and outside dropped"
    # 0, 100, 200 + 300 (trunk) + 400, 500 (foot=yes motorway) + way 6's inside end, left without an edge
    assert len(graph) == 7
    assert len(graph.source) == len(graph.target) == 8, "both directions"


def test_osm_places_count_for_the_right_service():
    assert osm_service({"shop": "bakery"}) == "shopping"
    assert osm_service({"shop": "jewelry"}) is None, "only daily shopping counts"
    assert osm_service({"amenity": "marketplace"}) == "shopping"
    assert osm_service({"leisure": "pitch"}) == "entertainment"
    assert osm_service({"leisure": "pitch", "access": "private"}) is None
    assert osm_service({"leisure": "park"}) == "parks"
    assert osm_service({"office": "company"}) == "work"


def test_destinations_join_key_buildings_and_osm_inside_the_area():
    facilities = {"schema": "dynacity-facilities/1", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": list(geo(X0 + 50, Y0).values())},
         "properties": {"id": "node/1", "facility": "school", "name": "A school", "named": True, "source": "OpenStreetMap"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": list(geo(X0 + 60, Y0).values())},
         "properties": {"id": "node/2", "facility": "pharmacy", "name": "Pharmacy", "named": False, "source": "OpenStreetMap"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": list(geo(X0 + 9000, Y0).values())},
         "properties": {"id": "node/3", "facility": "hospital", "name": "Far", "named": True, "source": "OpenStreetMap"}},
    ]}
    osm = {"elements": [
        {"type": "node", "id": 5, **geo(X0 + 70, Y0), "tags": {"shop": "supermarket", "name": "Market"}},
        {"type": "node", "id": 5, **geo(X0 + 70, Y0), "tags": {"shop": "supermarket", "name": "Market"}},
        {"type": "node", "id": 6, **geo(X0 + 80, Y0), "tags": {"office": "government"}},
        {"type": "way", "id": 7, "geometry": [geo(X0, Y0 + 10), geo(X0 + 20, Y0 + 10)], "tags": {"leisure": "park"}},
        {"type": "relation", "id": 8, "bounds": {"minlon": geo(X0, Y0)["lon"], "maxlon": geo(X0 + 40, Y0)["lon"],
                                                 "minlat": geo(X0, Y0)["lat"], "maxlat": geo(X0, Y0 + 40)["lat"]},
         "tags": {"leisure": "pitch"}},
    ]}
    places = destinations(facilities, [osm, osm], AREA, CRS)
    found = sorted((p.service, p.id) for p in places)
    assert found == [("education", "node/1"), ("entertainment", "relation/8"), ("parks", "way/7"),
                     ("shopping", "node/5")], "pharmacy is not medical care; far places and repeats dropped; " \
                                              "government offices come from the key-buildings layer"
    park = next(p for p in places if p.id == "way/7")
    assert abs(park.x - (X0 + 10)) < 0.5 and abs(park.y - (Y0 + 10)) < 0.5


def model(sites=None):
    homes = Homes(np.array(["h0", "h1"]), np.array([[X0, Y0], [X0 + 2000, Y0]]), np.array([30.0, 10.0]))
    places = [Place("s", "education", X0 + 600, Y0, "test"), Place("p", "parks", X0 + 2000, Y0 + 20, "test")]
    sites = sites if sites is not None else [
        {"id": "a", "x": X0 + 100, "y": Y0, "area_m2": 300.0, "state": "empty_or_parking", "allowed": ["education", "parks"],
         "allowed_source": "given"},
        {"id": "b", "x": X0 + 1900, "y": Y0, "area_m2": 50.0, "state": "empty_or_parking", "allowed": ["education"],
         "allowed_source": "given"},
    ]
    return build_access(walk_graph(street(), AREA, CRS), homes, places, sites,
                        services=("education", "medical", "parks"), score_services=("education", "medical"))


def test_walks_run_along_the_network_and_plans_take_the_nearest():
    m = model()
    assert m.base[0, 0] == pytest.approx(600, abs=1) and m.base[0, 1] == pytest.approx(1400, abs=1)
    assert math.isinf(m.base[1, 0]), "no medical place mapped"
    assert m.base[2, 1] == pytest.approx(20, abs=1), "the park's 20 m snap counts"
    d = m.distances({"a": "education"})
    assert d[0, 0] == pytest.approx(100, abs=1) and d[0, 1] == pytest.approx(1400, abs=1)
    with pytest.raises(ValueError, match="may not take"):
        m.distances({"b": "parks"})
    with pytest.raises(ValueError, match="unknown site"):
        m.distances({"z": "parks"})


def test_scores_follow_the_paper():
    m = model()
    base = m.score()
    # h0 (75% of residents) reaches education within 15 min only; nobody reaches medical care.
    assert base["service_score"] == {"500m": 0.0, "1170m": pytest.approx(0.375)}
    assert base["services"]["parks"]["residents_within"]["500m"] == pytest.approx(0.25)
    assert base["services"]["parks"]["in_score"] is False and base["scored_services"] == ["education", "medical"]
    plan = m.score({"a": "education"})
    assert plan["service_score"]["500m"] == pytest.approx(0.375)
    assert plan["services"]["education"]["places"] == 2
    # alpha weighs services against parks at the paper's 500 m.
    d = m.distances({"a": "education"})
    assert m.objective(d, alpha=2.0) == pytest.approx(2 * 0.375 + 0.25)


def test_greedy_and_gains_pick_the_best_site_and_stop_without_gain():
    m = model()
    gains = m.gains()
    assert [g["site"] for g in gains] == ["a", "b"]
    assert gains[0]["best_use"] == "parks" and gains[0]["best_gain"] == pytest.approx(0.75)
    steps = m.greedy(steps=5)
    assert [(s["site"], s["use"]) for s in steps] == [("a", "parks"), ("b", "education")]
    assert steps[-1]["objective"] == pytest.approx(0.75 + 0.25 + 0.125)


def test_model_round_trips_and_scores_from_the_command_line(tmp_path):
    m = model()
    path = tmp_path / "access.npz"
    m.save(path)
    again = AccessModel.load(path)
    assert again.site_ids == ["a", "b"] and again.meta["score_services"] == ["education", "medical"]
    assert again.score({"a": "parks"}) == m.score({"a": "parks"})
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"plan_id": "p1", "uses": {"a": "education"}}))
    out = tmp_path / "score.json"
    main(["score-access", "--model", str(path), "--plan", str(plan), "--output", str(out)])
    result = json.loads(out.read_text())
    assert result["change"]["residents_within"]["education"]["500m"] == pytest.approx(0.75)
    plan.write_text(json.dumps({"uses": {"a": "work"}}))
    with pytest.raises(SystemExit):
        main(["score-access", "--model", str(path), "--plan", str(plan), "--output", str(out)])


class FakePack:
    building_ids = np.array(["b0", "b1", "b2", "b3"])
    buildings = [box(X0, Y0, X0 + 10, Y0 + 10), box(X0 + 20, Y0, X0 + 30, Y0 + 10),
                 box(X0 + 40, Y0, X0 + 42, Y0 + 2), box(X0 + 9000, Y0, X0 + 9010, Y0 + 10)]

    def heights(self):
        return np.array([9.0, 2.0, 12.0, 9.0])


def test_homes_are_model_buildings_with_residents_from_floor_area():
    homes = homes_from_pack(FakePack(), AREA)
    assert list(homes.ids) == ["b0"], "too low, too small and outside are left out"
    assert homes.residents[0] == pytest.approx(100 * 3 / 100)


def test_sites_come_from_bbed_states_and_extra_files_and_respect_sizes():
    def lot(objectid, status, x, size):
        ring = [list(geo(X0 + x + dx, Y0 + dy).values()) for dx, dy in ((0, 0), (size, 0), (size, size), (0, size), (0, 0))]
        return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"OBJECTID": objectid, "F5__Current_status": status, "ParcelID": f"P{objectid}"}}

    bbed = {"features": [lot(1, "Parking Lot", 0, 20), lot(2, "Complete Building", 100, 20),
                         lot(3, "Construction on hold", 200, 30)]}
    sites = sites_from_bbed(bbed, AREA, CRS)
    states = {s["id"]: s["state"] for s in sites}
    assert states == {"bbed/1": "empty_or_parking", "bbed/3": "stalled_or_cancelled"}, states
    assert next(s for s in sites if s["id"] == "bbed/1")["area_m2"] == pytest.approx(400, rel=0.01)
    removed = limit_by_size(sites, {"education": 1000, "parks": 500})
    assert removed == 3
    assert "education" not in next(s for s in sites if s["id"] == "bbed/3")["allowed"]
    assert "parks" in next(s for s in sites if s["id"] == "bbed/3")["allowed"]
    extra = sites_from_geojson({"features": [{"type": "Feature", "geometry": {"type": "Point",
                                "coordinates": list(geo(X0, Y0).values())},
                                "properties": {"id": "railbed/1", "allowed": ["parks"], "owner": "railway authority"}}]}, CRS)
    assert extra[0]["allowed"] == ["parks"] and extra[0]["owner"] == "railway authority"
    railbed = sites_from_geojson({"features": [lot(9, None, 0, 20) | {"properties": {"id": "railbed/2"}}]}, CRS)
    assert railbed[0]["area_m2"] == pytest.approx(400, rel=0.01) and abs(railbed[0]["x"] - (X0 + 10)) < 0.5
    with pytest.raises(ValueError, match="unknown uses"):
        sites_from_geojson({"features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [35.5, 33.9]},
                                          "properties": {"id": "x", "allowed": ["casino"]}}]}, CRS)


def outline(identifier, x, size, tags):
    ring = [geo(X0 + x + dx, Y0 + dy) for dx, dy in ((0, 0), (size, 0), (size, size), (0, size), (0, 0))]
    return {"type": "way", "id": identifier, "geometry": ring, "tags": tags}


def test_parking_lots_on_the_ground_become_sites_of_their_mapped_size():
    osm = {"elements": [
        outline(1, 0, 30, {"amenity": "parking"}),
        outline(2, 100, 30, {"amenity": "parking", "parking": "underground"}),
        outline(3, 9000, 30, {"amenity": "parking"}),  # outside the area
        outline(4, 200, 30, {"amenity": "school"}),
        {"type": "node", "id": 5, **geo(X0, Y0), "tags": {"amenity": "parking"}},  # no outline
    ]}
    sites = sites_from_osm_parking([osm, osm], AREA, CRS)
    assert [s["id"] for s in sites] == ["osm/way/1"], "kept once; underground, outside, non-parking and points dropped"
    assert sites[0]["area_m2"] == pytest.approx(900, rel=0.01) and "private" in sites[0]["owner"]


def test_the_rail_bed_is_cut_into_pieces_for_parks_and_play_only():
    line = [tuple(geo(X0, Y0).values()), tuple(geo(X0 + 1000, Y0).values())]
    pieces = sites_from_line([line], CRS, piece_m=250)
    assert len(pieces) == 4 and [round(p["x"] - X0) for p in pieces] == [125, 375, 625, 875]
    assert all(p["allowed"] == RAILBED_USES and p["area_m2"] is None for p in pieces)
    assert limit_by_size(pieces, {"parks": 10_000}) == 0, "no mapped width, so no size rule"


def test_size_rules_come_from_the_places_beirut_already_has():
    osm = {"elements": [outline(10 + i, 100 * i, size, {"amenity": "school"})
                        for i, size in enumerate((10, 20, 30, 40, 50))]
           + [outline(30, 0, 20, {"leisure": "park"})]}
    rules = local_size_rules([osm], CRS)
    assert set(rules) == {"education"}, "too few parks mapped for a rule"
    assert rules["education"]["m2"] == pytest.approx(400, rel=0.01)  # 25th percentile of 100..2500 m2
    assert rules["education"]["mapped"] == 5
