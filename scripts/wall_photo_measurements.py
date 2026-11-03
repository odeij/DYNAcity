"""Photo-derived appearance and floor pitch per visible source wall, with explicit estimation limits.

python scripts/wall_photo_measurements.py <camera-registration.json> <walls.json> <out.json>
walls.json: [{"source_id" (uuid prefix), "edge"}...]

appearance: median colour of the photo pixels inside the wall's visible mask (nearest front-facing wall per pixel),
  with vegetation/sky/very dark pixels dropped, gray-world balanced per photo (the windshield cast), converted to linear.
  It is an appearance TARGET; shaded patches understate albedo, trees leave holes.
floor_pitch: the photo is resampled along the wall plane's height axis (ray-plane intersection per row), the mean
  luminance profile across the wall's central columns is autocorrelated, and the strongest period in 2.4-4.6 m is reported.
"""
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visible_walls as vw  # noqa: E402

S = vw.S


def srgb_to_linear(c):
    c = np.asarray(c, float) / 255.
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb8(c):
    c = np.asarray(c, float)
    return (np.clip(np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055), 0, 1) * 255).round().astype(int)


def camera_basis(views, pid, pitch):
    cam, yaw = views[pid]
    y, q = math.radians(yaw), math.radians(pitch)
    fwd = np.array([math.sin(y) * math.cos(q), math.cos(y) * math.cos(q), math.sin(q)])
    right = np.array([math.cos(y), -math.sin(y), 0.])
    return cam, fwd, right, np.cross(right, fwd)


def floor_pitch(mask, bal, w, cam, fwd, right, up, f):
    """Strongest 2.4-4.6 m period of the luminance profile along the wall's height axis, or None."""
    t = (w['b'] - w['a']) / np.linalg.norm(w['b'] - w['a'])
    n3 = np.array([t[1], -t[0], 0.])
    ys, xs = np.nonzero(mask)
    u0, u1 = np.percentile(xs, [30, 70])
    zs, lum = [], []
    for v in range(int(ys.min()), int(ys.max()) + 1):
        sel = mask[v, int(u0):int(u1) + 1]
        if sel.sum() < 8:
            continue
        d = fwd * f + right * ((u0 + u1) / 2 - S[0] / 2) + up * (S[1] / 2 - v)
        denom = n3 @ d
        if abs(denom) < 1e-6:
            continue
        tt = (np.array([*(w['a'] - cam[:2]), 0.]) @ n3) / denom
        if tt <= 0:
            continue
        zs.append(cam[2] + (tt * d)[2])
        lum.append(bal[v, int(u0):int(u1) + 1][sel].mean())
    if len(zs) < 40:
        return None
    zs, lum = np.array(zs), np.array(lum)
    order = np.argsort(zs)
    zs, lum = zs[order], lum[order]
    grid = np.arange(zs.min(), zs.max(), 0.05)
    prof = np.interp(grid, zs, lum)
    prof = (prof - np.convolve(prof, np.ones(81) / 81, mode='same'))[40:-40]
    lo, hi = int(2.4 / 0.05), int(4.6 / 0.05)
    if len(prof) < 120 or hi >= len(prof):
        return None
    ac = np.correlate(prof, prof, 'full')[len(prof) - 1:]
    ac = ac / (ac[0] + 1e-9)
    j = lo + int(np.argmax(ac[lo:hi]))
    return {'period_m': round(j * 0.05, 2), 'autocorr': round(float(ac[j]), 2), 'span_m': round(float(zs.max() - zs.min()), 1)}


def main(reg_path, walls_path, out_path):
    views, walls, idmaps, f, pitch = vw.raster(reg_path)
    out = []
    for want in json.loads(Path(walls_path).read_text()):
        ks = [k for k, w in enumerate(walls) if w['sid'].split(':')[1].startswith(want['source_id']) and w['edge'] == want['edge']]
        if len(ks) != 1:
            raise SystemExit(f'ambiguous wall {want}: {len(ks)} matches')
        k = ks[0]; w = walls[k]
        rec = {**want, 'source_id_full': w['sid'], 'length_m': round(float(np.linalg.norm(w['b'] - w['a'])), 2), 'views': {}}
        cols, pitches = [], []
        for pid in views:
            mask = idmaps[pid] == k
            if mask.sum() < 4000:
                continue
            img = cv2.resize(cv2.imread(str(vw.PHOTOS / f'{pid}.jpg')), S).astype(float)[:, :, ::-1]
            gains = img.reshape(-1, 3).mean(0)
            bal = np.clip(img * (gains.mean() / gains), 0, 255)
            px = bal[mask]
            hsv = cv2.cvtColor(px[None].astype(np.uint8), cv2.COLOR_RGB2HSV)[0]
            keep = (px.mean(1) > 45) & (px.mean(1) < 235) & ~((hsv[:, 0] > 30) & (hsv[:, 0] < 85) & (hsv[:, 1] > 60))
            if keep.sum() < 1500:
                continue
            med = np.median(px[keep], axis=0)
            rec['views'][pid] = {'visible_px': int(mask.sum()), 'sampled_px': int(keep.sum()), 'median_srgb_balanced': med.round().astype(int).tolist()}
            cols.append(srgb_to_linear(med))
            cam, fwd, right, up = camera_basis(views, pid, pitch)
            fp = floor_pitch(mask, bal, w, cam, fwd, right, up, f)
            if fp:
                rec['views'][pid]['floor_pitch'] = fp
                pitches.append((fp['period_m'], fp['autocorr']))
        if cols:
            lin = np.median(np.array(cols), axis=0)
            rec['appearance_linear_rgb_median_over_views'] = lin.round(4).tolist()
            rec['appearance_srgb_8bit'] = linear_to_srgb8(lin).tolist()
        strong = [p for p, a in pitches if a > 0.15]
        rec['floor_pitch_summary'] = {'periods_m': [p for p, _ in pitches], 'strong_periods_m': strong,
                                      'median_strong_m': round(float(np.median(strong)), 2) if strong else None}
        out.append(rec)
        print(want, rec.get('appearance_srgb_8bit'), rec['floor_pitch_summary'])
    Path(out_path).write_text(json.dumps({'schema': 'dynacity-wall-photo-measurements/1', 'walls': out,
        'limits': ['appearance is a target from lit/shaded mixed pixels, vegetation filtered, gray-world balanced',
                   'floor pitch from banding autocorrelation, not surveyed', 'camera uncalibrated; footprint ~5 m']}, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:4])
