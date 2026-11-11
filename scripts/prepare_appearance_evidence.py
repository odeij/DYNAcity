"""Run a bounded, historical roof-evidence sample without modifying Unreal."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import time

from dynacity.building_appearance_evidence import source_inventory, observe_roofs, digest

SOURCE = {
    'id': 'oam-maxar-beirut-2020-07-31', 'captured_at': '2020-07-31',
    'url': 'https://oin-hotosm-temp.s3.us-east-1.amazonaws.com/5f3350077ef1c4000631d8cd/2/5f3350077ef1c4000631d8d0.tif',
    'attribution': 'Maxar Open Data via OpenAerialMap',
    'license_recorded_in_repository': 'CC BY-NC 4.0',
    'license_metadata_status': 'Repository attribution; live OAM metadata endpoint returned HTTP 500',
    'usage': 'Historical research/review candidate; no production texture export',
    'source_configuration': 'src/dynacity/viewer.py',
}


def cache_crop(bundle, output):
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import from_bounds, Window
    from rasterio.warp import transform_bounds
    from shapely.geometry import shape
    import httpx

    records = bundle['records']
    crs = records[0]['crs']
    if any(r['crs'] != crs for r in records):
        raise ValueError('Sample has mixed coordinate systems')
    bounds = [shape(r['footprint']).bounds for r in records]
    extent = [min(b[0] for b in bounds)-10, min(b[1] for b in bounds)-10,
              max(b[2] for b in bounds)+10, max(b[3] for b in bounds)+10]
    if (extent[2]-extent[0]) * (extent[3]-extent[1]) > 2_000_000:
        raise ValueError('Sample exceeds bounded 2 km2 acquisition budget')
    key = hashlib.sha256(json.dumps([SOURCE, crs, extent], sort_keys=True).encode()).hexdigest()[:16]
    target = output / ('imagery-' + key + '.tif')
    metadata = target.with_suffix('.json')
    if target.exists() and metadata.exists():
        cached = json.loads(metadata.read_text())
        if cached['sha256'] == digest(target):
            return target, cached
        raise ValueError('Cached imagery hash mismatch')
    # Pin remote object metadata so cached bytes have a traceable acquisition.
    response = httpx.head(SOURCE['url'], timeout=20)
    response.raise_for_status()
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_HTTP_TIMEOUT='20',
                      GDAL_HTTP_MAX_RETRY='0', GDAL_CACHEMAX=64):
        with rasterio.open(SOURCE['url']) as image:
            bbox = transform_bounds(crs, image.crs, *extent, densify_pts=21)
            window = from_bounds(*bbox, transform=image.transform).round_offsets().round_lengths()
            window = window.intersection(Window(0, 0, image.width, image.height))
            if window.width * window.height > 16_000_000:
                raise ValueError('Sample exceeds 16 megapixel acquisition budget')
            data = image.read(window=window, masked=True)
            profile = image.profile.copy()
            profile.update(width=int(window.width), height=int(window.height),
                           transform=image.window_transform(window), driver='GTiff',
                           compress='deflate', photometric='RGB')
            with rasterio.open(target, 'w', **profile) as crop:
                crop.write(data.filled(0))
                crop.write_mask((~data.mask.any(axis=0) * 255).astype('uint8')
                                if data.mask.ndim else image.dataset_mask(window=window))
                crop.colorinterp = image.colorinterp
    cached = {'source_url': SOURCE['url'], 'etag': response.headers.get('etag'),
              'last_modified': response.headers.get('last-modified'),
              'requested_bounds': extent, 'bounds_crs': crs, 'sha256': digest(target),
              'cached_bytes': target.stat().st_size}
    metadata.write_text(json.dumps(cached, indent=2))
    return target, cached


def review_page(bundle, raster, output):
    import numpy as np
    from PIL import Image, ImageDraw
    from pyproj import Transformer
    import rasterio
    from rasterio.windows import from_bounds, Window
    from shapely.geometry import shape
    from shapely.ops import transform

    rows = []
    with rasterio.open(raster) as image:
        for number, record in enumerate(bundle['records']):
            convert = Transformer.from_crs(record['crs'], image.crs, always_xy=True)
            geom = transform(convert.transform, shape(record['footprint']))
            minx, miny, maxx, maxy = geom.bounds
            padding = max(maxx-minx, maxy-miny)*.15
            window = from_bounds(minx-padding, miny-padding, maxx+padding, maxy+padding,
                                 transform=image.transform).round_offsets().round_lengths()
            window = Window(window.col_off, window.row_off, max(1, window.width), max(1, window.height))
            data = image.read([1, 2, 3], window=window, boundless=True, fill_value=0)
            picture = Image.fromarray(np.moveaxis(data, 0, -1))
            affine = image.window_transform(window)
            draw = ImageDraw.Draw(picture)
            polygons = list(geom.geoms) if geom.geom_type == 'MultiPolygon' else [geom]
            for poly in polygons:
                for ring in [poly.exterior, *poly.interiors]:
                    pixels = [(~affine) * xy for xy in ring.coords]
                    draw.line(pixels, fill=(255, 60, 60), width=1)
            picture.thumbnail((360, 280))
            filename = f'building-{number:03}.png'
            picture.save(output / filename)
            roof = record['roof']
            swatch = roof.get('median_rgb')
            chip = (f'<span class="chip" style="background:rgb({",".join(map(str, swatch))})"></span>'
                    if swatch else '')
            rows.append(f'<article><img src="{filename}" alt="Historical aerial crop with source footprint outlined">'
                        f'<p>{number+1}. {html.escape(record["source_id"])}</p>'
                        f'<p>{chip}{html.escape(roof["status"])} — {html.escape(roof.get("reason", "Review roof alignment and color"))}</p>'
                        f'<p>Façade: {html.escape(record["facade"]["status"])} · Roof review: pending</p></article>')
    page = ('<!doctype html><meta charset="utf-8"><title>Beirut roof evidence review</title>'
            '<style>body{font:16px Arial,sans-serif;margin:24px;background:#f3f5f6;color:#17242e}'
            'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}'
            'article{background:white;padding:16px;border-radius:12px;overflow-wrap:anywhere}'
            'img{max-width:100%;image-rendering:auto}.chip{display:inline-block;width:24px;height:24px;margin-right:8px;vertical-align:middle}</style>'
            f'<h1>Historical roof evidence · {len(bundle["records"])} buildings</h1>'
            '<p>31 July 2020 · Maxar via OpenAerialMap · repository attribution: CC BY-NC 4.0.</p>'
            '<p>Red outlines are projected source footprints. Check roof displacement, shadows and neighbors. '
            'Colors are candidates under source lighting; wall colors remain unknown. This contact sheet does not approve records.</p>'
            '<p><a href="evidence.json">Evidence records</a>'
            + (' · <a href="street-identity-review.html">Street photos beside building maps</a>' if (output/'street-identity-review.html').exists() else '')
            + (' · <a href="appearance-evidence-reviewed.json">Reviewed appearance records</a>' if (output/'appearance-evidence-reviewed.json').exists() else '')
            + '</p><main>'
            + ''.join(rows) + '</main>')
    (output / 'review.html').write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--city-batches', default='C:/private/dynacity-data/engine/city-batches')
    parser.add_argument('--output', required=True)
    parser.add_argument('--count', type=int, default=100)
    args = parser.parse_args()
    started = time.perf_counter()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    bundle = source_inventory(args.city_batches, count=args.count)
    (output / 'sample-inventory.json').write_text(json.dumps(bundle, indent=2))
    raster, cache = cache_crop(bundle, output)
    bundle = observe_roofs(bundle, raster, dict(SOURCE), output / 'evidence.json')
    reviewed = bundle
    if (output/'reviewed-observations.json').exists():
        from dynacity.appearance_observations import apply_reviewed_observations
        review = json.loads((output/'reviewed-observations.json').read_text())
        reviewed = apply_reviewed_observations(bundle, review, output/'street-photos')
        (output/'appearance-evidence-reviewed.json').write_text(json.dumps(reviewed, indent=2))
    review_page(reviewed, raster, output)
    report = {'status': 'prepared_for_review', 'inventory_buildings': bundle['inventory_buildings'],
              'sample_buildings': bundle['sample_buildings'], 'roof_summary': bundle['summary'],
              'facade_review_summary': reviewed.get('review_summary', {'historically_observed_facades': 0}),
              'cache': cache, 'elapsed_seconds': round(time.perf_counter()-started, 2),
              'unreal_changes': False, 'automatic_approvals': 0,
              'limits': ['Projected footprint alignment is inferred, not surveyed',
                         '2020 imagery is historical evidence, not current building condition',
                         'Only explicitly reviewed facade observations establish historical appearance']}
    (output / 'run-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
