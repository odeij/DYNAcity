"""Live seam and road-contact measurements for the Unreal terrain extensions (stage 2 of the open-data run).

Talks to the editor through the local MCP server (`OpenDataTools.terrain`).  With one extension active it samples:
  * extension surface heights over footprint cells, against the offline grid for that extension;
  * the seam: height step between a Beirut 001 ground sample and the neighbouring extension sample, against the natural
    10 m step inside the Beirut 001 ground;
  * road contact: where a road mesh lies over the extension footprint, the gap between the road and the extension below it.
Run once per active extension (`--state glo30`, `--state fabdem`); `--compare` combines the two result files.
All sampling is seeded; outputs stay outside Git.
"""
import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from unreal_mcp_client import call  # noqa: E402

TERRAIN_PREFIX = ['StreetCover_TerrainExtension']
GROUND_PREFIX = ['source-ground']


def terrain(url, action, **kwargs):
    result = call(url, 'tools/call', {'name': 'call_tool', 'arguments': {
        'toolset_name': 'open_data_tools.OpenDataTools', 'tool_name': 'terrain',
        'arguments': {'action': action, 'args_json': json.dumps(kwargs)}}}, timeout=900)
    envelope = json.loads(result['result']['content'][0]['text'])
    value = json.loads(envelope['returnValue'])
    if not value.get('ok'):
        raise RuntimeError(value)
    return value['result']


def traced(url, points, **kwargs):
    out = []
    for i in range(0, len(points), 150):
        out += terrain(url, 'trace', points_xy_cm=points[i:i + 150], **kwargs)
    return out


def cell_cm(grid, r, c, origin):
    x = grid.x0 + (c + .5) * grid.cell_m
    y = grid.y0 - (r + .5) * grid.cell_m
    return (x - origin[0]) * 100.0, -(y - origin[1]) * 100.0, x, y


def quantiles(v):
    v = np.asarray([x for x in v if x is not None and np.isfinite(x)], dtype=float)
    if not len(v):
        return {'count': 0}
    return {'count': int(len(v)), 'median': float(np.median(v)), 'mean': float(v.mean()), 'p10': float(np.percentile(v, 10)),
            'p90': float(np.percentile(v, 90)), 'min': float(v.min()), 'max': float(v.max())}


def measure(args):
    from scipy.ndimage import binary_dilation
    from dynacity.beirut001_pack import Beirut001Pack
    from dynacity.terrain import GroundModel
    out_dir = Path(args.work)
    origin = json.loads(Path(args.city).read_text(encoding='utf-8'))['origin_utm_m']
    grid_new = GroundModel.load(out_dir / 'extended-visual-ground-fabdem.npz')
    grid_old = GroundModel.load(args.glo_merged)
    footprint = np.load(out_dir / 'footprints.npz')['glo']
    pack = Beirut001Pack.load(args.pack).ground
    base = np.full(grid_new.ground.shape, np.nan)
    c0 = int(round((pack.x0 - grid_new.x0) / grid_new.cell_m))
    r0 = int(round((grid_new.y0 - pack.y0) / grid_new.cell_m))
    base[r0:r0 + pack.ground.shape[0], c0:c0 + pack.ground.shape[1]] = pack.ground
    known = np.isfinite(base)[:-1, :-1]
    rng = random.Random(20261121)
    rr, cc = np.nonzero(footprint)
    idx = list(range(len(rr)))
    sample = rng.sample(idx, min(args.samples, len(idx)))
    pts = [cell_cm(grid_new, rr[i], cc[i], origin) for i in sample]
    live = traced(args.url, [[p[0], p[1]] for p in pts], complex_=True, only_prefixes=TERRAIN_PREFIX)
    grid = grid_new if args.state == 'fabdem' else grid_old
    expect = grid.sample([p[2] for p in pts], [p[3] for p in pts])
    surface = []
    for (z, label), e in zip(live, expect):
        if z is not None and np.isfinite(e):
            surface.append(z / 100.0 - (e - origin[2]))
    result = {'state': args.state, 'sampled_footprint_cells': len(pts), 'surface_hits': sum(1 for z, _ in live if z is not None),
              'hit_actor_labels': sorted({lbl for _, lbl in live if lbl}), 'live_minus_grid_m': quantiles(surface)}
    # Seam: footprint cells touching known ground, paired with the touching known cell.
    seam_cells = footprint & binary_dilation(known, structure=np.ones((3, 3), dtype=bool))
    sr, sc = np.nonzero(seam_cells)
    chosen = rng.sample(range(len(sr)), min(args.samples, len(sr)))
    ext_pts, known_pts = [], []
    for i in chosen:
        r, c = sr[i], sc[i]
        nb = [(r + dr, c + dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr or dc) and 0 <= r + dr < known.shape[0] and 0 <= c + dc < known.shape[1] and known[r + dr, c + dc]]
        if not nb:
            continue
        kr, kc = min(nb, key=lambda t: (t[0] - r) ** 2 + (t[1] - c) ** 2)
        ext_pts.append(cell_cm(grid_new, r, c, origin)[:2])
        known_pts.append(cell_cm(grid_new, kr, kc, origin)[:2])
    ze = traced(args.url, ext_pts, complex_=True, only_prefixes=TERRAIN_PREFIX)
    zk = traced(args.url, known_pts, complex_=False, only_prefixes=GROUND_PREFIX)
    steps = [abs(a[0] - b[0]) / 100.0 for a, b in zip(ze, zk) if a[0] is not None and b[0] is not None]
    # Natural step: neighbouring known cells 10 m apart on the Beirut 001 ground.
    kr_, kc_ = np.nonzero(known)
    nat_a, nat_b = [], []
    for i in rng.sample(range(len(kr_)), min(args.samples, len(kr_))):
        r, c = kr_[i], kc_[i]
        if c + 1 < known.shape[1] and known[r, c + 1]:
            nat_a.append(cell_cm(grid_new, r, c, origin)[:2])
            nat_b.append(cell_cm(grid_new, r, c + 1, origin)[:2])
    za = traced(args.url, nat_a, complex_=False, only_prefixes=GROUND_PREFIX)
    zb = traced(args.url, nat_b, complex_=False, only_prefixes=GROUND_PREFIX)
    natural = [abs(a[0] - b[0]) / 100.0 for a, b in zip(za, zb) if a[0] is not None and b[0] is not None]
    result['seam_step_m'] = {'across_seam_abs_dz': quantiles(steps), 'natural_10m_abs_dz_on_beirut001': quantiles(natural)}
    # Road contact over every footprint cell: first hit from above is a road mesh?
    every = [[cell_cm(grid_new, r, c, origin)[0], cell_cm(grid_new, r, c, origin)[1]] for r, c in zip(rr, cc)]
    top = traced(args.url, every, complex_=False)
    road_idx = [i for i, (z, label) in enumerate(top) if label and label.startswith(('roads_', 'roads-', 'structures'))]
    under = traced(args.url, [every[i] for i in road_idx], complex_=True, only_prefixes=TERRAIN_PREFIX) if road_idx else []
    gaps = [(top[i][0] - u[0]) / 100.0 for i, u in zip(road_idx, under) if u[0] is not None]
    result['road_contact'] = {'footprint_cells_checked': len(every), 'cells_with_a_road_mesh_on_top': len(road_idx),
                              'road_minus_extension_below_m': quantiles(gaps), 'cells_where_road_is_below_extension': int(sum(1 for g in gaps if g < -0.05))}
    # Road contact at the seam: Beirut 001 cells within 30 m of the footprint whose top surface is a road; compare the road
    # with the extension surface at the nearest footprint cell (distance recorded). A consistent extension keeps this gap
    # within the natural 30 m terrain slope.
    from scipy.ndimage import distance_transform_edt
    dist = distance_transform_edt(~footprint, sampling=10.0)
    near = known & (dist <= 30.0)
    nr, nc = np.nonzero(near)
    near_pts = [[cell_cm(grid_new, r, c, origin)[0], cell_cm(grid_new, r, c, origin)[1]] for r, c in zip(nr, nc)]
    near_top = traced(args.url, near_pts, complex_=False)
    road_near = [i for i, (z, label) in enumerate(near_top) if label and label.startswith(('roads_', 'roads-'))]
    from scipy.ndimage import distance_transform_edt as edt
    _, nearest = edt(~footprint, sampling=10.0, return_indices=True)
    ext_at, gap_rows = [], []
    for i in road_near:
        r, c = nr[i], nc[i]
        fr, fc = nearest[0][r, c], nearest[1][r, c]
        ext_at.append(cell_cm(grid_new, fr, fc, origin)[:2])
        gap_rows.append((r, c, fr, fc))
    ext_z = traced(args.url, ext_at, complex_=True, only_prefixes=TERRAIN_PREFIX) if ext_at else []
    seam_gaps, seam_dist = [], []
    for i, (z, _lbl), (r, c, fr, fc) in zip(road_near, ext_z, gap_rows):
        if z is not None:
            seam_gaps.append((near_top[i][0] - z) / 100.0)
            seam_dist.append(float(np.hypot((r - fr) * 10.0, (c - fc) * 10.0)))
    result['road_contact']['roads_within_30m_of_extension'] = {
        'known_cells_within_30m': int(near.sum()), 'with_road_mesh_on_top': len(road_near), 'paired_with_extension_surface': len(seam_gaps),
        'road_minus_nearest_extension_surface_m': quantiles(seam_gaps), 'pair_distance_m': quantiles(seam_dist)}
    Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def compare(args):
    old = json.loads(Path(args.old).read_text())
    new = json.loads(Path(args.new).read_text())
    return {'glo30': old, 'fabdem': new}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:8016/mcp')
    p.add_argument('--state', choices=['glo30', 'fabdem'], required=True)
    p.add_argument('--work', required=True, help='folder written by prepare_fabdem_unreal_extension.py')
    p.add_argument('--glo-merged', required=True)
    p.add_argument('--pack', required=True)
    p.add_argument('--city', required=True)
    p.add_argument('--samples', type=int, default=400)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    print(json.dumps(measure(args), indent=2))


if __name__ == '__main__':
    main()
