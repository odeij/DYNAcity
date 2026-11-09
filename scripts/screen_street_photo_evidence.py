"""Screen cached frames by heading, wall visibility and building obstruction."""
import argparse
from collections import Counter
import hashlib
import html
import json
import math
from pathlib import Path
import time

from pyproj import Geod, Transformer
from shapely.geometry import shape
from shapely.strtree import STRtree

from dynacity.building_appearance_evidence import source_inventory, digest
from dynacity.street_photo_visibility import visible_wall_samples


def screen(root):
    root = Path(root)
    started = time.perf_counter()
    sample = json.loads((root/'sample-inventory.json').read_text())
    records = sample['records']; crs = records[0]['crs']
    source = json.loads(Path(records[0]['source_index']).read_text())
    origin = source['coordinates']['origin_utm_m']
    bounds = [shape(r['footprint']).bounds for r in records]
    # Include all indexed buildings intersecting a 100 m margin, not just the sample.
    extent = [min(b[0] for b in bounds)-origin[0]-100,
              min(b[1] for b in bounds)-origin[1]-100,
              max(b[2] for b in bounds)-origin[0]+100,
              max(b[3] for b in bounds)-origin[1]+100]
    blocker_config = {'extent': extent, 'sources': sample['sources'], 'generator': 1}
    key = hashlib.sha256(json.dumps(blocker_config, sort_keys=True).encode()).hexdigest()[:16]
    cache = root/('blockers-'+key+'.json')
    if cache.exists():
        neighborhood = json.loads(cache.read_text())
    else:
        neighborhood = source_inventory(Path(records[0]['source_index']).parent,
                                        count=20000, selection_bounds=extent)
        cache.write_text(json.dumps(neighborhood))
    polygons = [shape(r['footprint']) for r in neighborhood['records']]
    tree = STRtree(polygons)
    convert = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    geod = Geod(ellps='WGS84')
    frames = {}
    for path in sorted(root.glob('street-sequence-*-page-*.json')):
        data = json.loads(path.read_text())
        if data['response']['status']['apiCode'] != 600:
            continue
        for row in data['response']['result']['data']:
            frames[str(row['id'])] = row
    normalized = []
    for row in frames.values():
        if row.get('projection') != 'PLANE':
            continue
        try:
            lon, lat, heading = float(row['lng']), float(row['lat']), float(row['heading'])
            if not all(math.isfinite(v) for v in [lon, lat, heading]):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        xy = convert.transform(lon, lat)
        lon2, lat2, _ = geod.fwd(lon, lat, heading, 100)
        x2, y2 = convert.transform(lon2, lat2)
        grid_heading = math.degrees(math.atan2(x2-xy[0], y2-xy[1])) % 360
        normalized.append((row, xy, grid_heading))
    results = []; reasons = Counter()
    for record in records:
        target = shape(record['footprint']); candidates = []
        for row, xy, heading in normalized:
            outcome = visible_wall_samples(xy, heading, target, tree)
            reasons[outcome['status']] += 1
            if not outcome['samples']:
                continue
            best = min(outcome['samples'], key=lambda s: abs(s['heading_offset_deg']) + abs(s['distance_m']-25)*.2)
            # Prefer visible, forward and moderately close views; never call this confidence.
            score = abs(best['heading_offset_deg']) + abs(best['distance_m']-25)*.2
            candidates.append({'photo_id': str(row['id']), 'sequence_id': str(row['sequenceId']),
                               'captured_at': row.get('shotDate'), 'camera_xy_m': list(xy),
                               'heading_true_deg': float(row['heading']), 'heading_grid_deg': heading,
                               'ranking_cost': round(score, 3), 'wall_sample': best,
                               'visible_wall_sample_count': len(outcome['samples']),
                               'processed_image_url': row.get('imageProcUrl'),
                               'thumbnail_url': row.get('imageThUrl'),
                               'camera_field_of_view_recorded': row.get('fieldOfView'),
                               'view_cone_half_angle_assumed_deg': 50,
                               'status': 'geometry_candidate_needs_visual_identity_review'})
        chosen = []
        for candidate in sorted(candidates, key=lambda c: (c['ranking_cost'], c['photo_id'])):
            # Suppress consecutive near-identical camera stations from the same route.
            if any(candidate['sequence_id'] == c['sequence_id'] and
                   math.dist(candidate['camera_xy_m'], c['camera_xy_m']) < 8 for c in chosen):
                continue
            chosen.append(candidate)
            if len(chosen) == 3:
                break
        results.append({'source_id': record['source_id'], 'candidates': chosen,
                        'facade_status': 'unknown', 'confirmed_identity': False})
    result = {'schema': 'dynacity-street-visibility-shortlist/1',
              'sample_buildings': len(records), 'blocker_buildings': len(polygons),
              'metadata_frames': len(frames), 'usable_plane_camera_records': len(normalized),
              'buildings_with_geometric_candidate': sum(bool(r['candidates']) for r in results),
              'candidate_pairs_after_deduplication': sum(len(r['candidates']) for r in results),
              'screening_reasons': dict(reasons), 'confirmed_facade_matches': 0,
              'elapsed_seconds': round(time.perf_counter()-started, 2),
              'limits': ['Uncalibrated forward cone; source fieldOfView is often zero',
                         'GPS and source footprint alignment are inferred',
                         '2D building obstruction only; trees, vehicles and scaffolding unmodeled',
                         'Geometry shortlist is not observed facade evidence'],
              'source_sample_sha256': digest(root/'sample-inventory.json'), 'records': results}
    (root/'street-visibility.json').write_text(json.dumps(result, indent=2))
    items = []
    for i, record in enumerate(results):
        links = []
        for c in record['candidates']:
            links.append(f'<li>Photo {c["photo_id"]}, {html.escape(str(c["captured_at"]))}: '
                         f'{c["wall_sample"]["distance_m"]:.1f} m, '
                         f'heading offset {c["wall_sample"]["heading_offset_deg"]:.1f}°, '
                         f'<a href="{html.escape(c["processed_image_url"], quote=True)}">view processed image</a></li>')
        items.append(f'<article><p>Building {i+1}: {html.escape(record["source_id"])}</p><ul>'
                     + ''.join(links) + '</ul><p>Identity and appearance: pending visual review.</p></article>')
    (root/'visibility-review.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>Geometric facade evidence shortlist</title>'
        '<style>body{font:16px Arial,sans-serif;background:#f3f5f6;margin:24px;color:#17242e}'
        'article{padding:16px;background:white;margin:12px 0;border-radius:12px;overflow-wrap:anywhere}</style>'
        '<h1>Façade evidence: screened camera views</h1><p>© Grab and KartaView Contributors · '
        '<a href="https://kartaview.org/terms">CC BY-SA 4.0</a></p>'
        '<p>Historical 2018 images. Filtered by camera direction and source-building obstruction. '
        'The field of view is assumed; identities are unconfirmed.</p><p><a href="review.html">Roof evidence</a></p>'
        + ''.join(items), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='records'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    screen(parser.parse_args().output)
