"""The planner's optional context layers: residents near streets (WorldPop) and places on them (Overture).

Everything here is synthetic: a handful of invented cells and places around the small test road grid.
"""
import json

import pytest
from fastapi.testclient import TestClient

from dynacity.planner import planner_page, planner_roads
from dynacity.planner_context import (
    PLACE_FIELDS, PLACES_SCHEMA, POPULATION_SCHEMA, PlannerContext, allowlisted_places, prepare_places,
    prepare_population, round_residents,
)
from dynacity.service import create_app
from dynacity.traffic import build_traffic_model

from test_models_engine_api import engine_fixture
from test_traffic import bus_overpass, overpass, snapshot, write_velocities

# The test grid: road 0 runs west-east at about 33.8646 N from 35.5188 E to 35.5209 E, road 1 continues it
# to 35.5231 E (about 200 m each). Road 2 is a parallel street 200 m north. Road 12 is a spur east of the grid.
CELL = 0.00083333333  # WorldPop's 3 arc-second cell, in degrees


@pytest.fixture
def model(tmp_path):
    csv = tmp_path / "velocities.csv"
    write_velocities(csv)
    return build_traffic_model(snapshot(), csv, overpass=overpass(), bus_overpass=bus_overpass())


def population(*cells):
    return {"schema": POPULATION_SCHEMA, "product": "synthetic", "year": 2025, "licence": "CC BY 4.0",
            "attribution": "test", "source_url": "", "doi": "", "cell_deg": [CELL, CELL], "cells": [list(c) for c in cells]}


def places(*rows):
    return {"schema": PLACES_SCHEMA, "release": "test", "licence": "CDLA Permissive 2.0", "attribution": "test",
            "source_url": "", "providers": {}, "places": [dict(r) for r in rows]}


def place(pid, lon, lat, name="", category="cafe", confidence=0.9):
    return {"id": pid, "name": name or pid, "category": category, "confidence": confidence, "lon": lon, "lat": lat}


# ---------------------------------------------------------------- residents
def test_residents_are_summed_per_road_within_200_m(model):
    near_road0 = (35.5198, 33.8646, 1000.0)
    far_away = (35.60, 33.95, 5000.0)
    ctx = PlannerContext(model, population(near_road0, far_away), None)
    # The whole cell lies within 200 m of road 0; the distant cell counts for no road.
    assert ctx.road_residents(0) == pytest.approx(1000.0)
    assert all(ctx.road_residents(i) < 5000 for i in range(len(model)))
    # Road 12 starts about 330 m east of the cell: out of reach.
    assert ctx.road_residents(12) == 0.0


def test_a_resident_near_two_changed_streets_is_counted_once(model):
    cell = (35.52093, 33.8646, 1000.0)  # at the junction of roads 0 and 1
    ctx = PlannerContext(model, population(cell), None)
    assert ctx.road_residents(0) == pytest.approx(1000.0) and ctx.road_residents(1) == pytest.approx(1000.0)
    both = ctx.summary([0, 1])
    # A sum of the two buffers would say 2,000; the union says 1,000.
    assert both["residents"] == pytest.approx(1000.0)
    assert both["residents"] < ctx.road_residents(0) + ctx.road_residents(1)
    # Repeated ids change nothing.
    assert ctx.summary([1, 0, 0])["residents"] == both["residents"]
    assert both["roads"] == 2


def test_union_counts_cells_that_only_one_street_reaches(model):
    west = (35.5188, 33.8646, 400.0)    # at the west end of road 0, nearly 400 m from road 12
    east = (35.5253, 33.8663, 600.0)    # at the far end of the spur, road 12, about 400 m from road 0
    ctx = PlannerContext(model, population(west, east), None)
    assert ctx.summary([0])["residents"] == pytest.approx(400.0)
    assert ctx.summary([12])["residents"] == pytest.approx(600.0)
    assert ctx.summary([0, 12])["residents"] == pytest.approx(1000.0)


def test_partial_cells_count_by_the_share_within_200_m(model):
    # A cell north of road 2 (200 m above road 0): only part of it is within 200 m of road 0.
    cell = (35.5198, 33.8665, 1600.0)
    ctx = PlannerContext(model, population(cell), None)
    assert 0 < ctx.road_residents(0) < 1600.0
    assert ctx.road_residents(2) == pytest.approx(1600.0)


def test_residents_are_rounded_sensibly():
    assert round_residents(0.4) == 0
    assert round_residents(47) == 50
    assert round_residents(1234) == 1200
    assert round_residents(4567) == 4600
    assert round_residents(180324) == 180500


def test_unknown_road_indices_are_ignored(model):
    ctx = PlannerContext(model, population((35.5198, 33.8646, 100.0)), None)
    assert ctx.summary([0, 999, -1]) == ctx.summary([0])


# ---------------------------------------------------------------- places
def test_places_belong_to_roads_within_25_m(model):
    # About 11 m north of road 0, 22 m south of it, and 66 m north (too far).
    rows = [place("a", 35.5198, 33.8647, "Bakery"), place("b", 35.5198, 33.8644, "Pharmacy", "pharmacy"),
            place("c", 35.5198, 33.8652, "Too far")]
    ctx = PlannerContext(model, None, places(*rows))
    assert sorted(ctx._place_rows[p]["id"] for p in ctx.road_places(0)) == ["a", "b"]
    s = ctx.summary([0])["places"]
    assert s["count"] == 2 and set(s["names"]) == {"Bakery", "Pharmacy"}
    assert ctx.summary([4])["places"]["count"] == 0


def test_a_place_at_a_junction_is_counted_once_for_both_streets(model):
    junction = place("j", 35.52093, 33.864572, "Junction kiosk")
    ctx = PlannerContext(model, None, places(junction))
    assert ctx.summary([0])["places"]["count"] == 1 and ctx.summary([1])["places"]["count"] == 1
    assert ctx.summary([0, 1])["places"]["count"] == 1


def test_street_summary_lists_top_categories_and_at_most_eight_names(model):
    rows = [place(f"p{i}", 35.5190 + i * 0.00015, 33.8646, f"Shop {i:02d}", "cafe" if i < 5 else "bakery", 0.5 + i / 100)
            for i in range(12)]
    ctx = PlannerContext(model, None, places(*rows))
    s = ctx.summary([0, 1])["places"]
    assert s["count"] == 12
    assert s["categories"][0] == ["bakery", 7] and s["categories"][1] == ["cafe", 5]
    assert len(s["names"]) == 8
    # The most confident places are listed first.
    assert s["names"][0] == "Shop 11"


def test_meta_sends_only_places_on_a_street_with_allowlisted_fields(model):
    rows = [place("on", 35.5198, 33.8647, "On the street"), place("off", 35.60, 33.95, "Far away")]
    meta = PlannerContext(model, population((35.5198, 33.8646, 10.0)), places(*rows)).meta()
    assert meta["places"]["points"] == [[35.5198, 33.8647, "On the street", "cafe"]]
    assert meta["population"]["year"] == 2025 and meta["population"]["licence"] == "CC BY 4.0"
    assert "cells" not in json.dumps(meta)


def test_distribution_reports_every_road(model):
    ctx = PlannerContext(model, population((35.5198, 33.8646, 1000.0)), places(place("a", 35.5198, 33.8647)))
    d = ctx.distribution()
    assert d["roads"] == len(model) and d["roads_with_a_place"] >= 1
    assert d["residents_within_200m"]["max"] == pytest.approx(1000.0)


# ---------------------------------------------------------------- the allowlist
def overture_feature(**extra):
    props = {
        "id": "gers-1", "confidence": 0.8, "names": {"primary": "Café Test", "common": None, "rules": None},
        "taxonomy": {"primary": "coffee_shop", "hierarchy": ["food_and_drink", "coffee_shop"]},
        "basic_category": "cafe", "emails": ["private@example.org"], "phones": ["+96100000000"],
        "socials": ["https://example.org/page"], "websites": ["https://example.org"],
        "addresses": [{"freeform": "1 Test Street"}], "brand": {"names": {"primary": "Chain"}},
        "sources": [{"dataset": "meta", "license": "CDLA-Permissive-2.0", "record_id": "42"}],
    }
    props.update(extra)
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [35.52, 33.86]}, "properties": props}


def test_allowlist_keeps_only_id_name_category_confidence_and_point():
    (row,) = allowlisted_places([overture_feature()])
    assert set(row) == set(PLACE_FIELDS)
    assert row == {"id": "gers-1", "name": "Café Test", "category": "coffee_shop", "confidence": 0.8,
                   "lon": 35.52, "lat": 33.86}
    text = json.dumps(row)
    for private in ("private@example.org", "+96100000000", "example.org", "Test Street", "Chain", "record_id"):
        assert private not in text


def test_allowlist_drops_rows_it_cannot_use():
    older_schema = overture_feature(taxonomy=None, categories={"primary": "bakery"})
    kept = allowlisted_places([
        older_schema,
        overture_feature(id="low", confidence=0.2),
        overture_feature(id="nocat", taxonomy=None),
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": []}, "properties": {"id": "x"}},
    ], min_confidence=0.5)
    assert [r["id"] for r in kept] == ["gers-1"] and kept[0]["category"] == "bakery"


def test_prepared_places_file_holds_no_contact_details(tmp_path):
    raw = tmp_path / "raw.geojson"
    raw.write_text(json.dumps({"type": "FeatureCollection", "features": [overture_feature()]}), encoding="utf-8")
    out = tmp_path / "places.json"
    info = prepare_places(raw, out, release="test", source_url="u", licence="CDLA Permissive 2.0", attribution="a")
    assert info["kept"] == 1 and info["providers"] == {"meta (CDLA-Permissive-2.0)": 1}
    text = out.read_text(encoding="utf-8")
    for private in ("private@example.org", "+96100000000", "example.org", "Test Street", "Chain"):
        assert private not in text
    assert json.loads(text)["places"][0]["name"] == "Café Test"


def test_population_is_cut_from_a_geotiff(tmp_path):
    np = pytest.importorskip("numpy")
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin
    data = np.zeros((20, 20), dtype="float32")
    data[5, 5], data[6, 6] = 12.5, 7.5
    tif = tmp_path / "pop.tif"
    with rasterio.open(tif, "w", driver="GTiff", height=20, width=20, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(35.50, 33.90, CELL, CELL), nodata=-99999) as dst:
        dst.write(data, 1)
    out = tmp_path / "population.json"
    info = prepare_population(tif, out, [35.50, 33.88, 35.52, 33.90], product="p", year=2025, source_url="u",
                              licence="CC BY 4.0", attribution="a")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert info["cell_count"] == 2 and payload["total"] == pytest.approx(20.0)
    assert sorted(c[2] for c in payload["cells"]) == [7.5, 12.5]
    lon, lat, _ = payload["cells"][0]
    assert 35.50 < lon < 35.52 and 33.88 < lat < 33.90


# ---------------------------------------------------------------- flags off: nothing changes
def test_without_the_flags_the_page_asks_for_no_context(model):
    page = planner_page()
    # The switch is off, and the only call that fetches context sits behind it.
    assert "const CONTEXT = /*PLANNER_CONTEXT*/false;" in page
    assert "if (CONTEXT) await loadContext();" in page
    assert "if (!CONTEXT || !state.ctx" in page and "CONTEXT && state.ctx ?" in page
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot(), viewer_traffic=model))
    served = client.get("/planner")
    assert served.status_code == 200 and served.text == page
    assert client.get("/v1/planner/context").status_code == 404
    assert client.post("/v1/planner/context/summary", json={"roads": [0]}).status_code == 404
    # The road list is exactly what it was.
    assert client.get("/v1/planner/roads").json() == planner_roads(model)


def test_with_the_flags_the_page_switches_the_layers_on_and_the_roads_stay_the_same(model):
    ctx = PlannerContext(model, population((35.52093, 33.8646, 1000.0)), places(place("a", 35.5198, 33.8647, "Bakery")))
    plain = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot(), viewer_traffic=model))
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=snapshot(), viewer_traffic=model, planner_context=ctx))
    page = client.get("/planner").text
    assert "const CONTEXT = true;" in page and "/*PLANNER_CONTEXT*/false" not in page
    # The road list is byte for byte the same with or without the layers.
    assert client.get("/v1/planner/roads").content == plain.get("/v1/planner/roads").content
    meta = client.get("/v1/planner/context").json()
    assert meta["population"]["year"] == 2025 and meta["places"]["points"] == [[35.5198, 33.8647, "Bakery", "cafe"]]
    both = client.post("/v1/planner/context/summary", json={"roads": [0, 1]}).json()
    assert both["residents"] == pytest.approx(1000.0) and both["residents_rounded"] == 1000
    assert both["places"]["count"] == 1
    assert client.post("/v1/planner/context/summary", json={"roads": [0], "extra": 1}).status_code == 422


def test_the_page_credits_both_sources_and_labels_the_estimate():
    page = planner_page(context=True)
    assert "WorldPop" in page and "Overture Maps" in page
    assert "modelled estimate" in page and "no census since 1932" in page
    assert "within ${state.ctx.residents_radius_m} m" in page
    assert "courier" not in page.lower()
