"""Conservative, reviewable roof observations; never infer facades from roofs."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def source_inventory(folder, focus=(2423., 1491.), count=100, selection_bounds=None):
    """Use all district identities; select by distance, with stable ties."""
    from dynacity.engine_city_batches import Glb

    indexed, sources, identities = [], [], set()
    for path in sorted(Path(folder).glob('buildings-*.objects.json')):
        data = json.loads(path.read_text())
        sources.append({'index': str(path), 'sha256': digest(path),
                        'buildings': len(data['objects'])})
        for record in data['objects']:
            if record['source_id'] in identities:
                raise ValueError('Duplicate building identity')
            identities.add(record['source_id'])
            bounds = np.asarray(record['local_bounds_m'], dtype=float)
            if selection_bounds is not None:
                xmin, ymin, xmax, ymax = selection_bounds
                if bounds[1, 0] < xmin or bounds[0, 0] > xmax or bounds[1, 1] < ymin or bounds[0, 1] > ymax:
                    continue
            center = bounds.mean(axis=0)
            distance = float(np.linalg.norm(center[:2] - focus))
            indexed.append((distance, record['source_id'], path, data['coordinates'], record))
    if count < 1:
        raise ValueError('Sample count must be positive')
    selected = sorted(indexed, key=lambda row: (row[0], row[1]))[:count]
    meshes, records = {}, []
    for distance, identity, path, coordinates, record in selected:
        if path not in meshes:
            asset = path.with_suffix('').with_suffix('.glb')
            source_record = next(item for item in sources if item['index'] == str(path))
            source_record.update(asset=str(asset), asset_sha256=digest(asset))
            meshes[path] = {node['name']: (positions, faces)
                            for node, _, positions, _, faces in Glb(asset).objects()}
        positions, faces = meshes[path][record['batch']]
        first = record['first_triangle']
        triangles = faces[first:first + record['triangle_count']]
        # GLB X/Y/Z -> east/up/south. Project source triangles, not AABBs.
        used, inverse = np.unique(triangles, return_inverse=True)
        projected = positions[used][:, [0, 2]].copy()
        projected[:, 1] *= -1
        polygons = [Polygon(projected[face]) for face in inverse.reshape(-1, 3)]
        footprint = unary_union([p for p in polygons if p.is_valid and p.area > 1e-6])
        if footprint.is_empty or not footprint.is_valid:
            raise ValueError('Invalid projected footprint: ' + identity)
        origin = coordinates['origin_utm_m']
        from shapely.affinity import translate
        footprint = translate(footprint, xoff=origin[0], yoff=origin[1])
        center = footprint.centroid
        records.append({'source_id': identity, 'district': record['metadata']['production_group'],
                        'distance_to_focus_m': distance, 'footprint': mapping(footprint),
                        'crs': coordinates['crs'], 'area_m2': footprint.area,
                        'owner_tile': f'{math.floor((center.x-origin[0])/250)}:{math.floor((center.y-origin[1])/250)}',
                        'source_index': str(path), 'source_metadata': record['metadata'],
                        'roof': {'status': 'unknown'}, 'facade': {'status': 'unknown',
                        'reason': 'No matched street-level observation'}})
    return {'schema': 'dynacity-building-appearance-evidence/1',
            'inventory_buildings': len(identities), 'sample_buildings': len(records),
            'selection': {'focus_local_m': list(focus), 'method': 'nearest source bounds center'},
            'sources': sources, 'records': records}


def roof_candidate(rgb, valid_mask, *, pixel_size_m, minimum_pixels=25):
    """Heuristics flag a review candidate, not a calibrated color/material."""
    if not np.isfinite(pixel_size_m) or pixel_size_m <= 0:
        raise ValueError('Invalid ground pixel size')
    if pixel_size_m > 1:
        return {'status': 'unknown', 'reason': 'Imagery coarser than 1 m/pixel'}
    rgb = np.asarray(rgb)
    valid_mask = np.asarray(valid_mask, dtype=bool)
    if rgb.ndim != 3 or rgb.shape[-1] != 3 or rgb.shape[:2] != valid_mask.shape:
        raise ValueError('Expected RGB array with matching validity mask')
    pixels = rgb[valid_mask].astype(float)
    if len(pixels) < minimum_pixels:
        return {'status': 'unknown', 'reason': 'Insufficient valid interior pixels'}
    bright = pixels.max(axis=1)
    # Exclude obvious darkness and green dominance, not all shadows/vegetation.
    green = ((pixels[:, 1] > pixels[:, 0] * 1.15) &
             (pixels[:, 1] > pixels[:, 2] * 1.1))
    keep = (bright > 45) & (bright < 250) & ~green
    if keep.sum() < minimum_pixels or keep.mean() < .4:
        return {'status': 'unknown', 'reason': 'Too few pixels after heuristic screening',
                'valid_pixels': len(pixels), 'retained_pixels': int(keep.sum())}
    median = np.median(pixels[keep], axis=0)
    return {'status': 'candidate_needs_review', 'median_rgb': median.round().astype(int).tolist(),
            'valid_pixels': len(pixels), 'retained_pixels': int(keep.sum()),
            'pixel_size_m': pixel_size_m,
            'limitations': ['Apparent historical roof color under source illumination',
                           'Building/roof alignment and occlusions require visual review',
                           'Heuristics do not fully remove shadows or vegetation',
                           'No facade or roof material classification established']}


def observe_roofs(bundle, raster_path, source, output):
    import rasterio
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    from rasterio.windows import from_bounds, Window
    from shapely.ops import transform

    with rasterio.open(raster_path) as image:
        if image.count < 3 or image.dtypes[:3] != ('uint8', 'uint8', 'uint8'):
            raise ValueError('Explicit RGB uint8 imagery required; do not guess band scaling')
        if [c.name for c in image.colorinterp[:3]] != ['red', 'green', 'blue']:
            raise ValueError('RGB band order must be explicit')
        for record in bundle['records']:
            footprint = shape(record['footprint']).buffer(-1.)
            if footprint.is_empty:
                record['roof'] = {'status': 'unknown', 'reason': 'Interior empty after 1 m inset'}
                continue
            convert = Transformer.from_crs(record['crs'], image.crs, always_xy=True)
            projected = transform(convert.transform, footprint)
            try:
                window = from_bounds(*projected.bounds, transform=image.transform).round_offsets().round_lengths()
                window = window.intersection(Window(0, 0, image.width, image.height))
            except rasterio.errors.WindowError:
                record['roof'] = {'status': 'unknown', 'reason': 'Outside cached imagery'}
                continue
            if window.width < 1 or window.height < 1:
                record['roof'] = {'status': 'unknown', 'reason': 'Empty image window'}
                continue
            data = image.read([1, 2, 3], window=window, masked=True)
            affine = image.window_transform(window)
            mask = geometry_mask([mapping(projected)], out_shape=data.shape[1:],
                                 transform=affine, invert=True)
            mask &= ~np.ma.getmaskarray(data).any(axis=0)
            to_metric = Transformer.from_crs(image.crs, record['crs'], always_xy=True)
            cx, cy = projected.centroid.coords[0]
            a = np.array(to_metric.transform(cx, cy))
            dx = np.linalg.norm(np.array(to_metric.transform(cx + affine.a, cy + affine.d)) - a)
            dy = np.linalg.norm(np.array(to_metric.transform(cx + affine.b, cy + affine.e)) - a)
            record['roof'] = roof_candidate(np.moveaxis(data.filled(0), 0, -1), mask,
                                            pixel_size_m=float(max(dx, dy)))
            record['roof']['imagery_id'] = source['id']
    bundle['imagery'] = source
    bundle['imagery']['cached_sha256'] = digest(raster_path)
    bundle['summary'] = {status: sum(r['roof']['status'] == status for r in bundle['records'])
                         for status in ['unknown', 'candidate_needs_review']}
    bundle['summary']['observed_facades'] = 0
    Path(output).write_text(json.dumps(bundle, indent=2))
    return bundle
