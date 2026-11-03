"""Cameras for the end-wall / roof proof: same corner pose, two nearby exterior offsets, two elevated look-downs above the roof.

python scripts/pick_iteration5_cameras.py <corridor-manifest-v2.json> <camera-registration.json> <left-cameras.json> <out.json>
Every position is checked: outside every source footprint (1.5 m margin) or above the highest building under it (elevated views),
and a 160-sample line of sight to the block targets that does not pass through another source building below its roof.
Native ground height must still be added (170 cm eye height for the ground cameras).
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


def main(man_path, reg_path, cams_path, out_path):
    man = json.loads(Path(man_path).read_text()); blk = man['estimated_blocks'][0]
    base = json.loads(Path(cams_path).read_text())['poses']['corner']
    a, b = np.array(blk['a_local_m']), np.array(blk['b_local_m']); t = (b - a) / np.linalg.norm(b - a); n = np.array([t[1], -t[0]])
    z0, H, D = blk['ground_z_local_m'], blk['height_m'], blk['plan_depth_used_m']
    roof_z = z0 + H
    views, walls, _, _, _ = vw.raster(reg_path)
    rings = {}
    for w in walls:
        rings.setdefault(w['sid'], {'pts': [], 'top': w['z1']})['pts'].append(w['a'])
    mid = (a + b) / 2
    o0 = mid - 120.
    shape = (int(240 / CELL), int(240 / CELL))
    top = np.zeros(shape, np.float32)
    for sid, r in sorted(rings.items(), key=lambda kv: kv[1]['top']):
        if len(r['pts']) >= 3:
            m = np.zeros(shape, np.uint8); cv2.fillPoly(m, [np.round((np.array(r['pts']) - o0) / CELL).astype(np.int32)], 1)
            top[m.astype(bool)] = np.maximum(top[m.astype(bool)], r['top'])
    own = np.zeros(shape, np.uint8); cv2.fillPoly(own, [np.round((np.array([a, b, b - n * D, a - n * D]) - o0) / CELL).astype(np.int32)], 1)
    own = cv2.dilate(own, np.ones((5, 5), np.uint8)).astype(bool)
    cand = blk['candidate_source_id']
    if cand in rings and len(rings[cand]['pts']) >= 3:
        m = np.zeros(shape, np.uint8); cv2.fillPoly(m, [np.round((np.array(rings[cand]['pts']) - o0) / CELL).astype(np.int32)], 1); own |= m.astype(bool)
    near = cv2.dilate((top > 0).astype(np.uint8), np.ones((int(3 / CELL) | 1, int(3 / CELL) | 1), np.uint8)).astype(bool)

    def cell(p):
        return int((p[1] - o0[1]) / CELL), int((p[0] - o0[0]) / CELL)

    def inside_footprint(xy):
        i, j = cell(xy)
        return not (0 <= i < shape[0] and 0 <= j < shape[1]) or near[i, j] or top[i, j] > 0

    def blocked(cam, tgt):
        for s in np.linspace(0.02, 0.96, 200):
            p = cam + (tgt - cam) * s
            i, j = cell(p[:2])
            if 0 <= i < shape[0] and 0 <= j < shape[1] and not own[i, j] and top[i, j] > 0 and p[2] < top[i, j]:
                return True
        return False

    end_mid = b - n * D / 2
    targets = {'roof_b': np.array([*b, roof_z]), 'roof_far_corner': np.array([*(b - n * D), roof_z]), 'end_wall_mid_5m': np.array([*end_mid, z0 + 5.]),
               'end_wall_mid_14m': np.array([*end_mid, z0 + 14.]), 'end_wall_top': np.array([*end_mid, roof_z - 0.5])}
    corner0 = np.array(base['cam_local_m'])
    look = np.array(base['look_at_local_m'])

    def pose(cam, tgt):
        d = tgt - cam
        return {'cam_local_m': cam.round(2).tolist(), 'look_at_local_m': np.array(tgt).round(2).tolist(),
                'ue_location_cm': [cam[0] * 100, -cam[1] * 100, cam[2] * 100],
                'ue_rotation_pitch_yaw_roll': [math.degrees(math.atan2(d[2], math.hypot(d[0], d[1]))), math.degrees(math.atan2(-d[1], d[0])), 0.]}

    out = {'corner_same': {**pose(corner0, look), 'note': 'same pose as iteration 4 left_corner'}}
    ground_cam = []
    for name, ang in (('corner_off_a', -9.), ('corner_off_b', 9.)):
        th = math.radians(ang)
        Rm = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        found = None
        for scale in (1.0, 0.92, 1.08, 0.85):
            xy = b + Rm @ ((corner0[:2] - b) * scale)
            cam = np.array([*xy, corner0[2]])
            if inside_footprint(xy):
                continue
            vis = [k for k, T in targets.items() if not blocked(cam, T)]
            if len(vis) >= 4:
                found = (cam, vis); break
        out[name] = None if found is None else {**pose(found[0], look), 'visible_targets': found[1], 'angle_about_corner_deg': ang}
    # elevated: above the roof, outside the block, not inside any building below its top
    for name, direction in (('elevated_street_side', n), ('elevated_end_side', t)):
        best = None
        for dist in (22., 28., 34., 40.):
            for h in (18., 24., 30.):
                xy = (b - n * D / 2 if name == 'elevated_end_side' else mid - n * D / 2) + direction * (dist + (W := 0))
                cam = np.array([*xy, roof_z + h])
                i, j = cell(xy)
                if 0 <= i < shape[0] and 0 <= j < shape[1] and top[i, j] > cam[2] - 3:
                    continue
                vis = [k for k, T in targets.items() if not blocked(cam, T)]
                if len(vis) >= 4:
                    best = (cam, vis, dist, h); break
            if best:
                break
        roof_centre = np.array([*(mid - n * D / 2), roof_z])
        out[name] = None if best is None else {**pose(best[0], roof_centre), 'visible_targets': best[1], 'height_above_roof_m': best[3], 'distance_m': best[2]}
    ground_cam = [k for k in ('corner_off_a', 'corner_off_b') if out.get(k)]
    Path(out_path).write_text(json.dumps({'schema': 'dynacity-iteration5-cameras/1', 'roof_z_local_m': roof_z, 'targets': {k: v.round(2).tolist() for k, v in targets.items()},
                                          'poses': out, 'ground_cameras': ground_cam, 'checks': 'footprint raster + 200-sample line of sight; elevated cameras clear the tallest building under them by 3 m'}, indent=1))
    for k, v in out.items():
        print(k, None if v is None else (v['cam_local_m'], v.get('visible_targets')))


if __name__ == '__main__':
    main(*sys.argv[1:5])
