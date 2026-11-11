"""Compare rendered vs photographed wall colour inside each wall's projected mask (same fitted camera, same pixels).

python scripts/appearance_check.py <camera-registration.json> <wall-measurements.json> <render_dir> <out.json> [buildings.json]
render_dir holds pair{1..4}_<photo>_fitted.png (1600x1200). Reports, per wall and view, the photo-balanced median (vegetation
filtered, as measured) against the render median over the same mask, and the linear-luminance ratio photo/render. The ratio is
a documented calibration input for the albedo scale, not proof of resemblance (windows, trees and lighting differ).
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visible_walls as vw  # noqa: E402
from wall_photo_measurements import srgb_to_linear  # noqa: E402

ORDER = {'1288770821': 1, '1288770597': 2, '1288771293': 3, '1288770701': 4}


def luma(rgb_lin):
    return float(np.dot(rgb_lin, [0.2126, 0.7152, 0.0722]))


def main(reg, meas_path, render_dir, out_path):
    views, walls, idmaps, f, pitch = vw.raster(reg)
    meas = json.loads(Path(meas_path).read_text())['walls']
    rows = []
    for m in meas:
        k = next(i for i, w in enumerate(walls) if w['sid'] == m['source_id_full'] and w['edge'] == m['edge'])
        for pid, pv in m['views'].items():
            mask = idmaps[pid] == k
            img = cv2.imread(str(Path(render_dir) / f'pair{ORDER[pid]}_{pid}_fitted.png'))[:, :, ::-1].astype(float)
            if mask.sum() < 4000:
                continue
            px = img[mask]
            keep = (px.mean(1) > 30) & (px.mean(1) < 245)
            med = np.median(px[keep], axis=0)
            lp, lr = luma(srgb_to_linear(pv['median_srgb_balanced'])), luma(srgb_to_linear(med))
            rows.append({'source_id': m['source_id'], 'edge': m['edge'], 'view': pid, 'photo_srgb': pv['median_srgb_balanced'],
                         'render_srgb': med.round().astype(int).tolist(), 'luma_ratio_photo_over_render': round(lp / max(lr, 1e-6), 3)})
    by = {}
    for r in rows:
        by.setdefault(r['source_id'], []).append(r['luma_ratio_photo_over_render'])
    summary = {k: {'median_ratio': round(float(np.median(v)), 3), 'n': len(v)} for k, v in by.items()}
    Path(out_path).write_text(json.dumps({'rows': rows, 'summary': summary}, indent=1))
    for r in rows:
        print(r['source_id'], r['edge'], r['view'][-4:], 'photo', r['photo_srgb'], 'render', r['render_srgb'], 'ratio', r['luma_ratio_photo_over_render'])
    print(summary)


if __name__ == '__main__':
    main(*sys.argv[1:5])
