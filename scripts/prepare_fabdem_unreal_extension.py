"""Build a FABDEM-based Unreal terrain extension next to the installed Copernicus GLO-30 one (stage 2 of the open-data run).

Re-runs the extension recipe of `prepare_unreal_terrain_extension.py` (same Beirut 001 ground, same bounds, same seam
handling: median offset plus an exponentially faded edge correction) with FABDEM V1-2 in place of GLO-30, then applies
the municipal-Beirut clip the city map already had (`city_clip.Mask`: triangles whose centre is outside are dropped), so
the new mesh covers the same footprint as the installed extension.  Writes the GLB for Unreal, both clipped footprints,
and the seam / difference statistics.  Keep outputs outside Git.  FABDEM is CC BY-NC-SA 4.0.
"""
import argparse
import base64
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
from pyproj import Transformer

from dynacity.beirut001_pack import Beirut001Pack
from dynacity.engine_asset import export_glb
from dynacity.engine_city_surface import terrain_mesh
from dynacity.engine_street_assets import _colour_glb
from dynacity.terrain import GroundModel


def _merge_ground():
    path = Path(__file__).with_name('prepare_unreal_terrain_extension.py')
    spec = importlib.util.spec_from_file_location('prepare_unreal_terrain_extension', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.merge_ground


class Mask:
    """The 5 m municipal-Beirut inside mask in Unreal cm (same lookup as engine/unreal/Content/Python/city_clip.Mask)."""

    def __init__(self, path):
        data = json.loads(Path(path).read_text())
        self.x0, self.y0, self.cell = data['x0'], data['y0'], data['cell_cm']
        self.width, self.height = data['width'], data['height']
        self.bits = np.unpackbits(np.frombuffer(base64.b64decode(data['mask_b64']), dtype=np.uint8))[:self.width * self.height].reshape(self.height, self.width)

    def inside(self, x, y):
        c = np.floor((x - self.x0) / self.cell).astype(int)
        r = np.floor((y - self.y0) / self.cell).astype(int)
        ok = (c >= 0) & (r >= 0) & (c < self.width) & (r < self.height)
        out = np.zeros(c.shape, dtype=bool)
        out[ok] = self.bits[r[ok], c[ok]] == 1
        return out


def extension_triangles(ground, original_cells, origin, mask):
    """Triangles of the extension (valid cells not in the original ground), clipped to the mask by triangle centre.

    Returns (mesh dict for export, boolean per-cell footprint of kept triangles, per-triangle plan area in m2).
    Triangle layout matches `terrain_mesh`: per valid cell, (a, e, b) then (b, e, d).
    """
    z = ground.ground
    valid = np.isfinite(z)
    good = valid[:-1, :-1] & valid[1:, :-1] & valid[:-1, 1:] & valid[1:, 1:]
    mesh = terrain_mesh(ground, origin)
    tris = np.asarray(mesh['triangles'])
    r, c = np.nonzero(good)
    cell = ground.cell_m
    keep_cell = ~original_cells[good]
    # Triangle centres in metres (EPSG:32636), then Unreal cm about the origin (X east, Y south).
    xa, ya = ground.x0 + (c + .5) * cell, ground.y0 - (r + .5) * cell
    xb, yb = xa + cell, ya
    xe, ye = xa, ya - cell
    xd, yd = xa + cell, ya - cell
    c1x, c1y = (xa + xe + xb) / 3, (ya + ye + yb) / 3
    c2x, c2y = (xb + xe + xd) / 3, (yb + ye + yd) / 3
    ux = lambda x: (x - origin[0]) * 100.0
    uy = lambda y: -(y - origin[1]) * 100.0
    inside1 = mask.inside(ux(c1x), uy(c1y))
    inside2 = mask.inside(ux(c2x), uy(c2y))
    keep = np.empty(len(tris), dtype=bool)
    keep[0::2] = keep_cell & inside1
    keep[1::2] = keep_cell & inside2
    area = np.full(len(tris), cell * cell / 2.0)
    footprint = np.zeros(original_cells.shape, dtype=bool)
    footprint[r, c] = (keep_cell & (inside1 | inside2))
    mesh['triangles'] = tris[keep].tolist()
    return mesh, footprint, float(area[keep].sum()), int(keep.sum())


def seam_statistics(merged, base, original_cells):
    """|dz| between 4-neighbour samples across the Beirut 001 seam versus inside the original ground (10 m spacing)."""
    known = np.isfinite(base)
    z = merged
    pairs_seam, pairs_inside = [], []
    for dr, dc in ((0, 1), (1, 0)):
        a = z[:z.shape[0] - dr, :z.shape[1] - dc]
        b = z[dr:, dc:]
        ka = known[:known.shape[0] - dr, :known.shape[1] - dc]
        kb = known[dr:, dc:]
        both = np.isfinite(a) & np.isfinite(b)
        d = np.abs(a - b)
        pairs_seam.append(d[both & (ka != kb)])
        pairs_inside.append(d[both & ka & kb])
    seam, inside = np.concatenate(pairs_seam), np.concatenate(pairs_inside)
    q = lambda v: {'count': int(len(v)), 'median': float(np.median(v)), 'p90': float(np.percentile(v, 90)), 'max': float(v.max())}
    return {'across_seam_abs_dz_10m_pairs': q(seam), 'inside_original_abs_dz_10m_pairs': q(inside)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pack', required=True)
    p.add_argument('--glo-merged', required=True, help='installed extension ground (extended-visual-ground.npz)')
    p.add_argument('--glo-fallback', required=True, help='copernicus-visual-ground.npz')
    p.add_argument('--fabdem', required=True, help='terrain-fabdem-roads.npz')
    p.add_argument('--mask', required=True, help='beirut-clip/mask.json')
    p.add_argument('--city', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    out = Path(args.output).resolve()
    if out.is_relative_to(Path(__file__).resolve().parents[1]):
        p.error('Keep terrain assets outside Git')
    out.mkdir(parents=True, exist_ok=True)
    merge_ground = _merge_ground()
    origin = json.loads(Path(args.city).read_text(encoding='utf-8'))['origin_utm_m']
    primary = Beirut001Pack.load(args.pack).ground
    mask = Mask(args.mask)
    glo_installed = GroundModel.load(args.glo_merged)
    # The installed extension's bounds: recover them from its lattice so both merges share one grid.
    # merge_ground pads each side by 2 cells; the installed grid reaches the Beirut 001 north-west corner exactly.
    rows, cols = glo_installed.ground.shape
    cell = glo_installed.cell_m
    bounds = [glo_installed.x0 + 2.5 * cell, glo_installed.y0 - (rows - 2) * cell,
              glo_installed.x0 + (cols - 2) * cell, glo_installed.y0 - 2.5 * cell]
    glo_fallback = GroundModel.load(args.glo_fallback)
    fabdem = GroundModel.load(args.fabdem)
    glo_merged, glo_original, glo_report = merge_ground(primary, glo_fallback, bounds)
    fab_merged, fab_original, fab_report = merge_ground(primary, fabdem, bounds)
    same_grid = (glo_merged.ground.shape == fab_merged.ground.shape and glo_merged.x0 == fab_merged.x0 and glo_merged.y0 == fab_merged.y0)
    base = np.full(fab_merged.ground.shape, np.nan)
    c0 = int(round((primary.x0 - fab_merged.x0) / fab_merged.cell_m))
    r0 = int(round((fab_merged.y0 - primary.y0) / fab_merged.cell_m))
    base[r0:r0 + primary.ground.shape[0], c0:c0 + primary.ground.shape[1]] = primary.ground
    glo_mesh, glo_foot, glo_area, glo_tris = extension_triangles(glo_merged, glo_original, origin, mask)
    fab_mesh, fab_foot, fab_area, fab_tris = extension_triangles(fab_merged, fab_original, origin, mask)
    # Elevation difference over the clipped footprint cells (FABDEM merged minus GLO-30 merged).
    diff = (fab_merged.ground - glo_merged.ground)
    dcell = diff[:-1, :-1]  # a footprint cell is represented by its north-west vertex
    cells = glo_foot & np.isfinite(dcell)
    unclipped = (~base_known(base)) & np.isfinite(diff)
    stats = {
        'grid_shared': bool(same_grid), 'grid': {'x0': fab_merged.x0, 'y0': fab_merged.y0, 'cell_m': fab_merged.cell_m, 'shape': list(fab_merged.ground.shape)},
        'installed_glo_grid_matches_recomputed': bool(np.allclose(np.nan_to_num(glo_installed.ground, nan=-9999), np.nan_to_num(glo_merged.ground, nan=-9999), atol=1e-6)) if glo_installed.ground.shape == glo_merged.ground.shape else False,
        'glo30_clipped_extension': {'triangles': glo_tris, 'plan_area_m2': glo_area},
        'fabdem_clipped_extension': {'triangles': fab_tris, 'plan_area_m2': fab_area},
        'alignment': {'glo30': {k: glo_report[k] for k in ('median_alignment_offset_m', 'overlap_absolute_residual_p90_m', 'overlap_samples')},
                      'fabdem': {k: fab_report[k] for k in ('median_alignment_offset_m', 'overlap_absolute_residual_p90_m', 'overlap_samples')}},
        'seam': {'glo30': seam_statistics(glo_merged.ground, base, glo_original), 'fabdem': seam_statistics(fab_merged.ground, base, fab_original)},
        'fabdem_minus_glo30_m_over_installed_footprint': _q(dcell[cells]) if cells.any() else None,
        'fabdem_minus_glo30_m_over_whole_unclipped_extension': _q(diff[unclipped]) if unclipped.any() else None,
        'unclipped_extension_cells': int(unclipped.sum()), 'installed_footprint_cells': int(glo_foot.sum()),
    }
    fab_merged.save(out / 'extended-visual-ground-fabdem.npz')
    np.savez_compressed(out / 'footprints.npz', glo=glo_foot, fabdem=fab_foot)
    fab_mesh['id'] = 'StreetCover_TerrainExtensionFabdem'
    fab_mesh['metadata'].update({'source': 'FABDEM V1-2 (CC BY-NC-SA 4.0), extension only; Beirut 001 ground preserved', 'clip': 'municipal Beirut mask, triangle centre'})
    path = out / 'StreetCover_TerrainExtensionFabdem.glb'
    export_glb({'coordinates': {'origin_utm_m': origin}, 'meshes': [fab_mesh], 'trees': [], 'roads': [], 'bounds_lonlat': [],
                'limitations': ['Visual terrain estimate; FABDEM CC BY-NC-SA 4.0; alignment to the Beirut 001 datum inferred']}, path, include_calibration=False)
    _colour_glb(path, (.49, .48, .43))
    stats.update(asset=str(path), triangles=len(fab_mesh['triangles']), origin_utm_m=origin)
    (out / 'fabdem-extension.json').write_text(json.dumps(stats, indent=2), encoding='utf-8')
    print(json.dumps(stats, indent=2))


def base_known(base):
    return np.isfinite(base)


def _q(v):
    return {'cells': int(len(v)), 'median': float(np.median(v)), 'mean': float(np.mean(v)), 'p10': float(np.percentile(v, 10)),
            'p90': float(np.percentile(v, 90)), 'max': float(v.max()), 'min': float(v.min())}


if __name__ == '__main__':
    main()
