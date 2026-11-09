"""Consolidate verified coverage and export remaining building evidence targets."""
import argparse
from collections import Counter
import json
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform

from dynacity.building_appearance_evidence import digest


def report(output):
    root = Path(output)
    reviewed = json.loads((root/'appearance-evidence-reviewed.json').read_text())
    visibility = json.loads((root/'street-visibility.json').read_text())
    photos = json.loads((root/'street-photo-review-report.json').read_text())
    assessments = json.loads((root/'photo-visual-assessments.json').read_text())
    by_id = {row['source_id']:row for row in visibility['records']}
    for row in assessments['records']:
        if digest(root/'street-photos'/(row['photo_id']+'.jpg')) != row['photo_sha256']:
            raise ValueError('Visual assessment source bytes changed')
    geometries = []; counts = Counter()
    convert = Transformer.from_crs(reviewed['records'][0]['crs'], 'EPSG:4326', always_xy=True)
    for row in reviewed['records']:
        if row['facade']['status'] == 'observed_historical':
            continue
        has_view = bool(by_id[row['source_id']]['candidates'])
        reason = 'identity_not_uniquely_corroborated' if has_view else 'no_geometrically_plausible_view_in_bounded_search'
        counts[reason] += 1
        geometries.append({'type':'Feature', 'geometry':mapping(transform(convert.transform, shape(row['footprint']))),
                           'properties':{'source_id':row['source_id'], 'district':row['district'],
                                         'reason':reason, 'roof_status':row['roof']['status'],
                                         'geometry_role':'building evidence target; not a camera position'}})
    (root/'capture-evidence-gaps.geojson').write_text(json.dumps({'type':'FeatureCollection', 'features':geometries}))
    quality = Counter(row['status'] for row in assessments['records'])
    result = {'status':'bounded_pilot_evidence_review_completed', 'sample_buildings':len(reviewed['records']),
              'roof_candidates':reviewed['summary']['candidate_needs_review'],
              'roof_unknowns':reviewed['summary']['unknown'],
              'historically_corroborated_facades':reviewed['review_summary']['historically_observed_facades'],
              'facades_still_unknown':reviewed['review_summary']['unknown_facades'],
              'buildings_with_geometric_view_candidate':visibility['buildings_with_geometric_candidate'],
              'shared_photos_reviewed':len(assessments['records']),
              'shared_photo_bytes':photos['cached_bytes'], 'photo_quality_assessments':dict(quality),
              'remaining_evidence_gaps':dict(counts), 'unreal_changes':False,
              'limits':['Historical imagery; no current-condition claim',
                        'Geometric coverage and reviewed photo quality do not establish building identity',
                        'Roof candidates remain unapproved; physical facade material and albedo unmeasured']}
    (root/'evidence-progress.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    report(parser.parse_args().output)
