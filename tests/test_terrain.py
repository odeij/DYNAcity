import numpy as np
import pytest
from fastapi.testclient import TestClient

from dynacity.contracts import HazardEvent, HazardKind
from dynacity.hazards import flood_level, hazard_shock
from dynacity.service import create_app
from dynacity.terrain import GroundModel, attach_ground_elevation, ground_from_surface
from dynacity.viewer import flood_water, hazard_focus

from test_hazards import city, lonlat, point
from test_models_engine_api import engine_fixture


def slope(crs="EPSG:32636") -> GroundModel:
    """Ground rising 5 cm per metre eastwards through 2 m at x = 0, on 10 m cells around the test city."""

    x = -1000 + (np.arange(400) + 0.5) * 10
    ground = np.tile(2 + 0.05 * x, (200, 1))
    return GroundModel(crs=crs, x0=-1000.0, y0=1000.0, cell_m=10.0, ground=ground)


def test_ground_model_samples_bilinearly_and_round_trips(tmp_path):
    ground = slope()
    assert ground.sample([0.0, 150.0], [0.0, 0.0]) == pytest.approx([2.0, 9.5], abs=0.01)
    assert np.isnan(ground.sample([-5000.0], [0.0])[0])
    ground.save(tmp_path / "terrain.npz")
    loaded = GroundModel.load(tmp_path / "terrain.npz")
    assert loaded.crs == ground.crs and loaded.cell_m == 10.0
    assert loaded.sample([150.0], [0.0]) == pytest.approx([9.5], abs=0.01)


def test_ground_filter_drops_roofs_to_street_level_and_masks_the_sea():
    surface = np.full((21, 21), 10.0)
    surface[10, 10] = 40.0  # one roof
    surface[:, :3] = 0.0  # sea along the west edge
    ground = ground_from_surface(surface, cell_m=30.0)
    assert ground[10, 10] == pytest.approx(10.0, abs=0.5)
    assert np.isnan(ground[:, :3]).all() and not np.isnan(ground[:, 3:]).any()


def test_attach_ground_elevation_sets_each_located_building():
    snapshot = attach_ground_elevation(city(), slope())
    by_id = {o.object_id: o.ground_elevation_m for o in snapshot.objects}
    assert by_id["zero"] == pytest.approx(2.0, abs=0.01) and by_id["near"] == pytest.approx(9.5, abs=0.01)
    assert by_id["far"] is None  # 5 km out, off the grid
    with pytest.raises(ValueError, match="EPSG"):
        attach_ground_elevation(city(), slope(crs="EPSG:32637"))


def test_flood_fills_low_ground_and_leaves_higher_buildings_dry():
    flood = point(HazardKind.FLOOD, magnitude=1.0)
    flat, terrain = city(), attach_ground_elevation(city(), slope())
    ids = [o.object_id for o in flat.objects]
    assert flood_level(flood, flat.objects) is None
    level = flood_level(flood, terrain.objects)
    assert 2.9 < level < 3.2  # ~2 m low ground + 1 m of water
    flat_hit, terrain_hit = hazard_shock(flood, flat.objects), hazard_shock(flood, terrain.objects)
    near = ids.index("near")
    assert flat_hit.exposed[near] and not terrain_hit.exposed[near]  # 9.5 m ground stands above the water
    assert terrain_hit.exposed[ids.index("zero")]
    # Other hazards ignore terrain.
    blast = point(HazardKind.EXPLOSION, magnitude=1.0)
    assert np.allclose(hazard_shock(blast, flat.objects).destroy, hazard_shock(blast, terrain.objects).destroy)


def test_flood_water_cells_sit_only_below_the_water_level():
    terrain = attach_ground_elevation(city(), slope())
    flood = point(HazardKind.FLOOD, magnitude=1.0, radius_m=300)
    water = flood_water(flood, terrain, slope())
    cells = np.asarray(water["cells"])
    assert water["level_m"] == pytest.approx(flood_level(flood, terrain.objects), abs=0.01)
    assert len(cells) and (cells[:, 2] > 0).all() and cells[:, 2].max() <= 1.5
    # The rising ground east of x ≈ 20 m stays dry.
    assert cells[:, 0].max() < lonlat(40, 0)[0]
    assert flood_water(point(HazardKind.FIRE), terrain, slope()) is None
    [focus] = hazard_focus([flood], terrain, ground=slope())
    assert focus["water"]["cells"] == water["cells"]


def test_dropped_flood_takes_a_chosen_reach_and_returns_its_water():
    terrain = attach_ground_elevation(city(), slope())
    client = TestClient(create_app(engine_fixture(), viewer_snapshot=terrain, viewer_ground=slope()))
    drop = {"kind": "flood", "points": [lonlat(0, 0)], "severity": "severe", "radius_m": 250}
    body = client.post("/v1/viewer/hazard", json={"hazards": [drop]}).json()
    [focus] = body["hazards"]
    assert focus["radius_m"] == 250 and focus["water"]["cells"]
    assert body["forecast"]["hazard_impacts"][0]["radius_m"] == 250
