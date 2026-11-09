"""Ranked occlusion-ordered projection of left-block alternatives against the worker's cream polygons (views 1, 2, 4; view 3 held out).

python scripts/occlusion_rank_left.py <camera-registration.json> <corridor-manifest-v2.json> <reference-profiles.json> <corner-observations.json> <out_dir>
Alternatives: A e8c87655 at its source footprint (19.5 m), B 8e705f22 at its source footprint (19.5 m), C the photo-fitted estimated
block. Every other source building is drawn, depth-ordered, so occluders hide the block exactly as the model predicts. Reported per
view: polygon coverage, precision, and the pixel offset between the projected facade roof corners and the hand-read corners.
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triangulate_corners as tc  # noqa: E402
import visible_walls as vw  # noqa: E402

S = vw.S


def raster_with(walls_all, views, pitch, f, mine_prefix):
    maps = {}
    for pid, (cam, yaw) in views.items():
        idmap = np.full(S[::-1], -1, np.int32); order = []
        for k, w in enumerate(walls_all):
            mid = (w['a'] + w['b']) / 2; t = w['b'] - w['a']; n = np.array([t[1], -t[0]]); n /= np.linalg.norm(n)
            if n @ (cam[:2] - mid) > 0:
                order.append((-float(np.linalg.norm(mid - cam[:2])), k))
        for _, k in sorted(order):
            w = walls_all[k]
            quad = np.array([[*w['a'], w['z0']], [*w['b'], w['z0']], [*w['b'], w['z1']], [*w['a'], w['z1']]])
            cs = vw.clip_near(vw.camera_space(quad, cam, yaw, pitch))
            if len(cs) < 3:
                continue
            px = np.stack([S[0] / 2 + f * cs[:, 0] / cs[:, 2], S[1] / 2 - f * cs[:, 1] / cs[:, 2]], axis=1)
            if np.abs(px).max() > 1e5:
                continue
            cv2.fillConvexPoly(idmap, px.round().astype(np.int32), int(k))
        maps[pid] = idmap
    return maps


def main(reg_path, man_path, prof_path, obs_path, out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    reg = json.loads(Path(reg_path).read_text()); man = json.loads(Path(man_path).read_text()); prof = json.loads(Path(prof_path).read_text()); obs = json.loads(Path(obs_path).read_text())
    views, walls, _, f, pitch = vw.raster(reg_path); hfov = reg['fitted']['hfov_deg']
    views_t = tc.load_views(reg)
    poly = {}
    for ob in prof['observations']:
        if ob['family'] == 'cream_yellow_older_balcony':
            m = poly.setdefault(ob['photo_id'], np.zeros(S[::-1], np.uint8)); cv2.fillPoly(m, [(np.array(ob['polygon_norm']) * S).astype(np.int32)], 1)
    blk = man['estimated_blocks'][0]
    z0 = blk['ground_z_local_m']
    alts = {}
    for key, prefix, edge in (('A_e8c87655_source', 'e8c87655', None), ('B_8e705f22_source', '8e705f22', None)):
        ws = sorted([w for w in walls if w['sid'].split(':')[1].startswith(prefix)], key=lambda w: w['edge'])
        alts[key] = {'drop': prefix, 'walls': [{'sid': 'CAND', 'edge': w['edge'], 'a': w['a'], 'b': w['b'], 'z0': w['z0'], 'z1': w['z0'] + 19.5} for w in ws],
                     'corners': (ws[5 if prefix == 'e8c87655' else 0]['a'], ws[5 if prefix == 'e8c87655' else 0]['b'], ws[0]['z0'] + 19.5)}
    a, b = np.array(blk['a_local_m']), np.array(blk['b_local_m']); n = np.array([(b - a)[1], -(b - a)[0]]); n /= np.linalg.norm(n)
    ring = [a, b, b - n * blk['plan_depth_used_m'], a - n * blk['plan_depth_used_m']]
    alts['C_photo_fitted_block'] = {'drop': blk['candidate_source_id'].split(':')[1][:8] + '__none__',
        'walls': [{'sid': 'CAND', 'edge': i, 'a': ring[i], 'b': ring[(i + 1) % 4], 'z0': z0, 'z1': z0 + blk['height_m']} for i in range(4)], 'corners': (a, b, z0 + blk['height_m'])}
    rows = {}
    for key, alt in alts.items():
        others = [w for w in walls if not w['sid'].split(':')[1].startswith(alt['drop'])] if not key.startswith('C') else list(walls)
        allw = others + alt['walls']
        maps = raster_with(allw, views, pitch, f, 'CAND')
        mine = [k for k, w in enumerate(allw) if w['sid'] == 'CAND']
        rows[key] = {'views': {}}
        a_c, b_c, zc = alt['corners']
        for pid, idmap in maps.items():
            vis = np.isin(idmap, mine); pm = poly.get(pid, np.zeros(S[::-1], np.uint8)).astype(bool); inter = int((vis & pm).sum())
            row = {'coverage_of_polygon': round(inter / max(int(pm.sum()), 1), 3), 'precision': round(inter / max(int(vis.sum()), 1), 3), 'visible_px': int(vis.sum())}
            res = {}
            for nm, X in (('roof_left_cap', a_c), ('wall_top_right', b_c)):
                if pid in obs['corners'][nm]:
                    pr = tc.project(np.array([X[0], X[1], zc]), views_t[pid], pitch, hfov)
                    res[nm] = None if pr is None else (pr - np.array(obs['corners'][nm][pid])).round(0).tolist()
            row['corner_offset_px_(projected-photo)'] = res
            if pid in obs.get('held_out_views', []):
                row['HELD_OUT'] = True
            rows[key]['views'][pid] = row
            img = cv2.resize(cv2.imread(str(vw.PHOTOS / f'{pid}.jpg')), S)
            lay = np.zeros_like(img); lay[vis] = (60, 255, 60)
            bl = cv2.addWeighted(img, .6, lay, .4, 0)
            cnts, _ = cv2.findContours(poly.get(pid, np.zeros(S[::-1], np.uint8)), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE); cv2.drawContours(bl, cnts, -1, (0, 255, 255), 3)
            for nm, per in obs['corners'].items():
                if pid in per:
                    cv2.drawMarker(bl, tuple(int(x) for x in per[pid]), (0, 0, 255), cv2.MARKER_CROSS, 26, 3)
            cv2.putText(bl, key + (' HELD OUT' if pid in obs.get('held_out_views', []) else ''), (14, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            cv2.imwrite(str(out / f'rank-{key}-{pid}.jpg'), bl)
        print(key, {k[-4:]: (v['coverage_of_polygon'], v['precision'], v.get('corner_offset_px_(projected-photo)')) for k, v in rows[key]['views'].items()})
    Path(out / 'occlusion-ranking.json').write_text(json.dumps({'schema': 'dynacity-left-occlusion-ranking/1', 'camera_status': reg['verdict']['status'], 'alternatives': rows,
        'limits': ['polygons and corners hand-read', 'uncalibrated camera', 'trees/cars not modelled (they hide wall in the photos)']}, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:6])
