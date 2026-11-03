import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'audit_road_paint_heights.py'
spec = importlib.util.spec_from_file_location('audit_road_paint_heights', SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_interpolates_height_on_sloped_road_triangle():
    # The plane rises 1 m per metre east and 2 m per metre north.
    triangle = np.array([[0, 0, 10], [4, 0, 14], [0, 3, 16]], dtype=float)
    assert audit.triangle_height(triangle, 1, 1) == pytest.approx(13)


def test_vertical_or_degenerate_triangle_has_no_ground_height():
    triangle = np.array([[0, 0, 0], [1, 1, 1], [2, 2, 5]], dtype=float)
    assert audit.triangle_height(triangle, 1, 1) is None
