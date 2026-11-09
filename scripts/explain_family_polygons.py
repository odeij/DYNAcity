"""Which source buildings explain a family's annotated photo polygons? (depth-ordered wall projection, all poses)

python scripts/explain_family_polygons.py <camera-registration.json> <reference-profiles.json> <family> <out_dir>
For every building: per pose, `coverage` = share of the family's polygon area covered by that building's visible walls;
`precision` = share of the building's visible wall area that lies inside the polygon. A good candidate has high coverage
and precision in several poses. Trees and cars hide wall area in the photos, and footprints carry about 5 m position error,
so this ranks candidates; it does not identify a building. Writes a table and a candidate overlay per pose.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visible_walls as vw  # noqa: E402

S = vw.S


def main(reg, profiles, family, out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    views, walls, idmaps, f, pitch = vw.raster(reg)
    prof = json.loads(Path(profiles).read_text())
    polys = {}
    for ob in prof['observations']:
        if ob['family'] != family:
            continue
        m = polys.setdefault(ob['photo_id'], np.zeros(S[::-1], np.uint8))
        cv2.fillPoly(m, [(np.array(ob['polygon_norm']) * S).astype(np.int32)], 1)
    sid_of = [w['sid'] for w in walls]
    rows = {}
    for pid, idmap in idmaps.items():
        if pid not in polys:
            continue
        poly = polys[pid].astype(bool); area = int(poly.sum())
        ids, counts = np.unique(idmap[(idmap >= 0)], return_counts=True)
        per = {}
        for k, c in zip(ids, counts):
            per.setdefault(sid_of[k], [0, 0])[1] += int(c)
        ids2, counts2 = np.unique(idmap[(idmap >= 0) & poly], return_counts=True)
        for k, c in zip(ids2, counts2):
            per.setdefault(sid_of[k], [0, 0])[0] += int(c)
        for sid, (inside, total) in per.items():
            rows.setdefault(sid, {})[pid] = {'coverage': round(inside / max(area, 1), 3), 'precision': round(inside / max(total, 1), 3), 'inside_px': inside}
    ranked = sorted(rows.items(), key=lambda kv: -sum(v['inside_px'] for v in kv[1].values()))
    table = []
    for sid, v in ranked[:25]:
        w = [x for x in walls if x['sid'] == sid]
        table.append({'source_id': sid, 'height_m': round(max(x['z1'] for x in w) - min(x['z0'] for x in w), 1), 'views': v,
                      'total_inside_px': sum(x['inside_px'] for x in v.values()), 'views_with_inside_px_over_5000': len([1 for x in v.values() if x['inside_px'] > 5000])})
    (out / f'{family}-explained.json').write_text(json.dumps({'family': family, 'camera_status': json.loads(Path(reg).read_text())['verdict']['status'], 'candidates': table}, indent=1))
    rng = np.random.default_rng(11)
    cols = {t['source_id']: tuple(int(c) for c in rng.integers(70, 255, 3)) for t in table[:8]}
    for pid, idmap in idmaps.items():
        if pid not in polys:
            continue
        img = cv2.resize(cv2.imread(str(vw.PHOTOS / f'{pid}.jpg')), S)
        layer = np.zeros_like(img)
        for sid, c in cols.items():
            for k in [i for i, s in enumerate(sid_of) if s == sid]:
                layer[idmap == k] = c
        blend = cv2.addWeighted(img, 0.55, layer, 0.45, 0)
        cnts, _ = cv2.findContours(polys[pid], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(blend, cnts, -1, (0, 255, 255), 3)
        for sid, c in cols.items():
            ys, xs = np.nonzero(np.isin(idmap, [i for i, s in enumerate(sid_of) if s == sid]))
            if len(xs) > 3000:
                cv2.putText(blend, sid[10:14], (int(np.median(xs)) - 25, int(np.median(ys))), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
        cv2.imwrite(str(out / f'{family}-overlay-{pid}.jpg'), blend)
    for t in table[:12]:
        print(t['source_id'][10:18], t['height_m'], t['views_with_inside_px_over_5000'], t['total_inside_px'], {k[-4:]: (v['coverage'], v['precision']) for k, v in t['views'].items()})


if __name__ == '__main__':
    main(*sys.argv[1:5])
