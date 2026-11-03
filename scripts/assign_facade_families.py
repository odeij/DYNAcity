"""Score corridor source buildings against reference-profile family polygons through the fitted cameras.

python scripts/assign_facade_families.py <camera-registration.json> <reference-profiles.json> <out.json>
A building is a *multi-view candidate* for a family when its projected street-facing silhouette overlaps that
family's annotated polygons in at least two photographs and beats every other family there. Positional error of
the Beirut 001 footprints (about 5 m) makes this a candidate ranking, never an identity.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'dynacity'))
import glb_geometry as g  # noqa: E402
from facade_massing import project_points  # noqa: E402

BATCHES = Path('C:/private/dynacity-data/engine/city-batches')
PHOTOS = Path('C:/private/dynacity-data/backups/beirut-evidence/engine/appearance-evidence-20261030/street-photos')
MATCHED = Path('C:/private/dynacity-data/engine/title-menu/matched.json')
S = (1600, 1200)


def main(reg_path, prof_path, out_path, min_height=12., max_dist=260.):
    reg = json.loads(Path(reg_path).read_text()); prof = json.loads(Path(prof_path).read_text())
    origin = json.loads(next(BATCHES.glob('buildings-remeil-*.objects.json')).read_text())['coordinates']['origin_utm_m']
    matched = json.loads(MATCHED.read_text())
    views = {}
    for pid, off in reg['fitted']['yaw_offset_deg'].items():
        meta = json.loads((PHOTOS / f'{pid}.json').read_text())
        views[pid] = {'cam': np.array([meta['camera_xy_m'][0] - origin[0], meta['camera_xy_m'][1] - origin[1], matched['m' + pid][0][2] / 100]),
                      'yaw': meta['heading_grid_deg'] + off}
    masks = {}
    for ob in prof['observations']:
        m = masks.setdefault((ob['photo_id'], ob['family']), np.zeros(S[::-1], np.uint8))
        poly = (np.array(ob['polygon_norm']) * S).astype(np.int32)
        cv2.fillPoly(m, [poly], 1)
    families = sorted({f for _, f in masks})
    out = []
    for glb in sorted(BATCHES.glob('buildings-*.glb')):
        index = json.loads(glb.with_suffix('.objects.json').read_text()); meshes = None
        for o in index['objects']:
            b = o['local_bounds_m']; c = np.array([(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2])
            if b[1][2] - b[0][2] < min_height or min(np.hypot(*(c - v['cam'][:2])) for v in views.values()) > max_dist:
                continue
            meshes = meshes or g.mesh_primitives(glb)
            P = meshes[o['batch']][0][o['first_vertex']:o['first_vertex'] + o['vertex_count']]
            cols = g.vertical_edges(P, None, 2.0)
            if not len(cols):
                continue
            per_view = {}
            for pid, v in views.items():
                pts = []
                for x, y, z0, z1 in cols:
                    pts += [[x, y, z0], [x, y, z1]]
                px = project_points(pts, v['cam'], v['yaw'], reg['fitted']['pitch_deg'], reg['fitted']['hfov_deg'], S)
                if np.isnan(px).any() or len(px) < 4:
                    continue
                hull = cv2.convexHull(px.astype(np.float32)).astype(np.int32)
                area = np.zeros(S[::-1], np.uint8); cv2.fillPoly(area, [hull], 1)
                inside = int(area[:S[1], :S[0]].sum())
                if inside < 2000:
                    continue
                scores = {f: round(float((area & masks[(pid, f)]).sum() / inside), 3) for f in families if (pid, f) in masks}
                if scores:
                    per_view[pid] = {'visible_px': inside, 'family_overlap': scores}
            if per_view:
                out.append({'source_id': o['source_id'], 'height_m': round(float(b[1][2] - b[0][2]), 1),
                            'batch': o['batch'], 'views': per_view})
    # aggregate: family supported if it is best (overlap>=0.15) in >=2 views
    for r in out:
        votes = {}
        for pid, pv in r['views'].items():
            f, s = max(pv['family_overlap'].items(), key=lambda kv: kv[1])
            if s >= 0.15:
                votes.setdefault(f, []).append({'photo_id': pid, 'overlap': s})
        best = max(votes.items(), key=lambda kv: (len(kv[1]), sum(x['overlap'] for x in kv[1])), default=None)
        r['family_votes'] = votes
        r['candidate_family'] = best[0] if best and len(best[1]) >= 2 else None
        r['evidence_level'] = ('multi_view_visual_candidate' if r['candidate_family'] else 'weak_or_single_view')
    Path(out_path).write_text(json.dumps({'schema': 'dynacity-family-candidates/1', 'camera_status': reg['verdict']['status'],
        'method': 'convex hull of projected building columns vs worker polygons; footprint position error ~5 m', 'buildings': out}, indent=1))
    for r in sorted(out, key=lambda r: -r['height_m']):
        if r['candidate_family']:
            print(r['source_id'][10:18], r['height_m'], r['candidate_family'], {k: [x['photo_id'][-4:] + ':' + str(x['overlap']) for x in v] for k, v in r['family_votes'].items()})


if __name__ == '__main__':
    main(*sys.argv[1:4])
