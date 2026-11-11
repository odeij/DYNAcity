"""Cache a small shared photo set and map context for visual identity review."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import html
import json
import math
from pathlib import Path
import time

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pyproj import Transformer
import rasterio
from rasterio.windows import from_bounds
from shapely.geometry import shape
from shapely.ops import transform

from dynacity.building_appearance_evidence import digest


def prepare(output, budget=8):
    if not 1 <= budget <= 24:
        raise ValueError('Photo budget must be between 1 and 24')
    root = Path(output)
    visibility = json.loads((root/'street-visibility.json').read_text())
    inventory = json.loads((root/'sample-inventory.json').read_text())
    observations = {}
    if (root/'appearance-evidence-reviewed.json').exists():
        reviewed = json.loads((root/'appearance-evidence-reviewed.json').read_text())
        observations = {r['source_id']:r['facade'] for r in reviewed['records']
                        if r['facade']['status'] == 'observed_historical'}
    assessments = {}
    if (root/'photo-visual-assessments.json').exists():
        assessments = {r['photo_id']:r for r in json.loads((root/'photo-visual-assessments.json').read_text())['records']}
    choices, coverage = {}, {}
    for number, record in enumerate(visibility['records'], 1):
        for candidate in record['candidates']:
            identity = candidate['photo_id']
            choices[identity] = candidate
            coverage.setdefault(identity, set()).add(number)
    selected, covered = [], set()
    while choices and len(selected) < budget:
        key = min(choices, key=lambda pid: (-len(coverage[pid]-covered),
                                           choices[pid]['ranking_cost'], pid))
        if not coverage[key]-covered:
            break
        selected.append(choices.pop(key)); covered.update(coverage[key])
    photo_folder = root/'street-photos'; photo_folder.mkdir(exist_ok=True)

    def fetch(candidate):
        identity = candidate['photo_id']
        target = photo_folder/(identity+'.jpg'); sidecar = target.with_suffix('.json')
        url = candidate['processed_image_url']
        if not url.startswith('https://cdn.kartaview.org/'):
            raise ValueError('Unapproved photo host')
        if target.exists() and sidecar.exists():
            metadata = json.loads(sidecar.read_text())
            if metadata['url'] == url and metadata['sha256'] == digest(target):
                return metadata
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        if not response.headers.get('content-type', '').startswith('image/') or len(response.content) > 15_000_000:
            raise ValueError('Unexpected image response or download exceeds photo budget')
        target.write_bytes(response.content)
        with Image.open(target) as picture:
            size = list(picture.size)
        metadata = {'photo_id': identity, 'url': url, 'sha256': digest(target), 'bytes': target.stat().st_size,
                    'image_size_px': size, 'captured_at': candidate['captured_at'],
                    'camera_xy_m': candidate['camera_xy_m'], 'heading_grid_deg': candidate['heading_grid_deg'],
                    'source_record': candidate, 'license': 'CC BY-SA 4.0',
                    'license_source': 'https://kartaview.org/terms',
                    'attribution': '© Grab and KartaView Contributors'}
        sidecar.write_text(json.dumps(metadata, indent=2))
        return metadata

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        photos = list(pool.map(fetch, selected))
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 19)
    raster = next(root.glob('imagery-*.tif'))
    rows = []
    with rasterio.open(raster) as image:
        convert = Transformer.from_crs(inventory['records'][0]['crs'], image.crs, always_xy=True)
        for photo in photos:
            candidate = choices.get(photo['photo_id']) or next(c for c in selected if c['photo_id'] == photo['photo_id'])
            x, y = photo['camera_xy_m']
            # 160 m context centered 25 m ahead of the camera.
            heading = math.radians(photo['heading_grid_deg'])
            cx, cy = x+25*math.sin(heading), y+25*math.cos(heading)
            minx, miny = convert.transform(cx-80, cy-80)
            maxx, maxy = convert.transform(cx+80, cy+80)
            window = from_bounds(minx, miny, maxx, maxy, image.transform).round_offsets().round_lengths()
            data = image.read([1, 2, 3], window=window, boundless=True, fill_value=0)
            picture = Image.fromarray(np.moveaxis(data, 0, -1)).resize((900, 900))
            affine = image.window_transform(window)
            def pixel(east, north):
                px, py = (~affine)*convert.transform(east, north)
                return (px*900/data.shape[2], py*900/data.shape[1])
            draw = ImageDraw.Draw(picture)
            for number, record in enumerate(inventory['records'], 1):
                geom = shape(record['footprint'])
                polygons = list(geom.geoms) if geom.geom_type == 'MultiPolygon' else [geom]
                color = ('#55ff88' if record['source_id'] in observations else
                         '#ffdf00' if number in coverage[photo['photo_id']] else '#b8c2cb')
                for polygon in polygons:
                    points = [pixel(a, b) for a, b in polygon.exterior.coords]
                    draw.line(points, fill=color, width=3)
                px, py = pixel(geom.centroid.x, geom.centroid.y)
                if 0 <= px < 900 and 0 <= py < 900:
                    label = str(number)
                    box = draw.textbbox((px, py), label, font=font)
                    draw.rectangle(box, fill='#14212a')
                    draw.text((px, py), label, fill=color, font=font)
            px, py = pixel(x, y)
            draw.ellipse((px-7, py-7, px+7, py+7), fill='#00ffff', outline='#10202a', width=2)
            endpoint = pixel(x+30*math.sin(heading), y+30*math.cos(heading))
            draw.line([(px, py), endpoint], fill='#00ffff', width=6)
            draw.text((12, 12), 'North ↑ · cyan camera · yellow candidates · green corroborated', fill='white',
                      stroke_width=2, stroke_fill='black', font=font)
            map_name = photo['photo_id']+'-map.png'; picture.save(photo_folder/map_name)
            labels = sorted(coverage[photo['photo_id']])
            assessment = assessments.get(photo['photo_id'])
            notes = ('<p><strong>'+html.escape(assessment['status'])+'</strong>: '
                     + html.escape(assessment['notes'])+'</p>') if assessment else ''
            matched = [obs for obs in observations.values() if obs['photo_id'] == photo['photo_id']]
            for observation in matched:
                anchor_url = observation.get('corroboration', {}).get('mapped_anchor', {}).get('url')
                anchor_link = (f' <a href="{html.escape(anchor_url, quote=True)}">Mapped corroboration anchor</a>.'
                               if anchor_url and anchor_url.startswith('https://www.openstreetmap.org/') else '')
                notes += ('<p><strong>Corroborated historical façade: building '
                          + str(next(i for i,r in enumerate(inventory['records'],1) if r['source_id']==observation['source_id']))
                          + '</strong> — '+html.escape(', '.join(observation['visible_features']))
                          + '.' + anchor_link + '</p>')
            rows.append(f'<article id="photo-{photo["photo_id"]}"><h2>Photo {photo["photo_id"]}</h2>'
                        f'<p>{html.escape(str(photo["captured_at"]))} · candidate buildings: {", ".join(map(str, labels))}</p>'
                        '<div class="pair">'
                        f'<a href="street-photos/{photo["photo_id"]}.jpg"><img src="street-photos/{photo["photo_id"]}.jpg" alt="Original processed street photograph"></a>'
                        f'<a href="street-photos/{map_name}"><img src="street-photos/{map_name}" alt="Aerial context with numbered source footprints and camera direction"></a></div>'
                        '<p>Click either image to inspect full size. Yellow footprints are unconfirmed candidates; green marks a corroborated historical match.</p>'
                        + notes + '</article>')
    links = []
    for number, record in enumerate(inventory['records'], 1):
        matched = [p for p in photos if number in coverage[p['photo_id']]]
        if matched:
            links.append(f'<li>Building {number}: {html.escape(record["source_id"])} — '
                         + ' · '.join(f'<a href="#photo-{p["photo_id"]}">{p["photo_id"]}</a>' for p in matched)+'</li>')
    (root/'street-identity-review.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>Street photos and building identity review</title>'
        '<style>body{font:16px Arial,sans-serif;background:#eef2f4;color:#17242e;margin:24px}'
        'article{background:white;padding:20px;margin:24px 0;border-radius:14px}.pair{display:grid;grid-template-columns:1.35fr 1fr;gap:16px}'
        'img{width:100%;height:auto}li{margin:8px 0}h2{font-size:20px}a{color:#00697a}'
        '@media(max-width:900px){.pair{grid-template-columns:1fr}}</style>'
        '<h1>Street photographs beside building footprints</h1>'
        '<p><a href="beirut-work-and-evidence.html">Open the complete Beirut work and evidence page →</a></p>'
        '<p>Historical evidence: 2018 street photographs and 2020 aerial context. '
        'Camera direction and 2D obstruction screened. Green marks a corroborated historical match; other identities remain pending.</p>'
        '<p>Street imagery: © Grab and KartaView Contributors, <a href="https://kartaview.org/terms">CC BY-SA 4.0</a>. '
        'Aerial context: Maxar via OpenAerialMap, repository attribution CC BY-NC 4.0.</p>'
        '<p><a href="review.html">Roof review</a> · <a href="visibility-review.html">All screened candidates</a>'
        ' · <a href="appearance-evidence-reviewed.json">Reviewed observations</a></p>'
        '<details><summary>Building index</summary><ul>'+''.join(links)+'</ul></details>'
        + ''.join(rows), encoding='utf-8')
    report = {'status': 'photos_cached_for_identity_review', 'photos': photos,
              'unique_photo_count': len(photos), 'candidate_buildings_shown': len(covered),
              'sample_buildings': len(inventory['records']),
              'cached_bytes': sum(p['bytes'] for p in photos),
              'elapsed_seconds': round(time.perf_counter()-started, 2),
              'corroborated_historical_facades': len(observations)}
    (root/'street-photo-review-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='photos'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True)
    parser.add_argument('--photo-budget', type=int, default=8)
    args = parser.parse_args(); prepare(args.output, args.photo_budget)
