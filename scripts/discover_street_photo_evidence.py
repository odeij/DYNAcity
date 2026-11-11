"""Bounded KartaView metadata discovery; proximity is not a facade match."""
import argparse
import html
import json
from pathlib import Path

import httpx
from pyproj import Transformer
from shapely.geometry import shape


def discover(output):
    root = Path(output)
    sample = json.loads((root / 'sample-inventory.json').read_text())
    records = sample['records']
    crs = records[0]['crs']
    centers = [shape(record['footprint']).centroid for record in records]
    x = sum(point.x for point in centers)/len(centers)
    y = sum(point.y for point in centers)/len(centers)
    to_lonlat = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
    to_metric = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    lon, lat = to_lonlat.transform(x, y)
    endpoint = 'https://api.kartaview.org/2.0/photo/'

    def query(filename, params):
        path = root / filename
        if path.exists():
            saved = json.loads(path.read_text())
            if saved.get('params') == params and saved['response'].get('status', {}).get('apiCode') == 600:
                return saved['response']
        response = httpx.get(endpoint, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
        path.write_text(json.dumps({'request_url': str(response.url), 'params': params,
                                    'response': data}, indent=2))
        if data.get('status', {}).get('apiCode') != 600:
            raise RuntimeError('Street imagery API rejected query: ' + str(data.get('status')))
        return data

    nearby = query('street-nearby.json', {'lat': lat, 'lng': lon, 'radius': 500, 'itemsPerPage': 100})
    sequence_ids = sorted({str(row['sequenceId']) for row in nearby['result']['data']})
    frames = {}; pages = []; total_calls = 1
    # Hard budget: 3 sequences x 3 pages plus nearby query. All are cached.
    for sid in sequence_ids[:3]:
        for page in range(1, 4):
            data = query(f'street-sequence-{sid}-page-{page}.json',
                         {'sequenceId': sid, 'itemsPerPage': 100, 'page': page})
            total_calls += 1
            pages.append({'sequence': sid, 'page': page, 'has_more': data['result']['hasMoreData']})
            for row in data['result']['data']:
                px, py = to_metric.transform(float(row['lng']), float(row['lat']))
                if ((px-x)**2 + (py-y)**2)**.5 <= 500:
                    frames[str(row['id'])] = row
            if not data['result']['hasMoreData']:
                break
    candidates = []
    for record in records:
        footprint = shape(record['footprint'])
        possible = []
        for row in frames.values():
            from shapely.geometry import Point
            point = Point(to_metric.transform(float(row['lng']), float(row['lat'])))
            distance = footprint.distance(point)
            if distance <= 80:
                possible.append({'photo_id': str(row['id']), 'distance_m': round(distance, 2),
                                 'captured_at': row.get('shotDate'), 'heading_deg': row.get('heading'),
                                 'image_url': row.get('imageThUrl'), 'sequence_id': str(row['sequenceId'])})
        candidates.append({'source_id': record['source_id'], 'status': 'proximity_only_needs_review',
                           'photos': sorted(possible, key=lambda p: (p['distance_m'], p['photo_id']))[:3]})
    result = {'status': 'metadata_discovered', 'provider': 'KartaView',
              'documentation': 'https://kartaview.org/doc/photos',
              'sequence_ids': sequence_ids, 'sequence_pages': pages,
              'unique_frames_within_500m': len(frames),
              'sample_buildings_with_photo_within_80m': sum(bool(c['photos']) for c in candidates),
              'sample_buildings': len(records), 'confirmed_facade_matches': 0,
              'query_slots': total_calls, 'photo_downloads': 0,
              'license': 'CC BY-SA 4.0', 'license_source': 'https://kartaview.org/terms',
              'attribution': '© Grab and KartaView Contributors',
              'limits': ['Only up to 3 sequences and 3 pages per sequence inspected',
                         'Distance is only a discovery aid; direction, occlusion and identity unverified',
                         'Review images and retain license/attribution before extraction/reuse'],
              'candidates': candidates}
    (root / 'street-evidence-candidates.json').write_text(json.dumps(result, indent=2))
    rows = []
    for candidate in candidates:
        links = []
        for photo in candidate['photos']:
            if not photo.get('image_url', '').startswith('https://cdn.kartaview.org/'):
                continue
            links.append(f'<li><a href="{html.escape(photo["image_url"], quote=True)}">Photo {photo["photo_id"]}</a>'
                         f' · {photo["distance_m"]} m · {html.escape(str(photo["captured_at"]))}'
                         f' · camera heading {html.escape(str(photo["heading_deg"]))}°</li>')
        rows.append('<article><p>' + html.escape(candidate['source_id']) + '</p><ul>'
                    + ''.join(links) + '</ul>' + ('<p>No nearby frame in the bounded search.</p>' if not links else '')
                    + '<p>Building identity and façade visibility: unverified.</p></article>')
    (root / 'street-review.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>Street evidence shortlist</title>'
        '<style>body{font:16px Arial,sans-serif;margin:24px;background:#f3f5f6;color:#17242e}'
        'article{background:white;padding:16px;margin:12px 0;border-radius:12px;overflow-wrap:anywhere}</style>'
        '<h1>Street-photo discovery shortlist</h1><p>© Grab and KartaView Contributors · '
        '<a href="https://kartaview.org/terms">CC BY-SA 4.0</a></p>'
        '<p>Historical photos from 2018. Nearby camera locations do not establish a building match. '
        'Check heading, occlusion, identity and condition before extracting appearance.</p>'
        '<p><a href="review.html">Roof evidence contact sheet</a></p>' + ''.join(rows), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ['candidates', 'sequence_pages']}))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    discover(parser.parse_args().output)
