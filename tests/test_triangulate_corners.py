import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import triangulate_corners as tc  # noqa: E402

VIEW = {'cam': np.array([0., 0., 1.4]), 'yaw': 90.}  # looking east


def test_project_and_ray_round_trip_through_the_same_camera():
    X = np.array([60., 12., 9.])
    px = tc.project(X, VIEW, 11., 54.5)
    d = tc.ray(px, VIEW, 11., 54.5)
    expected = (X - VIEW['cam']) / np.linalg.norm(X - VIEW['cam'])
    assert d == pytest.approx(expected, abs=1e-6)
    assert tc.project(np.array([-10., 0., 1.4]), VIEW, 11., 54.5) is None  # behind the camera


def test_nearest_point_recovers_a_point_seen_from_two_cameras_and_is_weak_along_a_collinear_baseline():
    X = np.array([50., 20., 8.])
    cams = [np.array([0., 0., 1.4]), np.array([20., 0., 1.4])]
    dirs = [(X - c) / np.linalg.norm(X - c) for c in cams]
    assert tc.nearest_point(cams, dirs) == pytest.approx(X, abs=1e-6)
    # a 12 px error moves the point far more along the sight line than across it (why the fit reports a range)
    rng = np.random.default_rng(1)
    err = []
    for _ in range(200):
        d2 = []
        for c in cams:
            px = np.array(tc.project(X, {'cam': c, 'yaw': 90.}, 11., 54.5)) + rng.normal(0, 12, 2)
            d2.append(tc.ray(px, {'cam': c, 'yaw': 90.}, 11., 54.5))
        err.append(tc.nearest_point(cams, d2) - X)
    err = np.array(err)
    along = (X - cams[0]) * [1, 1, 0]
    along = along / np.linalg.norm(along)
    across = np.array([-along[1], along[0], 0.])
    assert (err @ along).std() > 3 * (err @ across).std()  # the sight line is the weak direction
