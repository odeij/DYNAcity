"""Joint footprint/frontage/corner fit of the left cream block across views 1, 2, 4 (view 3 held out), with occlusion order.

python scripts/fit_left_block.py <camera-registration.json> <corner-observations.json> <reference-profiles.json> <out_dir>

For a candidate source footprint (facade edge a->b, viewer's right = tangent) the block is described by a shift (de, dn), a rotation
about the facade midpoint and a roof height H above the source ground. Observed static corners (facade right roof corner = b,
facade left cap corner = a, hand read, +-12 px) are compared with the projected corners in all used views; each view's heading
offset is a nuisance with a Gaussian prior (0.5 deg, the scale of the camera-fit landmark residuals) solved in closed form.
Loss is Huber in pixels. The whole loss valley over (de, dn) is reported, because the cameras sit on one road line and the facade
is nearly parallel to it, so the position along the sight line is only weakly constrained: the result is a RANGE, not a survey.
Candidates are then ranked by an occlusion-ordered raster (all other buildings drawn in front/behind) against the worker's
cream polygons: coverage, precision and per-view roof-corner residual.
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visible_walls as vw  # noqa: E402
import triangulate_corners as tc  # noqa: E402

S = vw.S
SIG_PX, SIG_YAW_DEG = 12., 0.5
ROTS = (-18., -15., -12., -9., -6., -3., 0., 3., 6.)
HS = (15., 16.5, 18., 19.5, 21., 22.5, 24.)
# vertical edge of the facade's right end read at v~0.5 (u only; independent of height): view -> u pixel on 1600 wide
RIGHT_EDGE_U = {'1288770821': 318., '1288770597': 441., '1288770701': 391.}


def huber(r, k=2.0):
    a = np.abs(r)
    return np.where(a <= k, .5 * r * r, k * (a - .5 * k))


def project_pts(P, view, pitch, hfov):
    return np.array([tc.project(p, view, pitch, hfov) if tc.project(p, view, pitch, hfov) is not None else [np.nan, np.nan] for p in P])


def corner_loss(a, b, z, obs, views, pitch, hfov, f):
    """a=left cap corner, b=right roof corner (x, y); z roof height (local, absolute). Returns loss, per-view residual dict, yaw nuisance (deg)."""
    total, res, nuis = 0., {}, {}
    for v, per in obs.items():
        pts, pix = [], []
        for name, X in (('roof_left_cap', a), ('wall_top_right', b)):
            if v in per.get(name, {}):
                pts.append([X[0], X[1], z]); pix.append(per[name][v])
        if not pts:
            continue
        pr = project_pts(pts, views[v], pitch, hfov)
        if np.isnan(pr).any():
            return 1e9, {}, {}
        r = pr - np.array(pix, float)
        n = len(r)
        sigma_px_yaw = f * math.radians(SIG_YAW_DEG)
        delta = -r[:, 0].sum() / (n + (SIG_PX / sigma_px_yaw) ** 2)  # closed-form heading nuisance, in pixels of u
        r2 = r.copy(); r2[:, 0] += delta
        total += float(huber(r2 / SIG_PX).sum() + (delta / sigma_px_yaw) ** 2)
        if v in RIGHT_EDGE_U:
            pe = tc.project(np.array([b[0], b[1], z - 8.]), views[v], pitch, hfov)
            if pe is None:
                return 1e9, {}, {}
            re = pe[0] + delta - RIGHT_EDGE_U[v]
            total += float(huber(np.array([re / SIG_PX])).sum())
            r2 = np.vstack([r2, [re, 0.]])
        res[v] = r2.round(1).tolist(); nuis[v] = round(math.degrees(delta / f), 2)
    return total, res, nuis


def candidate_ring(walls, prefix):
    ws = sorted([w for w in walls if w['sid'].split(':')[1].startswith(prefix)], key=lambda w: w['edge'])
    return np.array([w['a'] for w in ws]), ws[0]['z0'], ws[0]['z1']


def main(reg_path, obs_path, prof_path, out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    reg = json.loads(Path(reg_path).read_text()); obs_all = json.loads(Path(obs_path).read_text()); prof = json.loads(Path(prof_path).read_text())
    views_t = tc.load_views(reg); pitch, hfov = reg['fitted']['pitch_deg'], reg['fitted']['hfov_deg']
    f = (S[0] / 2) / math.tan(math.radians(hfov) / 2)
    held = obs_all.get('held_out_views', [])
    corners = {k: {v: p for v, p in per.items() if v not in held} for k, per in obs_all['corners'].items()}
    obs = {}
    for v in views_t:
        if v in held:
            continue
        obs[v] = {k: per for k, per in corners.items() if v in per}
    views, walls, _, _, _ = vw.raster(reg_path)
    poly = {}
    for ob in prof['observations']:
        if ob['family'] == 'cream_yellow_older_balcony':
            m = poly.setdefault(ob['photo_id'], np.zeros(S[::-1], np.uint8)); cv2.fillPoly(m, [(np.array(ob['polygon_norm']) * S).astype(np.int32)], 1)
    candidates = {'e8c87655': 5, '8e705f22': 0}
    report = {'schema': 'dynacity-left-block-fit/1', 'camera_status': reg['verdict']['status'], 'observation_sigma_px': SIG_PX, 'yaw_prior_deg': SIG_YAW_DEG,
              'held_out_views': held, 'candidates': {}}
    for cand, edge in candidates.items():
        ring, z0, z1 = candidate_ring(walls, cand)
        a0, b0 = ring[edge], ring[(edge + 1) % len(ring)]
        mid = (a0 + b0) / 2
        grid = np.arange(-36., 36.1, 3.)
        best = (1e18, None)
        valley = np.full((len(grid), len(grid)), np.inf)
        for i, de in enumerate(grid):
            for j, dn in enumerate(grid):
                for rot in ROTS:
                    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                    R = np.array([[c, -s], [s, c]])
                    a = (R @ (a0 - mid)) + mid + [de, dn]; b = (R @ (b0 - mid)) + mid + [de, dn]
                    for H in HS:
                        L, res, nu = corner_loss(a, b, z0 + H, obs, views_t, pitch, hfov, f)
                        valley[i, j] = min(valley[i, j], L)
                        if L < best[0]:
                            best = (L, {'de': float(de), 'dn': float(dn), 'rot_deg': rot, 'H_m': H, 'residual_px_by_view': res, 'yaw_nuisance_deg': nu})
        L0, res0, nu0 = corner_loss(a0, b0, z0 + 19.5, obs, views_t, pitch, hfov, f)
        origin_best = (1e18, None)
        for rot in ROTS:
            c, s_ = math.cos(math.radians(rot)), math.sin(math.radians(rot)); R = np.array([[c, -s_], [s_, c]])
            a = (R @ (a0 - mid)) + mid; b = (R @ (b0 - mid)) + mid
            for H in HS:
                L, res, nu = corner_loss(a, b, z0 + H, obs, views_t, pitch, hfov, f)
                if L < origin_best[0]:
                    origin_best = (L, {'rot_deg': rot, 'H_m': H, 'residual_px_by_view': res})
        held_proj = {}
        for hv in held:
            for name, X in (('left_cap_a', a0), ('right_corner_b', b0)):
                pr = tc.project(np.array([X[0], X[1], z0 + 19.0]), views_t[hv], pitch, hfov)
                held_proj.setdefault(hv, {})[name] = None if pr is None else pr.round(0).tolist()
        thr = best[0] + 9.0  # roughly the 3-parameter 99% region
        ok = np.argwhere(valley <= thr)
        de_rng = [float(grid[ok[:, 0]].min()), float(grid[ok[:, 0]].max())]; dn_rng = [float(grid[ok[:, 1]].min()), float(grid[ok[:, 1]].max())]
        report['candidates'][cand] = {'facade_edge': edge, 'facade_length_m': round(float(np.linalg.norm(b0 - a0)), 2), 'source_height_m': round(float(z1 - z0), 2),
            'loss_at_source_position_H19.5': round(L0, 2), 'source_position_best_rot_H': {**origin_best[1], 'loss': round(origin_best[0], 2)}, 'held_out_projection_px_at_source_position': held_proj, 'residual_px_at_source_position': res0, 'best_fit': {**best[1], 'loss': round(best[0], 2)},
            'accepted_shift_range_m_(loss<=best+9)': {'de': de_rng, 'dn': dn_rng, 'cells': int(len(ok))},
            'source_position_inside_accepted_range': bool(origin_best[0] <= thr)}
        np.save(out / f'valley-{cand}.npy', valley)
        # heat map
        img = np.clip((np.log10(valley + 1) / np.log10(valley[np.isfinite(valley)].max() + 1) * 255), 0, 255).astype(np.uint8)
        img = cv2.resize(cv2.applyColorMap(255 - img, cv2.COLORMAP_VIRIDIS), None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST)
        c0 = len(grid) // 2 * 6 + 3
        cv2.drawMarker(img, (c0, c0), (255, 255, 255), cv2.MARKER_CROSS, 18, 2)
        cv2.putText(img, f'{cand}: x=east shift, y=north shift (-60..60 m), white cross = source', (6, 18), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
        cv2.imwrite(str(out / f'valley-{cand}.png'), img)
        print(cand, 'src loss', round(L0, 1), 'best', round(best[0], 1), best[1]['de'], best[1]['dn'], best[1]['rot_deg'], best[1]['H_m'], 'range', de_rng, dn_rng)
    # occlusion-ordered ranking: replace candidate walls by the shifted/raised block and rasterise against everything else
    ranking = []
    for cand, edge in candidates.items():
        for label, sh in (('source_position', None), ('best_fit', report['candidates'][cand]['best_fit'])):
            ring, z0, z1 = candidate_ring(walls, cand)
            H = 19.5 if sh is None else sh['H_m']
            if sh is not None:
                mid = ((ring[edge] + ring[(edge + 1) % len(ring)]) / 2)
                c, s = math.cos(math.radians(sh['rot_deg'])), math.sin(math.radians(sh['rot_deg'])); R = np.array([[c, -s], [s, c]])
                ring = (ring - mid) @ R.T + mid + [sh['de'], sh['dn']]
            others = [w for w in walls if not w['sid'].split(':')[1].startswith(cand)]
            mine = [{'sid': f'CAND-{cand}', 'edge': i, 'a': ring[i], 'b': ring[(i + 1) % len(ring)], 'z0': z0, 'z1': z0 + H} for i in range(len(ring))]
            allw = others + mine
            row = {'candidate': cand, 'placement': label, 'H_m': H, 'views': {}}
            for pid, (cam, yaw) in views.items():
                if pid not in poly:
                    continue
                idmap = np.full(S[::-1], -1, np.int32); order = []
                for k, w in enumerate(allw):
                    mid2 = (w['a'] + w['b']) / 2; t = w['b'] - w['a']; n = np.array([t[1], -t[0]]); n /= np.linalg.norm(n)
                    if n @ (cam[:2] - mid2) > 0:
                        order.append((-float(np.linalg.norm(mid2 - cam[:2])), k))
                for _, k in sorted(order):
                    w = allw[k]
                    quad = np.array([[*w['a'], w['z0']], [*w['b'], w['z0']], [*w['b'], w['z1']], [*w['a'], w['z1']]])
                    cs = vw.clip_near(vw.camera_space(quad, cam, yaw, pitch))
                    if len(cs) < 3:
                        continue
                    px = np.stack([S[0] / 2 + f * cs[:, 0] / cs[:, 2], S[1] / 2 - f * cs[:, 1] / cs[:, 2]], axis=1)
                    if np.abs(px).max() > 1e5:
                        continue
                    cv2.fillConvexPoly(idmap, px.round().astype(np.int32), int(k))
                mine_ids = [k for k, w in enumerate(allw) if w['sid'].startswith('CAND-')]
                vis = np.isin(idmap, mine_ids); pm = poly[pid].astype(bool)
                inter = int((vis & pm).sum())
                row['views'][pid] = {'visible_px': int(vis.sum()), 'coverage_of_polygon': round(inter / max(int(pm.sum()), 1), 3), 'precision': round(inter / max(int(vis.sum()), 1), 3)}
                img = cv2.resize(cv2.imread(str(vw.PHOTOS / f'{pid}.jpg')), S)
                lay = np.zeros_like(img); lay[vis] = (60, 255, 60)
                bl = cv2.addWeighted(img, .6, lay, .4, 0)
                cnts, _ = cv2.findContours(poly[pid], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE); cv2.drawContours(bl, cnts, -1, (0, 255, 255), 3)
                for name, per in corners.items():
                    if pid in per:
                        cv2.drawMarker(bl, tuple(int(x) for x in per[pid]), (0, 0, 255), cv2.MARKER_CROSS, 24, 3)
                cv2.putText(bl, f'{cand} {label} H={H}', (14, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
                cv2.imwrite(str(out / f'occlusion-{cand}-{label}-{pid}.jpg'), bl)
            ranking.append(row)
            print(cand, label, {k[-4:]: (v['coverage_of_polygon'], v['precision']) for k, v in row['views'].items()})
    report['occlusion_ranking'] = ranking
    (out / 'left-block-fit.json').write_text(json.dumps(report, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:5])
