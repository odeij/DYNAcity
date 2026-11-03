from datetime import date

import numpy as np
import pytest

from dynacity.contracts import UrbanObjectState, UrbanStateSnapshot
from dynacity.status import CanonicalState
from dynacity.terrain import GroundModel, attach_ground_elevation, ground_from_surface


def building(object_id, x, y=0.0):
    return UrbanObjectState(
        object_id=object_id, state=CanonicalState.STABLE_BUILT, sector="Mar Mikhael",
        building_use="Residential", footprint_area_m2=100, floors=4.0, height_m=12.8,
        centroid_x_m=x, centroid_y_m=y,
    )


def city() -> UrbanStateSnapshot:
    """A row of buildings along x: one at x = 0, one 150 m out, one 5 km away."""

    return UrbanStateSnapshot(
        snapshot_id="row", as_of=date(2024, 4, 30), data_version="synthetic",
        objects=[building("zero", 0), building("near", 150), building("far", 5000)],
    )


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
