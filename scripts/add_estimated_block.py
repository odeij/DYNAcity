"""Append a photo-fitted ESTIMATED block (additive geometry, no source triangles omitted) to a corridor manifest.

python scripts/add_estimated_block.py <manifest-v2.json> <map.json> <reference-profiles.json> <camera-registration.json> <out-manifest.json> [depth_m] [albedo_scale]

The facade corners and roof height come from the MAP fit (fit_left_block_map.py). The plan depth behind the facade is UNSEEN: it
is the requested depth shrunk until the footprint overlaps no other source building by more than a small area, and recorded.
Colour: median of the cream-family polygon pixels in the three fitted views (vegetation/sky filtered, gray-world balanced).
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visible_walls as vw  # noqa: E402
from wall_photo_measurements import linear_to_srgb8, srgb_to_linear  # noqa: E402

S = vw.S


def polygon_colour(profiles, photos=('1288770821', '1288770597', '1288770701')):
    cols = []
    for pid in photos:
        m = np.zeros(S[::-1], np.uint8)
        for ob in profiles['observations']:
            if ob['family'] == 'cream_yellow_older_balcony' and ob['photo_id'] == pid:
                cv2.fillPoly(m, [(np.array(ob['polygon_norm']) * S).astype(np.int32)], 1)
        img = cv2.resize(cv2.imread(str(vw.PHOTOS / f'{pid}.jpg')), S).astype(float)[:, :, ::-1]
        g = img.reshape(-1, 3).mean(0); bal = np.clip(img * (g.mean() / g), 0, 255)
        px = bal[m.astype(bool)]
        hsv = cv2.cvtColor(px[None].astype(np.uint8), cv2.COLOR_RGB2HSV)[0]
        keep = (px.mean(1) > 60) & (px.mean(1) < 235) & ~((hsv[:, 0] > 30) & (hsv[:, 0] < 85) & (hsv[:, 1] > 50)) & (hsv[:, 1] > 25)  # not sky/white, not foliage
        cols.append(srgb_to_linear(np.median(px[keep], axis=0)))
    lin = np.median(np.array(cols), axis=0)
    return linear_to_srgb8(lin).tolist()


def main(manifest_path, map_path, prof_path, reg_path, out_path, depth=12., scale=0.4):
    man = json.loads(Path(manifest_path).read_text()); mp = json.loads(Path(map_path).read_text()); prof = json.loads(Path(prof_path).read_text())
    _, walls, _, _, _ = vw.raster(reg_path)
    a, b = np.array(mp['map']['a_local_m']), np.array(mp['map']['b_local_m'])
    t = (b - a) / np.linalg.norm(b - a); n = np.array([t[1], -t[0]])
    z0 = mp['source_ground_z_local_m']; H = mp['map']['H_m']
    cand = mp['candidate']
    sid_full = next(w['sid'] for w in walls if w['sid'].split(':')[1].startswith(cand))
    rings = {}
    for w in walls:
        if w['sid'].split(':')[1].startswith(cand):
            continue
        rings.setdefault(w['sid'], []).append(w['a'])
    others = {sid: np.array(pts) for sid, pts in rings.items() if len(pts) >= 3}
    cell = 0.25
    o0 = np.minimum(a, b) - 45.

    def mask(poly):
        m = np.zeros((int(110 / cell), int(110 / cell)), np.uint8)
        cv2.fillPoly(m, [np.round((poly - o0) / cell).astype(np.int32)], 1)
        return m.astype(bool)

    others_mask = {sid: mask(p) for sid, p in others.items()}

    def overlaps(depth_m, min_area=0.5):
        fm = mask(np.array([a, b, b - n * depth_m, a - n * depth_m]))
        out = [(sid[10:18], round(float((fm & om).sum()) * cell * cell, 1)) for sid, om in others_mask.items() if (fm & om).any()]
        return [x for x in out if x[1] > min_area]

    used = depth
    while used > 4. and any(x[1] > 3.0 for x in overlaps(used)):
        used -= 1.0
    over = overlaps(used)
    block = {'key': 'cream_left', 'kind': 'estimated_block', 'family': 'cream_yellow_older_balcony', 'candidate_source_id': sid_full,
             'a_local_m': a.round(3).tolist(), 'b_local_m': b.round(3).tolist(), 'tangent': t.round(5).tolist(), 'normal': n.round(5).tolist(),
             'width_m': round(float(np.linalg.norm(b - a)), 2), 'ground_z_local_m': z0, 'height_m': H, 'height_p5_p95_m': mp['posterior']['H_m_p5_p50_p95'],
             'plan_depth_requested_m': depth, 'plan_depth_used_m': used, 'overlap_with_other_source_footprints_m2': over,
             'ground_floor_m': 3.2, 'albedo_target_srgb_balanced': polygon_colour(prof), 'albedo_scale': scale,
             'fit': {'map': mp['map'], 'posterior': mp['posterior'], 'priors': mp['priors'], 'source_edge': mp['source_edge_local_m']},
             'identity': 'candidate only: 8e705f22 is the nearest source footprint; its BBED edge orientation conflicts with the photos (rotation about -24 deg)',
             'source_geometry': 'additive: no source triangles of the candidate are omitted or moved; the original 5.7 m volume stays',
             'collision': 'none added; original collision unchanged', 'geometry_status': 'estimated from hand-read corners in 3 photos; uncalibrated camera; plan depth unseen'}
    man['estimated_blocks'] = [block]
    man['buildings'] = [x for x in man['buildings'] if x['key'] != 'cream_left']
    Path(out_path).write_text(json.dumps(man, indent=1))
    print('depth used', used, 'overlaps', over, 'colour', block['albedo_target_srgb_balanced'])


if __name__ == '__main__':
    args = sys.argv
    main(args[1], args[2], args[3], args[4], args[5], float(args[6]) if len(args) > 6 else 12., float(args[7]) if len(args) > 7 else 0.4)
