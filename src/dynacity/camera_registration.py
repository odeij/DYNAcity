"""Fit a shared camera model to several photographs from static building edges.

A landmark is the left or right silhouette of one source building, observed as an
image column ``u`` at image row ``v``. The model predicts it from the building's
vertical corner columns (``glb_geometry.vertical_edges``), so building height never
enters the fit: tops and bases may be hidden or wrong without affecting the camera.

Shared parameters: horizontal FOV and pitch (one phone, one mount). Per view: a yaw
offset on the recorded heading (heading metadata is noisy). Positions are held at
the recorded camera XY. The result is *uncalibrated* unless enough landmarks have a
confirmed identity at different depths; otherwise it is an explicit uncertain camera.
"""
from __future__ import annotations

import math

import numpy as np

try:
    from .facade_massing import project_points
except ImportError:  # run as a script without the package (no pydantic needed)
    from facade_massing import project_points

MIN_DEFINITE = 6
MIN_DEPTH_SPREAD_M = 30.


def predict_edge_u(columns, camera, yaw, pitch, fov, size, v, side):
    """Silhouette column of a building at image row ``v``, from its vertical corner lines extended to ``v``."""
    us = []
    for x, y, z0, z1 in columns:
        px = project_points([[x, y, z0], [x, y, z1]], camera, yaw, pitch, fov, size)
        if np.isnan(px).any():
            continue
        (ub, vb), (ut, vt) = px
        if abs(vb - vt) < 1e-6:
            continue  # the 3D vertical line is extended to row v: model height must not decide the fit
        us.append(ub + (ut - ub) * (v - vb) / (vt - vb))
    if not us:
        return None
    return min(us) if side == 'L' else max(us)


def _residuals(params, landmarks, views, buildings, size, free=None):
    fov, pitch = params[0], params[1]
    out = []
    for lm in landmarks:
        view = views[lm['view']]
        yaw = view['heading_deg'] + params[2 + view['index']]
        if lm.get('kind') == 'vp':  # vanishing point of the (level) street: row = H/2 + f*tan(pitch), independent of yaw
            f = (size[0] / 2) / math.tan(math.radians(fov) / 2)
            out.append(size[1] / 2 + f * math.tan(math.radians(pitch)) - lm['v'] * size[1])
            continue
        pred = predict_edge_u(buildings[lm['building']], view['camera_m'], yaw, pitch, fov, size, lm['v'] * size[1], lm['side'])
        out.append(1000. if pred is None else (pred / size[0] - lm['u']) * size[0])
    return np.array(out)


def _cost(params, landmarks, views, buildings, size, huber_px):
    r = np.abs(_residuals(params, landmarks, views, buildings, size))
    return float(np.sum(np.where(r <= huber_px, .5 * r * r, huber_px * (r - .5 * huber_px))))


def fit(landmarks, views, buildings, size=(2000, 1500), fov0=63., pitch0=12., huber_px=25.):
    """Coordinate descent with shrinking steps on (fov, pitch, yaw_1..yaw_n)."""
    n = 2 + len(views)
    p = np.array([fov0, pitch0] + [0.] * len(views))
    steps = np.array([8., 6.] + [4.] * len(views))
    bounds = [(35., 100.), (-5., 30.)] + [(-10., 10.)] * len(views)
    best = _cost(p, landmarks, views, buildings, size, huber_px)
    for _ in range(40):
        improved = False
        for i in range(n):
            for sign in (-1, 1):
                q = p.copy()
                q[i] = float(np.clip(q[i] + sign * steps[i], *bounds[i]))
                c = _cost(q, landmarks, views, buildings, size, huber_px)
                if c < best - 1e-9:
                    p, best, improved = q, c, True
        if not improved:
            steps *= .5
            if steps.max() < 0.01:
                break
    return p, best


def assess(params, landmarks, views, buildings, size, held_out_ids):
    """Per-landmark residuals split into fitted and held-out, with explicitly labelled denominators."""
    r = _residuals(params, landmarks, views, buildings, size)
    diag = math.hypot(*size)
    rows = []
    for lm, e in zip(landmarks, r):
        rows.append({'id': lm['id'], 'view': lm['view'], 'building': lm.get('building', 'street'), 'side': lm.get('side', 'vp'),
                     'identity': lm['identity'], 'held_out': lm['id'] in held_out_ids, 'residual_px': round(float(e), 1),
                     'residual_pct_of_width': round(abs(float(e)) / size[0] * 100, 2),
                     'residual_pct_of_diagonal': round(abs(float(e)) / diag * 100, 2)})

    def stats(sel):
        a = np.array([abs(x['residual_px']) for x in rows if sel(x)])
        if not len(a):
            return {'n': 0}
        return {'n': int(len(a)), 'median_px': round(float(np.median(a)), 1), 'max_px': round(float(a.max()), 1),
                'median_pct_of_width': round(float(np.median(a)) / size[0] * 100, 2), 'max_pct_of_width': round(float(a.max()) / size[0] * 100, 2),
                'median_pct_of_diagonal': round(float(np.median(a)) / diag * 100, 2), 'max_pct_of_diagonal': round(float(a.max()) / diag * 100, 2)}
    return rows, {'image_frame_px': list(size), 'diagonal_px': round(diag, 1),
                  'fitted': stats(lambda x: not x['held_out']), 'held_out': stats(lambda x: x['held_out'])}


def residual_gate(summary, median_frac=0.015, max_frac=0.03):
    """Proposed (not validated) gate on residuals as a fraction of the image DIAGONAL. Residuals alone never make a
    camera calibrated: see ``verdict`` for the landmark-identity requirement."""
    diag = summary['diagonal_px']
    out = {'denominator': 'image diagonal (px)', 'diagonal_px': diag, 'median_limit_px': round(median_frac * diag, 1),
           'max_limit_px': round(max_frac * diag, 1), 'status': 'proposed engineering gate, not validated'}
    for k in ('fitted', 'held_out'):
        s = summary[k]
        out[k] = {'median_pass': s['median_px'] <= median_frac * diag, 'max_pass': s['max_px'] <= max_frac * diag} if s['n'] else {'n': 0}
    return out


def verdict(landmarks, views, buildings):
    """Calibrated only with enough confirmed, depth-diverse, static landmarks."""
    definite = [lm for lm in landmarks if lm['identity'] == 'confirmed' and lm.get('kind') != 'vp']
    ids = {lm['building'] for lm in definite}
    depths = []
    for lm in definite:
        c = np.asarray(buildings[lm['building']])[:, :2].mean(0)
        depths.append(float(np.linalg.norm(c - np.asarray(views[lm['view']]['camera_m'])[:2])))
    spread = (max(depths) - min(depths)) if depths else 0.
    ok = len(definite) >= MIN_DEFINITE and len(ids) >= 2 and spread >= MIN_DEPTH_SPREAD_M
    return {'status': 'calibrated' if ok else 'uncalibrated_estimate',
            'definite_landmarks': len(definite), 'definite_buildings': len(ids),
            'depth_spread_m': round(spread, 1),
            'required': {'landmarks': MIN_DEFINITE, 'buildings': 2, 'depth_spread_m': MIN_DEPTH_SPREAD_M}}


def bootstrap(landmarks, views, buildings, size, rounds=40, seed=7, **kw):
    """Spread of fitted parameters when landmarks are resampled (explicit uncertainty)."""
    rng = np.random.default_rng(seed)
    fits = []
    for _ in range(rounds):
        idx = rng.integers(0, len(landmarks), len(landmarks))
        sample = [landmarks[i] for i in idx]
        p, _ = fit(sample, views, buildings, size, **kw)
        fits.append(p)
    f = np.array(fits)
    return {'mean': f.mean(0).round(2).tolist(), 'std': f.std(0).round(2).tolist()}
