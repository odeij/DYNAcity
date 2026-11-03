"""MAP + posterior sampling of the left block's facade corners and roof height from hand-read corners in views 1, 2, 4.

python scripts/fit_left_block_map.py <camera-registration.json> <corner-observations.json> <candidate-prefix> <edge> <out.json>

Parameters: facade corners a (left cap) and b (right roof corner) as shifts (da, db) from the candidate's source edge, and roof
height H above the source ground. Likelihood: Huber on the observed corner pixels (+-12 px) and the right-end vertical edge column,
with each view's heading offset a Gaussian nuisance (0.5 deg) solved in closed form. Priors (reasonable, stated, not tuned to win):
corner shift sigma 8 m each (typical Beirut 001 footprint error 5-9 m), facade-orientation change sigma 8 deg, H = 19.5 +- 3 m.
A random-walk Metropolis sampler gives posterior ranges. Held-out view 3 is projected, not fitted. NOT a survey.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fit_left_block as flb  # noqa: E402
import triangulate_corners as tc  # noqa: E402
import visible_walls as vw  # noqa: E402

RIGHT_EDGE_U = flb.RIGHT_EDGE_U


def neg_log_post(theta, a0, b0, z0, obs, views, pitch, hfov, f, prior):
    da, db, H = theta[0:2], theta[2:4], theta[4]
    a, b = a0 + da, b0 + db
    L, _, _ = flb.corner_loss(a, b, z0 + H, obs, views, pitch, hfov, f)
    ang = math.degrees(math.atan2(*(b - a)[::-1]) - math.atan2(*(b0 - a0)[::-1]))
    ang = (ang + 180) % 360 - 180
    pri = (da @ da + db @ db) / (2 * prior['shift_sigma_m'] ** 2) + ang ** 2 / (2 * prior['rot_sigma_deg'] ** 2) + (H - prior['H_mean']) ** 2 / (2 * prior['H_sigma'] ** 2)
    return L + pri


def main(reg_path, obs_path, prefix, edge, out_path, seed=5):
    reg = json.loads(Path(reg_path).read_text()); obs_all = json.loads(Path(obs_path).read_text())
    views = tc.load_views(reg); pitch, hfov = reg['fitted']['pitch_deg'], reg['fitted']['hfov_deg']
    f = (flb.S[0] / 2) / math.tan(math.radians(hfov) / 2)
    held = obs_all.get('held_out_views', [])
    corners = {k: {v: p for v, p in per.items() if v not in held} for k, per in obs_all['corners'].items()}
    obs = {v: {k: per for k, per in corners.items() if v in per} for v in views if v not in held}
    _, walls, _, _, _ = vw.raster(reg_path)
    ring, z0, z1 = flb.candidate_ring(walls, prefix)
    a0, b0 = ring[edge], ring[(edge + 1) % len(ring)]
    prior = {'shift_sigma_m': 8., 'rot_sigma_deg': 8., 'H_mean': 19.5, 'H_sigma': 3.}
    rng = np.random.default_rng(seed)
    best = (1e18, None)
    for restart in range(40):  # random restarts + coordinate descent
        th = np.array([*rng.normal(0, 6, 4), rng.uniform(16, 23)])
        cur = neg_log_post(th, a0, b0, z0, obs, views, pitch, hfov, f, prior)
        step = np.array([4., 4., 4., 4., 1.5])
        for _ in range(60):
            moved = False
            for i in range(5):
                for sg in (-1, 1):
                    t2 = th.copy(); t2[i] += sg * step[i]
                    v = neg_log_post(t2, a0, b0, z0, obs, views, pitch, hfov, f, prior)
                    if v < cur - 1e-9:
                        th, cur, moved = t2, v, True
            if not moved:
                step *= .5
                if step.max() < .05:
                    break
        if cur < best[0]:
            best = (cur, th.copy())
    th, cur = best[1].copy(), best[0]
    chain = []
    for it in range(6000):  # random-walk Metropolis around the MAP
        t2 = th + rng.normal(0, [.8, .8, .8, .8, .35])
        v = neg_log_post(t2, a0, b0, z0, obs, views, pitch, hfov, f, prior)
        if math.log(rng.uniform()) < cur - v:
            th, cur = t2, v
        if it > 1000 and it % 5 == 0:
            chain.append(th.copy())
    C = np.array(chain)
    a_map, b_map = a0 + best[1][0:2], b0 + best[1][2:4]
    L, res, nuis = flb.corner_loss(a_map, b_map, z0 + best[1][4], obs, views, pitch, hfov, f)
    L_src, res_src, _ = flb.corner_loss(a0, b0, z0 + prior['H_mean'], obs, views, pitch, hfov, f)
    held_proj = {hv: {n: (None if (p := tc.project(np.array([*X, z0 + best[1][4]]), views[hv], pitch, hfov)) is None else p.round(0).tolist()) for n, X in (('a', a_map), ('b', b_map))} for hv in held}
    out = {'schema': 'dynacity-left-block-map/1', 'candidate': prefix, 'edge': edge, 'source_edge_local_m': {'a': a0.round(2).tolist(), 'b': b0.round(2).tolist(), 'length_m': round(float(np.linalg.norm(b0 - a0)), 2)},
           'source_ground_z_local_m': round(float(z0), 2), 'priors': prior, 'observation_sigma_px': flb.SIG_PX,
           'map': {'a_local_m': a_map.round(2).tolist(), 'b_local_m': b_map.round(2).tolist(), 'length_m': round(float(np.linalg.norm(b_map - a_map)), 2), 'H_m': round(float(best[1][4]), 2),
                   'shift_a_m': best[1][0:2].round(2).tolist(), 'shift_b_m': best[1][2:4].round(2).tolist(), 'rotation_change_deg': round(math.degrees(math.atan2(*(b_map - a_map)[::-1]) - math.atan2(*(b0 - a0)[::-1])), 2),
                   'neg_log_posterior': round(float(best[0]), 2), 'data_loss': round(float(L), 2), 'residual_px_by_view': res, 'yaw_nuisance_deg': nuis},
           'source_position_reference': {'data_loss_at_source_H19.5': round(float(L_src), 2), 'residual_px_by_view': res_src},
           'posterior': {'shift_a_m_mean_std': [C[:, 0:2].mean(0).round(2).tolist(), C[:, 0:2].std(0).round(2).tolist()], 'shift_b_m_mean_std': [C[:, 2:4].mean(0).round(2).tolist(), C[:, 2:4].std(0).round(2).tolist()],
                         'H_m_p5_p50_p95': np.percentile(C[:, 4], [5, 50, 95]).round(2).tolist(),
                         'length_m_p5_p95': np.percentile([np.linalg.norm((b0 + c[2:4]) - (a0 + c[0:2])) for c in C], [5, 95]).round(2).tolist()},
           'held_out_projection_px': held_proj, 'limits': ['hand-read corners', 'uncalibrated camera', 'priors are assumptions', 'not a survey']}
    Path(out_path).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ('map', 'source_position_reference', 'posterior', 'held_out_projection_px')}, indent=0)[:2600])


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
