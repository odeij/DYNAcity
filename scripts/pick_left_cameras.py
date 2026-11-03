"""Pick EXTERIOR cameras for the left block with footprint and line-of-sight checks (no camera inside any building).

python scripts/pick_left_cameras.py <corridor-manifest-v2.json> <camera-registration.json> <out.json>
A candidate position is rejected if it lies inside (or within 1.5 m of) any source building footprint, or if the straight segment to
any of 5 target points on the block (both roof corners, facade mid at 4 m and 12 m, end-wall mid) passes through another source
building below its roof. The best 'street' view faces the facade; the best 'corner' view stands beyond the right end so the end wall,
facade and roof are all in line of sight. Native ground height is checked separately before capture.
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visible_walls as vw  # noqa: E402

CELL = 0.5


def main(man_path, reg_path, out_path):
    man = json.loads(Path(man_path).read_text()); blk = man['estimated_blocks'][0]
    a, b = np.array(blk['a_local_m']), np.array(blk['b_local_m']); t = (b - a) / np.linalg.norm(b - a); n = np.array([t[1], -t[0]])
    z0, H, D = blk['ground_z_local_m'], blk['height_m'], blk['plan_depth_used_m']
    views, walls, _, _, _ = vw.raster(reg_path)
    rings = {}
    for w in walls:
        rings.setdefault(w['sid'], {'pts': [], 'top': w['z1']})['pts'].append(w['a'])
    mid = (a + b) / 2
    o0 = mid - 90.
    shape = (int(180 / CELL), int(180 / CELL))
    top = np.zeros(shape, np.float32)  # roof z of the highest source building covering each cell (0 = none)
    order = sorted(rings.items(), key=lambda kv: kv[1]['top'])
    for sid, r in order:
        if len(r['pts']) < 3:
            continue
        m = np.zeros(shape, np.uint8); cv2.fillPoly(m, [np.round((np.array(r['pts']) - o0) / CELL).astype(np.int32)], 1)
        top[m.astype(bool)] = np.maximum(top[m.astype(bool)], r['top'])
    near = cv2.dilate((top > 0).astype(np.uint8), np.ones((int(3 / CELL) | 1, int(3 / CELL) | 1), np.uint8)).astype(bool)  # 1.5 m margin
    # the block itself (and the source volume it overlaps) must not count as an occluder or an obstruction
    own = np.zeros(shape, np.uint8); cv2.fillPoly(own, [np.round((np.array([a, b, b - n * D, a - n * D]) - o0) / CELL).astype(np.int32)], 1)
    own = cv2.dilate(own, np.ones((5, 5), np.uint8)).astype(bool)
    cand_src = blk['candidate_source_id']
    if cand_src in rings and len(rings[cand_src]['pts']) >= 3:
        m = np.zeros(shape, np.uint8); cv2.fillPoly(m, [np.round((np.array(rings[cand_src]['pts']) - o0) / CELL).astype(np.int32)], 1); own |= m.astype(bool)
    ground = z0

    def cell(p):
        return int((p[1] - o0[1]) / CELL), int((p[0] - o0[0]) / CELL)

    def blocked(cam, tgt):
        for s in np.linspace(0.02, 0.96, 160):
            p = cam + (tgt - cam) * s
            i, j = cell(p[:2])
            if 0 <= i < shape[0] and 0 <= j < shape[1] and not own[i, j] and top[i, j] > 0 and p[2] < top[i, j]:
                return True
        return False

    targets = {'roof_a': np.array([*a, z0 + H]), 'roof_b': np.array([*b, z0 + H]), 'facade_mid_4m': np.array([*mid, z0 + 4.]), 'facade_mid_12m': np.array([*mid, z0 + 12.]),
               'end_wall_mid': np.array([*(b - n * D / 2), z0 + 10.])}
    best = {'street': (-1, None), 'corner': (-1, None)}
    for d in np.arange(16., 60., 2.):
        for s in np.arange(-40., 60., 2.):
            for h in (1.7, 6., 12.):
                cam_xy = mid + n * d + t * s
                i, j = cell(cam_xy)
                if not (0 <= i < shape[0] and 0 <= j < shape[1]) or near[i, j] or top[i, j] > 0:
                    continue
                cam = np.array([*cam_xy, ground + h])
                vis = [k for k, T in targets.items() if not blocked(cam, T)]
                if len(vis) < 4:
                    continue
                to = mid - cam_xy; to /= np.linalg.norm(to)
                facing = float(to @ -n)  # 1 = looking straight at the facade
                kind = 'street' if abs(s) < 14 else 'corner'
                beyond_b = float((cam_xy - b) @ t)
                if kind == 'corner' and beyond_b < 8:
                    continue
                score = len(vis) * 10 + (facing * 5 if kind == 'street' else (1 - abs(facing - 0.6)) * 5) - abs(d - 30) * 0.05 - (0 if h == 1.7 else 0.5)
                if score > best[kind][0]:
                    best[kind] = (score, {'cam_local_m': cam.round(2).tolist(), 'visible_targets': vis, 'facing_cos': round(facing, 2), 'offset_normal_m': float(d), 'offset_along_m': float(s), 'eye_height_above_source_ground_m': h})
    poses = {}
    for kind, (sc, info) in best.items():
        if info is None:
            poses[kind] = None; continue
        cam = np.array(info['cam_local_m']); tgt = np.array([*(mid if kind == 'street' else (a + b) / 2 + (b - a) * 0.0), z0 + (9. if kind == 'street' else 9.)])
        dv = tgt - cam
        info['look_at_local_m'] = tgt.round(2).tolist()
        info['ue_location_cm'] = [cam[0] * 100, -cam[1] * 100, cam[2] * 100]
        info['ue_rotation_pitch_yaw_roll'] = [math.degrees(math.atan2(dv[2], math.hypot(dv[0], dv[1]))), math.degrees(math.atan2(-dv[1], dv[0])), 0.]
        info['score'] = round(sc, 2)
        poses[kind] = info
    Path(out_path).write_text(json.dumps({'schema': 'dynacity-left-cameras/1', 'ground_z_assumed_local_m': ground, 'targets': {k: v.round(2).tolist() for k, v in targets.items()}, 'poses': poses,
        'checks': 'footprint raster (1.5 m margin) + 160-sample line of sight against every source building below its roof; native ground height must still be checked'}, indent=1))
    for k, v in poses.items():
        print(k, None if v is None else (v['cam_local_m'], v['visible_targets'], v['facing_cos'], v['offset_normal_m'], v['offset_along_m'], v['eye_height_above_source_ground_m']))


if __name__ == '__main__':
    main(*sys.argv[1:4])
