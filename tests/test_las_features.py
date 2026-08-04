import numpy as np
import pytest

from dynacity.features import height_above_ground, load_points, vegetation_mask
from dynacity.las import LASHeader, classification_sample, iter_point_chunks

from conftest import write_las


def test_las_header_and_point_reader(tmp_path):
    source = write_las(
        tmp_path / "sample.las",
        [
            (0, 0, 1000, 0, 20, 40, 20),
            (100, 100, 2000, 2, 30, 200, 20),
        ],
    )
    header = LASHeader.read(source)
    assert header.version == "1.2"
    assert header.point_count == 2
    assert header.point_format == 2
    assert header.vlr_count == 0
    chunk = next(iter_point_chunks(source))
    assert np.allclose(chunk.z, [10.0, 20.0])
    assert chunk.classification.tolist() == [0, 2]
    assert classification_sample(source, 2) == {0: 1, 2: 1}


def test_las_guard_prevents_implicit_large_load(tmp_path):
    source = write_las(tmp_path / "sample.las", [(0, 0, 0, 0, 0, 0, 0)] * 3)
    with pytest.raises(ValueError, match="maximum"):
        list(iter_point_chunks(source, max_points=2))


def test_ground_and_rgb_features(tmp_path):
    source = write_las(
        tmp_path / "sample.las",
        [
            (100, 100, 1000, 0, 50, 50, 50),
            (200, 200, 2000, 0, 10, 250, 10),
            (300, 300, 2500, 0, 50, 50, 50),
        ],
    )
    points = load_points(source)
    hag = height_above_ground(points.x, points.y, points.z, grid_size_m=10)
    assert hag.min() == 0
    vegetation = vegetation_mask(points.red, points.green, points.blue)
    assert vegetation.tolist() == [False, True, False]

