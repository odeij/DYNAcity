import json

import numpy as np
import pytest

from conftest import write_las
from dynacity.las_surface import export_las_surface


def test_surface_preserves_observations_gaps_and_unverified_status(tmp_path):
    source = write_las(tmp_path / "cloud.las", [
        (0, 0, 1000, 0, 0, 0, 0), (0, 0, 3000, 2, 0, 0, 0),
        (200, 200, 2000, 0, 0, 0, 0),
    ])
    dest = tmp_path / "review.npz"
    report = export_las_surface(source, dest, cell_m=1,
                               horizontal_crs="EPSG:32636", crs_evidence="test coordinates")
    assert report["points_processed"] == 3
    assert report["classification_counts"] == {"0": 2, "2": 1}
    assert not report["verified_flood_terrain"]
    assert report["vertical_reference"] is None
    with np.load(dest, allow_pickle=False) as data:
        assert data["point_count"].sum() == 3
        assert data["ground_label_count"].sum() == 1
        assert data["min_observed_z"][2, 0] == 10
        assert data["max_observed_z"][2, 0] == 30
        assert np.isnan(data["min_observed_z"][1, 1])
        assert json.loads(str(data["metadata"])) == report


def test_surface_refuses_truncation_and_compression(tmp_path):
    source = write_las(tmp_path / "cloud.las", [(0, 0, 0, 0, 0, 0, 0)])
    kwargs = dict(cell_m=1, horizontal_crs="EPSG:32636", crs_evidence="test")
    content = source.read_bytes()
    source.write_bytes(content[:-1])
    with pytest.raises(ValueError, match="truncated"):
        export_las_surface(source, tmp_path / "review.npz", **kwargs)
    data = bytearray(content)
    data[104] |= 0x80
    source.write_bytes(data)
    with pytest.raises(ValueError, match="compressed"):
        export_las_surface(source, tmp_path / "review.npz", **kwargs)


def test_surface_refuses_unbounded_or_nonmetric_import(tmp_path):
    source = write_las(tmp_path / "cloud.las", [(0, 0, 0, 0, 0, 0, 0)])
    kwargs = dict(cell_m=1, horizontal_crs="EPSG:32636", crs_evidence="test")
    with pytest.raises(ValueError, match="max_points"):
        export_las_surface(source, tmp_path / "review.npz", max_points=0, **kwargs)
    kwargs["horizontal_crs"] = "EPSG:4326"
    with pytest.raises(ValueError, match="metre"):
        export_las_surface(source, tmp_path / "review.npz", **kwargs)
