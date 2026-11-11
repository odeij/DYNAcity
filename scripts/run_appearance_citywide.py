"""Resumable historical roof and street-photo evidence over source city tiles.

This is a collection pipeline, not automated building identification or an
Unreal import. A district index is an inventory, not a census of all Beirut.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
import math
import os
from pathlib import Path
import time

import httpx
from PIL import Image
from pyproj import Transformer
from shapely.geometry import shape

from dynacity.building_appearance_evidence import digest, observe_roofs, source_inventory
from prepare_appearance_evidence import SOURCE, cache_crop, review_page


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def inventory_fingerprint(folder, photos_per_group):
    indices = sorted(Path(folder).glob('buildings-*.objects.json'))
    if not indices:
        raise ValueError('No building indices found')
    files = []
    for index in indices:
        asset = index.with_suffix('').with_suffix('.glb')
        files.extend([{'path': str(path.resolve()), 'sha256': digest(path)} for path in (index, asset)])
    for path in [Path(__file__), Path(__file__).with_name('prepare_appearance_evidence.py'),
                 Path(__file__).resolve().parents[1]/'src/dynacity/building_appearance_evidence.py']:
        files.append({'path': str(path.resolve()), 'sha256': digest(path)})
    payload = {'files': files, 'imagery': SOURCE, 'tile_size_m': 250, 'photos_per_group': photos_per_group,
               'street_group_size_m': 500, 'schema': 1}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    return key, payload


def inventory(folder, runroot, fingerprint):
    path = runroot/'inventory.json'
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
        if data['fingerprint'] != fingerprint:
            raise ValueError('Inventory fingerprint changed')
        ids = [r['source_id'] for r in data['records']]
        if len(ids) != len(set(ids)) or len(ids) != data['inventory_buildings']:
            raise ValueError('Cached city inventory is incomplete or has duplicate IDs')
        return data
    data = source_inventory(folder, count=1000000)
    ids = [r['source_id'] for r in data['records']]
    if len(ids) != data['inventory_buildings'] or len(ids) != len(set(ids)):
        raise ValueError('Source inventory did not yield every unique building')
    data.update(fingerprint=fingerprint, selection={'method': 'all indexed buildings',
                'tile_owner': 'one 250 m source-centroid tile per building'})
    write_json(path, data)
    return data


def street_groups(records):
    result = defaultdict(list)
    for record in records:
        tx, ty = map(int, record['owner_tile'].split(':'))
        result[f'{tx//2}:{ty//2}'].append(record)
    return result


def sample_street(records, target, last_request_file, requests_per_hour, photo_budget):
    target.mkdir(parents=True, exist_ok=True)
    crs = records[0]['crs']; centers = [shape(r['footprint']).centroid for r in records]
    x = sum(p.x for p in centers)/len(centers); y = sum(p.y for p in centers)/len(centers)
    lon, lat = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True).transform(x, y)
    last = json.loads(last_request_file.read_text())['time'] if last_request_file.exists() else 0
    interval = 3600/requests_per_hour
    while True:
        remaining = last+interval-time.time()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 30))
    # Persist before the request so interrupts/restarts cannot bypass the rate limit.
    write_json(last_request_file, {'time': time.time()})
    response = httpx.get('https://api.kartaview.org/2.0/photo/',
                         params={'lat': lat, 'lng': lon, 'radius': 500, 'itemsPerPage': 100}, timeout=30)
    response.raise_for_status(); payload = response.json()
    if payload.get('status', {}).get('apiCode') != 600:
        raise RuntimeError('KartaView API: '+str(payload.get('status')))
    rows = payload['result']['data']
    raw = target/'street-query.json'
    write_json(raw, {'request_url': str(response.url), 'response': payload,
                     'source': 'KartaView', 'license': 'CC BY-SA 4.0',
                     'attribution': '© Grab and KartaView Contributors'})
    # Nearby endpoint yields representative images/sequences, not all frames.
    # One deterministic image is sufficient for a cheap overview, and is never
    # assigned to a building without later review.
    chosen = sorted((r for r in rows if (r.get('imageProcUrl') or '').startswith('https://cdn.kartaview.org/')
                     and r.get('visibility') == 'public'), key=lambda r: str(r.get('id', '')))[:photo_budget]
    photos = []
    for row in chosen:
        identity = str(row['id'])
        if not identity.isdigit():
            continue
        path = target/(identity+'.jpg')
        if path.exists():
            try:
                with Image.open(path) as image:
                    image.verify()
            except (OSError, ValueError):
                os.replace(path, path.with_suffix('.invalid.jpg'))
        if not path.exists():
            res = httpx.get(row['imageProcUrl'], timeout=45, follow_redirects=True)
            res.raise_for_status()
            if res.url.host != 'cdn.kartaview.org' or not res.headers.get('content-type', '').startswith('image/') or len(res.content) > 15_000_000:
                raise ValueError('Unexpected street-photo response')
            temporary = path.with_suffix('.download')
            temporary.write_bytes(res.content)
            with Image.open(temporary) as image:
                image.verify()
            os.replace(temporary, path)
        photos.append({'id': identity, 'file': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size,
                       'capture_date': row.get('shotDate'), 'url': row['imageProcUrl'],
                       'attribution': '© Grab and KartaView Contributors', 'license': 'CC BY-SA 4.0'})
    result = {'status': 'metadata_collected', 'request_url': str(response.url), 'query_sha256': digest(raw),
              'representative_records': len(rows), 'api_has_more': payload['result'].get('hasMoreData'),
              'photos': photos, 'building_matches': 0,
              'limitations': ['One nearby query; sequences and frames are not exhaustively paginated',
                              'Camera proximity does not identify visible buildings',
                              'Photos and dates need review before facade assignment']}
    write_json(target/'result.json', result)
    return result


def update_report(runroot, data, tiles, groups, state):
    roofs = [json.loads(p.read_text(encoding='utf-8')) for p in (runroot/'tiles').glob('*/result.json')]
    streets = [json.loads(p.read_text(encoding='utf-8')) for p in (runroot/'street').glob('*/result.json')]
    roof_counts = Counter()
    for item in roofs:
        roof_counts.update(item['roof_summary'])
    report = {'status': state, 'inventory_buildings': data['inventory_buildings'],
              'district_indices': len(data['sources']), 'tiles_total': len(tiles), 'tiles_processed': len(roofs),
              'roof_buildings_processed': sum(r['buildings'] for r in roofs),
              'roof_tiles_failed': sum(1 for p in (runroot/'tiles').glob('*/error.json') if not (p.parent/'result.json').exists()),
              'roof_candidate_counts': dict(roof_counts), 'street_groups_total': len(groups),
              'street_groups_processed': len(streets),
              'street_groups_failed': sum(1 for p in (runroot/'street').glob('*/error.json') if not (p.parent/'result.json').exists()),
              'street_representative_records': sum(r['representative_records'] for r in streets),
              'street_photos_cached': sum(len(r['photos']) for r in streets),
              'reviewed_facades': 0, 'reviewed_facades_scope': 'this collection run; earlier pilot reviews remain separate',
              'source_scope': 'indexed source buildings, not a full Beirut census',
              'limits': ['2018/2020 imagery is historical; roof colors are unreviewed candidates',
                         'Street queries are bounded samples and do not establish facade identity',
                         'No Unreal assets changed; no production use of noncommercial aerial imagery is asserted']}
    write_json(runroot/'citywide-report.json', report)
    rows = ''.join(f'<li><a href="tiles/{html.escape(r["tile"].replace(":", "_"))}/review.html">'
                   f'Tile {html.escape(r["tile"])}</a> · {r["buildings"]} buildings · '
                   f'{r["roof_summary"].get("candidate_needs_review",0)} roof candidates</li>' for r in roofs)
    pictures = ''.join(f'<article><a href="street/{html.escape(p.parent.name)}/{html.escape(photo["file"])}">'
                       f'<img loading="lazy" src="street/{html.escape(p.parent.name)}/{html.escape(photo["file"])}" alt="Historical street photo"></a>'
                       f'<p>Photo {html.escape(photo["id"])} · {html.escape(str(photo["capture_date"]))}</p></article>'
                       for p in (runroot/'street').glob('*/result.json')
                       for photo in json.loads(p.read_text(encoding='utf-8'))['photos'])
    page = ('<!doctype html><meta charset="utf-8">'
            + ('<meta http-equiv="refresh" content="30">' if state == 'running' else '')
            + '<title>Beirut evidence collection</title>'
            '<style>body{font:16px Arial,sans-serif;margin:30px;background:#f3f5f2;color:#172a22}'
            'a{color:#176652}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}'
            'article{background:white;padding:12px;border-radius:12px}img{width:100%;height:auto}</style>'
            '<h1>Beirut evidence collection</h1>'
            f'<p>Status: {html.escape(state)} · {report["roof_buildings_processed"]}/{report["inventory_buildings"]} indexed buildings processed for roofs · '
            f'{report["street_groups_processed"]}/{report["street_groups_total"]} street query areas completed.</p>'
            '<p>Historical imagery. Roof colors and photos require review; no automatic facade matches or Unreal updates.</p>'
            '<p>Street photos © Grab and KartaView Contributors, CC BY-SA 4.0. Aerial source: Maxar via OpenAerialMap; repository attribution CC BY-NC 4.0, research use.</p>'
            '<p><a href="citywide-report.json">Detailed status</a></p><h2>Roof tiles</h2><ul>'+rows+'</ul>'
            '<h2>Street photographs</h2><div class="grid">'+pictures+'</div>')
    (runroot/'index.html').write_text(page, encoding='utf-8')
    return report


def valid_tile(target):
    try:
        data = json.loads((target/'result.json').read_text(encoding='utf-8'))
        return all((target/name).is_file() and digest(target/name) == data[key]
                   for name, key in [('evidence.json', 'evidence_sha256'), ('review.html', 'review_sha256')])
    except (OSError, ValueError, KeyError):
        return False


def valid_street(target):
    try:
        data = json.loads((target/'result.json').read_text(encoding='utf-8'))
        return (digest(target/'street-query.json') == data['query_sha256'] and
                all(digest(target/photo['file']) == photo['sha256'] for photo in data['photos']))
    except (OSError, ValueError, KeyError):
        return False


def run(output, folder, *, requests_per_hour=60, photos_per_group=1, max_tiles=None, skip_street=False):
    if not 1 <= requests_per_hour <= 60 or not 0 <= photos_per_group <= 3:
        raise ValueError('Request rate must be 1–60/hour; photo budget 0–3 per group')
    key, inputs = inventory_fingerprint(folder, photos_per_group)
    runroot = Path(output).resolve()/'runs'/key; runroot.mkdir(parents=True, exist_ok=True)
    write_json(runroot/'inputs.json', inputs)
    data = inventory(folder, runroot, key)
    tiles = defaultdict(list)
    for r in data['records']:
        tiles[r['owner_tile']].append(r)
    groups = street_groups(data['records'])
    update_report(runroot, data, tiles, groups, 'running')
    write_json(Path(output)/'latest-run.json', {'fingerprint': key, 'run_directory': str(runroot),
                                                'report': str(runroot/'citywide-report.json'),
                                                'page': str(runroot/'index.html'), 'status': 'running'})
    count = 0
    for tile, records in sorted(tiles.items()):
        target = runroot/'tiles'/tile.replace(':','_'); target.mkdir(parents=True, exist_ok=True)
        if valid_tile(target):
            continue
        if max_tiles is not None and count >= max_tiles:
            break
        if (target/'result.json').exists():
            os.replace(target/'result.json', target/'result.invalid.json')
        count += 1
        try:
            bundle = {'records': records, 'inventory_buildings': data['inventory_buildings'],
                      'sample_buildings': len(records), 'sources': data['sources']}
            raster, cache = cache_crop(bundle, target)
            result = observe_roofs(bundle, raster, dict(SOURCE), target/'evidence.json')
            review_page(result, raster, target)
            write_json(target/'result.json', {'tile': tile, 'buildings': len(records),
                       'roof_summary': result['summary'], 'raster_sha256': cache['sha256'],
                       'evidence_sha256': digest(target/'evidence.json'), 'review_sha256': digest(target/'review.html')})
        except Exception as exc:
            write_json(target/'error.json', {'stage': 'roof', 'error': str(exc), 'building_count': len(records)})
        update_report(runroot, data, tiles, groups, 'running')
    if not skip_street and max_tiles is None:
        failures = 0
        for name, records in sorted(groups.items()):
            target = runroot/'street'/name.replace(':','_')
            if valid_street(target):
                continue
            if (target/'result.json').exists():
                os.replace(target/'result.json', target/'result.invalid.json')
            try:
                sample_street(records, target, Path(output)/'last-street-request.json', requests_per_hour, photos_per_group)
                failures = 0
            except Exception as exc:
                write_json(target/'error.json', {'stage': 'street', 'error': str(exc), 'building_count': len(records)})
                failures += 1
            update_report(runroot, data, tiles, groups, 'running')
            if failures >= 3:
                break
    report = update_report(runroot, data, tiles, groups, 'stopped_after_limit' if max_tiles is not None else 'finished_with_gaps')
    if max_tiles is None and report['tiles_processed'] == report['tiles_total'] and report['street_groups_processed'] == report['street_groups_total']:
        report = update_report(runroot, data, tiles, groups, 'collection_finished_review_needed')
    write_json(Path(output)/'latest-run.json', {'fingerprint': key, 'run_directory': str(runroot),
                                                'report': str(runroot/'citywide-report.json'),
                                                'page': str(runroot/'index.html'), 'status': report['status']})
    print(json.dumps({'run_directory': str(runroot), **report}))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    parser.add_argument('--city-batches', default='C:/private/dynacity-data/engine/city-batches')
    parser.add_argument('--requests-per-hour', type=int, default=60)
    parser.add_argument('--photos-per-group', type=int, default=1)
    parser.add_argument('--max-tiles', type=int)
    parser.add_argument('--skip-street', action='store_true')
    args = parser.parse_args()
    root = Path(args.output).resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root/'.citywide.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('Another citywide run owns this output directory')
        else:
            import fcntl
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError('Another citywide run owns this output directory')
        run(args.output, args.city_batches, requests_per_hour=args.requests_per_hour,
            photos_per_group=args.photos_per_group, max_tiles=args.max_tiles, skip_street=args.skip_street)
