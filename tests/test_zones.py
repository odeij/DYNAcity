import numpy as np
import pytest

from dynacity.viewer import utm_to_lonlat
from dynacity.zones import ZoneMap, boundary_polygon, build_zones

from test_traffic import CRS, STEP, X0, Y0, model, roads_of_way, snapshot  # noqa: F401 (fixture)


def ring_way(points: list[tuple[float, float]], role: str = "outer") -> dict:
    return {"type": "way", "role": role,
            "geometry": [dict(zip(("lon", "lat"), utm_to_lonlat(x, y, 36, True))) for x, y in points]}


def relation(rel_id: int, level: str, name: str, x0: float, y0: float, x1: float, y1: float,
             hole: tuple[float, float, float, float] | None = None) -> dict:
    # The outer ring in two ways, as OSM splits long boundaries.
    members = [ring_way([(x0, y0), (x1, y0), (x1, y1)]), ring_way([(x1, y1), (x0, y1), (x0, y0)])]
    if hole:
        a, b, c, d = hole
        members.append(ring_way([(a, b), (c, b), (c, d), (a, d), (a, b)], role="inner"))
    members.append({"type": "node", "role": "admin_centre", "lat": 0.0, "lon": 0.0})
    return {"type": "relation", "id": rel_id, "members": members,
            "tags": {"boundary": "administrative", "admin_level": level, "name:en": name}}


def boundaries() -> dict:
    # A sector over the western half of the grid, and a coarser district over
    # the whole grid; the spur east of it is left to the grid fill.
    return {"elements": [
        relation(2, "7", "District", X0 - 50, Y0 - 50, X0 + 2 * STEP + 50, Y0 + 2 * STEP + 50),
        relation(1, "8", "West", X0 - 50, Y0 - 50, X0 + STEP, Y0 + 2 * STEP + 50),
        relation(3, "4", "Governorate", X0 - 999, Y0 - 999, X0 + 999, Y0 + 999),
    ]}


def test_boundary_rings_join_and_holes_cut():
    hole = (X0 + 10, Y0 + 10, X0 + 40, Y0 + 40)
    area = boundary_polygon(relation(9, "8", "H", X0, Y0, X0 + 100, Y0 + 100, hole=hole), CRS)
    assert area.area == pytest.approx(100 * 100 - 30 * 30, rel=1e-3)


def test_finest_boundaries_first_then_coarser_then_grid(model):
    zones = build_zones(model, boundaries(), grid_m=300)
    sources = {z.zone_id: z.source for z in zones.zones}
    assert sources["osm:r1"] == "osm admin_level 8" and sources["osm:r2"] == "osm admin_level 7"
    assert "osm:r3" not in sources  # not a requested level
    assert any(s.startswith("grid") for s in sources.values())
    # The district only keeps what the sector does not cover.
    district = next(z for z in zones.zones if z.zone_id == "osm:r2")
    sector = next(z for z in zones.zones if z.zone_id == "osm:r1")
    assert district.area.intersection(sector.area).area < 1.0
    # Every road has exactly one zone; the west column is in the sector, the spur in the grid.
    assert (zones.road_zone >= 0).all()
    assert zones.zones[zones.road_zone[roads_of_way(model, 10)[0]]].zone_id == "osm:r1"
    assert zones.zones[zones.road_zone[roads_of_way(model, 20)[0]]].source.startswith("grid")
    index = {z.zone_id: i for i, z in enumerate(zones.zones)}
    assert tuple(sorted((index["osm:r1"], index["osm:r2"]))) in zones.adjacency
    summary = zones.summary(model)
    assert sum(s["roads"] for s in summary) == len(model)
    assert all(s["roads"] > 0 for s in summary)


def test_without_grid_roads_outside_every_boundary_stay_unzoned(model):
    zones = build_zones(model, boundaries(), levels=("8",), grid_m=None)
    assert [z.zone_id for z in zones.zones] == ["osm:r1"]
    assert (zones.road_zone[roads_of_way(model, 10)] == 0).all()
    assert (zones.road_zone[roads_of_way(model, 20)] == -1).all()


def test_buildings_take_the_zone_under_their_centroid(model):
    zones = build_zones(model, boundaries(), grid_m=300)
    placed = zones.object_zones(snapshot())
    names = [zones.zones[i].zone_id for i in placed]
    assert names == ["osm:r1", "osm:r2"]  # "kerb" in the west, "block" at the east corner


def test_zone_map_round_trips_and_checks_its_model(model, tmp_path):
    zones = build_zones(model, boundaries(), grid_m=300)
    zones.save(tmp_path / "zones.json")
    loaded = ZoneMap.load(tmp_path / "zones.json")
    assert [z.zone_id for z in loaded.zones] == [z.zone_id for z in zones.zones]
    assert list(loaded.road_zone) == list(zones.road_zone) and loaded.adjacency == zones.adjacency
    assert loaded.zones[0].area.area == pytest.approx(zones.zones[0].area.area, rel=1e-4)
    loaded.check(model)
    loaded.road_zone = np.zeros(3, dtype=np.int64)
    with pytest.raises(ValueError):
        loaded.check(model)
