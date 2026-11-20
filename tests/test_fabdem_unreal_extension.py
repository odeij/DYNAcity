"""Footprint, municipal clip and seam statistics of the FABDEM Unreal terrain extension (synthetic grids, no Unreal)."""
import base64
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip('pyproj')
pytest.importorskip('scipy')

_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'prepare_fabdem_unreal_extension.py'
_spec = importlib.util.spec_from_file_location('prepare_fabdem_unreal_extension_under_test', _PATH)
module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = module
_spec.loader.exec_module(module)


def mask_file(tmp_path, inside_rows, inside_cols, x0=0.0, y0=0.0, cell=500.0, shape=(40, 40)):
    bits = np.zeros(shape, dtype=np.uint8)
    bits[inside_rows[0]:inside_rows[1], inside_cols[0]:inside_cols[1]] = 1
    payload = {'x0': x0, 'y0': y0, 'cell_cm': cell, 'width': shape[1], 'height': shape[0],
               'mask_b64': base64.b64encode(np.packbits(bits.ravel()).tobytes()).decode()}
    path = tmp_path / 'mask.json'
    path.write_text(__import__('json').dumps(payload))
    return path


def test_mask_inside_matches_the_packed_bit_layout_and_treats_off_grid_as_outside(tmp_path):
    mask = module.Mask(mask_file(tmp_path, (10, 20), (5, 15)))
    x = np.array([5 * 500 + 10.0, 14 * 500 + 400.0, 15 * 500 + 1.0, -10.0])
    y = np.array([10 * 500 + 10.0, 19 * 500 + 400.0, 10 * 500 + 10.0, 10 * 500 + 10.0])
    assert mask.inside(x, y).tolist() == [True, True, False, False]
    assert mask.inside(np.array([1e9]), np.array([0.0])).tolist() == [False]


def synthetic_grid(rows=8, cols=8, cell=10.0):
    z = np.full((rows, cols), 5.0)
    return SimpleNamespace(crs='EPSG:32636', x0=1000.0, y0=2000.0, cell_m=cell, ground=z, source='synthetic')


def test_extension_triangles_keep_only_extension_cells_inside_the_mask():
    ground = synthetic_grid()
    original = np.zeros((7, 7), dtype=bool)
    original[:, :3] = True                                    # the first three cell columns belong to Beirut 001
    origin = [1000.0, 2000.0, 0.0]
    # Unreal cm about the origin: X east = +100 per metre of easting.  Cells start at x0 = origin x, so cell column k
    # centres sit at (10 k + 5) m -> 1000 k + 500 cm.  The mask keeps x < 60 m only (cells 0..5).
    mask = SimpleNamespace(inside=lambda x, y: x < 6000.0)
    mesh, footprint, area, count = module.extension_triangles(ground, original, origin, mask)
    assert footprint[:, :3].sum() == 0                        # never inside the original ground
    assert footprint[:, 3:6].all() and not footprint[:, 6:].any()
    assert area == pytest.approx(count * 50.0)                # half a 10 m cell per triangle
    # The clip is by triangle centre: columns 3 and 4 keep both triangles; in column 5 only the first triangle's centre
    # (x = 58.3 m) is inside, the second (61.7 m) is not.  7 rows x (2 + 2 + 1) triangles.
    assert len(mesh['triangles']) == count == 7 * 5
    # With nothing inside the mask nothing is kept.
    none_mesh, none_foot, none_area, none_count = module.extension_triangles(ground, original, origin, SimpleNamespace(inside=lambda x, y: np.zeros(x.shape, dtype=bool)))
    assert none_count == 0 and not none_foot.any() and none_area == 0.0


def test_seam_statistics_separate_the_seam_pairs_from_the_inside_pairs():
    base = np.full((6, 6), np.nan)
    base[:, :3] = 10.0                                         # known ground on the west half
    merged = base.copy()
    merged[:, 3:] = 10.5                                       # extension 0.5 m above at the seam
    stats = module.seam_statistics(merged, base, ~np.isnan(base[:-1, :-1]))
    assert stats['across_seam_abs_dz_10m_pairs']['median'] == pytest.approx(0.5)
    assert stats['inside_original_abs_dz_10m_pairs']['median'] == pytest.approx(0.0)
    assert stats['across_seam_abs_dz_10m_pairs']['count'] == 6
