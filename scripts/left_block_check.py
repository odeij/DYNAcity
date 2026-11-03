"""Is the left cream/yellow block explained by a source candidate? Implied distance vs candidate distance.

python scripts/left_block_check.py <camera-registration.json> <reference-profiles.json> <out.json> [roof_lo roof_hi]
The family polygon's top edge is the real block's roofline (hand-read by the profile worker, about +-30 px). For an assumed
roof height above the road (5 storeys plus a stone base: 19-23 m, an ESTIMATE) the ray through that pixel gives the block's
horizontal distance from the camera. Source candidates are then compared on distance. A large mismatch means the candidate
footprint is misplaced (Beirut 001 is registered by an inferred rigid fit) or is a different building; it is evidence, not identity.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import estimate_roof_heights as eh  # noqa: E402
import visible_walls as vw  # noqa: E402

S = vw.S


def main(reg_path, prof_path, out_path, lo=19., hi=23.):
    reg = json.loads(Path(reg_path).read_text()); prof = json.loads(Path(prof_path).read_text())
    views, walls, idmaps, f, pitch = vw.raster(reg_path)
    hfov = reg['fitted']['hfov_deg']
    rows = []
    for pid, (cam, yaw) in views.items():
        pts = np.vstack([np.array(o['polygon_norm']) for o in prof['observations'] if o['family'] == 'cream_yellow_older_balcony' and o['photo_id'] == pid] or [np.zeros((0, 2))])
        if not len(pts):
            continue
        top = pts[np.argmin(pts[:, 1])]  # highest point of the annotated block silhouette
        d, _ = eh.ray(top[0], top[1], yaw, pitch, hfov)
        out = {'view': pid, 'roofline_pixel_norm': [round(float(top[0]), 3), round(float(top[1]), 3)], 'implied_distance_m': {}}
        for h in (lo, hi):
            z = cam[2] - 1.4 + h
            out['implied_distance_m'][str(h)] = round(float((z - cam[2]) / d[2] * np.hypot(d[0], d[1])), 1) if d[2] > 0 else None
        cand = {}
        for k, w in enumerate(walls):
            sid = w['sid'][10:18]
            if sid in ('c64c5523', '0c57adc2', 'b371ee82', 'bf8b70f2', '77bae857', '8e705f22', 'e8c87655'):
                dd = float(np.min([np.hypot(*(p - cam[:2])) for p in (w['a'], w['b'])]))
                cand[sid] = min(cand.get(sid, 1e9), dd)
        out['candidate_nearest_corner_distance_m'] = {k: round(v, 1) for k, v in sorted(cand.items(), key=lambda kv: kv[1])}
        rows.append(out)
        print(pid[-4:], out['roofline_pixel_norm'], out['implied_distance_m'], out['candidate_nearest_corner_distance_m'])
    Path(out_path).write_text(json.dumps({'schema': 'dynacity-left-block-check/1', 'assumption': f'roof {lo}-{hi} m above the road (estimate)', 'rows': rows,
        'camera_status': reg['verdict']['status'], 'limits': ['polygon is hand-read (+-30 px)', 'roof height assumed, not measured', 'lens distortion at the frame edge is not modelled']}, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:4], *[float(x) for x in sys.argv[4:6]])
