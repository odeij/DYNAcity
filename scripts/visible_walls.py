"""Which source walls are actually visible in each photo pose? Depth-ordered raster of every nearby building wall.

python scripts/visible_walls.py <camera-registration.json> <out_dir>
For each pose: walls (ring edges) of every building within range are clipped to the near plane, projected through the
fitted camera, back-face culled and painted far-to-near into an id map. Result: visible pixels per (building, wall) per
pose, plus a colour overlay. Occlusion by trees/cars is NOT modelled (they hide wall area in the photos); the fitted
camera is an uncalibrated estimate and footprints carry ~5 m position error, so this ranks candidates, it does not identify.
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'dynacity'))
import corridor_reconstruction as cr  # noqa: E402
import glb_geometry as g  # noqa: E402

BATCHES = Path('C:/private/dynacity-data/engine/city-batches')
PHOTOS = Path('C:/private/dynacity-data/backups/beirut-evidence/engine/appearance-evidence-20261030/street-photos')
MATCHED = Path('C:/private/dynacity-data/engine/title-menu/matched.json')
S = (1600, 1200)


def camera_space(pts, cam, yaw, pitch):
    p = np.asarray(pts, float) - cam
    y, q = math.radians(yaw), math.radians(pitch)
    fwd = np.array([math.sin(y) * math.cos(q), math.cos(y) * math.cos(q), math.sin(q)])
    right = np.array([math.cos(y), -math.sin(y), 0.])
    up = np.cross(right, fwd)
    return np.stack([p @ right, p @ up, p @ fwd], axis=1)


def clip_near(poly, near=0.3):
    out = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        ina, inb = a[2] >= near, b[2] >= near
        if ina:
            out.append(a)
        if ina != inb:
            t = (near - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
    return np.array(out)


def load_walls(origin_cams, max_dist):
    walls = []
    for glb in sorted(BATCHES.glob('buildings-*.glb')):
        index = json.loads(glb.with_suffix('.objects.json').read_text()); meshes = None
        for o in index['objects']:
            b = o['local_bounds_m']; c = np.array([(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2])
            if min(np.hypot(*(c - cam[:2])) for cam in origin_cams) > max_dist:
                continue
            meshes = meshes or g.mesh_primitives(glb)
            P, T = meshes[o['batch']]
            tri = T[o['first_triangle']:o['first_triangle'] + o['triangle_count']]
            used = np.unique(tri); remap = {int(v): i for i, v in enumerate(used)}
            try:
                ring = cr.building_ring(P[used], np.vectorize(remap.get)(tri))
            except ValueError:
                continue
            z0, z1 = float(P[used][:, 2].min()), float(P[used][:, 2].max())
            for i in range(len(ring)):
                a, bb = ring[i], ring[(i + 1) % len(ring)]
                if np.linalg.norm(bb - a) < 0.5:
                    continue
                walls.append({'sid': o['source_id'], 'edge': i, 'a': a, 'b': bb, 'z0': z0, 'z1': z1})
    return walls


def raster(reg_path, max_dist=260.):
    """(views, walls, idmaps, f, pitch): per-pose id maps of the nearest front-facing wall at each pixel (-1 = none)."""
    reg = json.loads(Path(reg_path).read_text())
    origin = json.loads(next(BATCHES.glob('buildings-remeil-*.objects.json')).read_text())['coordinates']['origin_utm_m']
    matched = json.loads(MATCHED.read_text())
    views = {}
    for pid, off in reg['fitted']['yaw_offset_deg'].items():
        meta = json.loads((PHOTOS / f'{pid}.json').read_text())
        views[pid] = (np.array([meta['camera_xy_m'][0] - origin[0], meta['camera_xy_m'][1] - origin[1], matched['m' + pid][0][2] / 100]), meta['heading_grid_deg'] + off)
    walls = load_walls([v[0] for v in views.values()], max_dist)
    f = (S[0] / 2) / math.tan(math.radians(reg['fitted']['hfov_deg']) / 2)
    pitch = reg['fitted']['pitch_deg']
    idmaps = {}
    for pid, (cam, yaw) in views.items():
        idmap = np.full(S[::-1], -1, np.int32)
        order = []
        for k, w in enumerate(walls):
            mid = (w['a'] + w['b']) / 2
            t = w['b'] - w['a']; n = np.array([t[1], -t[0]]); n /= np.linalg.norm(n)
            if n @ (cam[:2] - mid) > 0:
                order.append((-float(np.linalg.norm(mid - cam[:2])), k))
        for _, k in sorted(order):
            w = walls[k]
            quad = np.array([[*w['a'], w['z0']], [*w['b'], w['z0']], [*w['b'], w['z1']], [*w['a'], w['z1']]])
            cs = clip_near(camera_space(quad, cam, yaw, pitch))
            if len(cs) < 3:
                continue
            px = np.stack([S[0] / 2 + f * cs[:, 0] / cs[:, 2], S[1] / 2 - f * cs[:, 1] / cs[:, 2]], axis=1)
            if np.abs(px).max() > 1e5:
                continue
            cv2.fillConvexPoly(idmap, px.round().astype(np.int32), int(k))
        idmaps[pid] = idmap
    return views, walls, idmaps, f, pitch


def main(reg_path, out_dir):
    reg = json.loads(Path(reg_path).read_text()); out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    origin = json.loads(next(BATCHES.glob('buildings-remeil-*.objects.json')).read_text())['coordinates']['origin_utm_m']
    matched = json.loads(MATCHED.read_text())
    views = {}
    for pid, off in reg['fitted']['yaw_offset_deg'].items():
        meta = json.loads((PHOTOS / f'{pid}.json').read_text())
        views[pid] = (np.array([meta['camera_xy_m'][0] - origin[0], meta['camera_xy_m'][1] - origin[1], matched['m' + pid][0][2] / 100]), meta['heading_grid_deg'] + off)
    walls = load_walls([v[0] for v in views.values()], 260.)
    f = (S[0] / 2) / math.tan(math.radians(reg['fitted']['hfov_deg']) / 2)
    pitch = reg['fitted']['pitch_deg']
    table = {}
    rng = np.random.default_rng(5)
    colours = {}
    for pid, (cam, yaw) in views.items():
        idmap = np.full(S[::-1], -1, np.int32)
        order = []
        for k, w in enumerate(walls):
            mid = (w['a'] + w['b']) / 2
            t = w['b'] - w['a']; n = np.array([t[1], -t[0]]); n /= np.linalg.norm(n)
            if n @ (cam[:2] - mid) <= 0:
                continue
            order.append((-float(np.linalg.norm(mid - cam[:2])), k))
        for _, k in sorted(order):
            w = walls[k]
            quad = np.array([[*w['a'], w['z0']], [*w['b'], w['z0']], [*w['b'], w['z1']], [*w['a'], w['z1']]])
            cs = clip_near(camera_space(quad, cam, yaw, pitch))
            if len(cs) < 3:
                continue
            px = np.stack([S[0] / 2 + f * cs[:, 0] / cs[:, 2], S[1] / 2 - f * cs[:, 1] / cs[:, 2]], axis=1)
            if np.abs(px).max() > 1e5:
                continue
            cv2.fillConvexPoly(idmap, px.round().astype(np.int32), int(k))
        counts = np.bincount(idmap[idmap >= 0].ravel(), minlength=len(walls))
        for k in np.flatnonzero(counts):
            key = (walls[k]['sid'], walls[k]['edge'])
            table.setdefault(key, {})[pid] = int(counts[k])
        img = cv2.resize(cv2.imread(str(PHOTOS / f'{pid}.jpg')), S)
        layer = np.zeros_like(img)
        for k in np.flatnonzero(counts > 1500):
            col = colours.setdefault(walls[k]['sid'], tuple(int(x) for x in rng.integers(60, 255, 3)))
            layer[idmap == k] = col
        blend = cv2.addWeighted(img, 0.5, layer, 0.5, 0)
        for k in np.flatnonzero(counts > 6000):
            ys, xs = np.nonzero(idmap == k)
            cv2.putText(blend, walls[k]['sid'][10:14] + f".{walls[k]['edge']}", (int(xs.mean()) - 30, int(ys.mean())), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 2)
        cv2.imwrite(str(out / f'visible-walls-{pid}.jpg'), blend)
    rows = []
    for (sid, edge), v in table.items():
        w = next(x for x in walls if x['sid'] == sid and x['edge'] == edge)
        rows.append({'source_id': sid, 'edge': edge, 'length_m': round(float(np.linalg.norm(w['b'] - w['a'])), 2), 'height_m': round(w['z1'] - w['z0'], 1),
                     'visible_px': v, 'total_px': int(sum(v.values())), 'views_seen': len([1 for x in v.values() if x > 3000])})
    rows.sort(key=lambda r: -r['total_px'])
    (out / 'visible-walls.json').write_text(json.dumps({'schema': 'dynacity-visible-walls/1', 'camera_status': reg['verdict']['status'], 'frame_px': S, 'walls': rows}, indent=1))
    for r in rows[:22]:
        print(r['source_id'][10:18], 'edge', r['edge'], r['length_m'], 'm x', r['height_m'], 'total', r['total_px'], r['visible_px'])


if __name__ == '__main__':
    main(*sys.argv[1:3])
