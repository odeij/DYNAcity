"""Recover supported road/ground observations using explicit review footprints.

This is a survey-preparation product, not a certified DTM. No interpolation,
height offset, automatic class promotion or invented elevations are applied.
The footprint producer, CRS assertion and screening choices remain auditable.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from pyproj import CRS
from shapely import intersects_xy
from shapely.geometry import shape
from shapely.ops import unary_union

from .las import LASHeader, iter_point_chunks


def _hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def review_ground(source, footprints, output, *, cell_m, horizontal_crs,
                  crs_evidence, exclusion_buffer_m, min_points, max_spread_m,
                  max_points=10_000_000, max_cells=2_000_000):
    """Aggregate actual points inside include polygons, outside exclusions.

    Footprints are a planar GeoJSON FeatureCollection with ``horizontal_crs``,
    nonempty ``evidence`` and Polygon/MultiPolygon features whose ``role`` is
    include/exclude. Include means a manually identified road/open-ground
    candidate, never a certified ground class. Exclusions win on boundaries.
    All thresholds must be explicitly selected; they do not establish accuracy.
    Quantiles select original observed ranks, without averaging or gap filling.
    """
    source, footprints, output = map(Path, (source, footprints, output))
    repo = Path(__file__).resolve().parents[2]
    if output.resolve() == repo or repo in output.resolve().parents:
        raise ValueError('private ground review outputs must be outside the repository')
    if output.suffix != '.npz' or output.exists() or output.with_suffix('.json').exists():
        raise ValueError('new .npz output and sidecar required')
    if not all(math.isfinite(v) for v in (cell_m, exclusion_buffer_m, max_spread_m)) or cell_m <= 0 or exclusion_buffer_m < 0 or max_spread_m <= 0:
        raise ValueError('positive cell/spread and nonnegative exclusion buffer required')
    if type(min_points) is not int or min_points < 1:
        raise ValueError('min_points must be a positive integer')
    crs = CRS.from_user_input(horizontal_crs)
    if not crs.is_projected or any(abs(a.unit_conversion_factor-1) > 1e-12 for a in crs.axis_info[:2]):
        raise ValueError('projected metre CRS required')
    if not crs_evidence.strip():
        raise ValueError('CRS evidence required')
    footprint_hash = _hash(footprints)
    spec = json.loads(footprints.read_text())
    if spec.get('type') != 'FeatureCollection' or not isinstance(spec.get('evidence'), str) or not spec['evidence'].strip():
        raise ValueError('footprints require FeatureCollection and producer evidence')
    if CRS(spec['horizontal_crs']) != crs:
        raise ValueError('footprints and cloud must use the same asserted CRS')
    groups = {'include': [], 'exclude': []}
    for feature in spec['features']:
        geom = shape(feature['geometry'])
        role = feature.get('properties', {}).get('role')
        if role not in groups or geom.geom_type not in {'Polygon', 'MultiPolygon'} or geom.is_empty or not geom.is_valid or geom.has_z or not np.isfinite(geom.bounds).all():
            raise ValueError('valid 2D include/exclude polygons required; no implicit repair')
        groups[role].append(geom)
    if not groups['include']:
        raise ValueError('at least one include polygon required')
    include = unary_union(groups['include'])
    exclude = unary_union(groups['exclude']).buffer(exclusion_buffer_m)
    header = LASHeader.read(source)
    with source.open('rb') as stream:
        stream.seek(104)
        if stream.read(1)[0] & 0xC0:
            raise ValueError('compressed cloud requires PDAL')
    if header.point_count < 1 or header.point_count > max_points:
        raise ValueError('point count outside max_points guard')
    if source.stat().st_size < header.point_data_offset+header.point_count*header.point_record_length:
        raise ValueError('truncated cloud')
    minx, maxx, miny, maxy, _, _ = header.bounds
    if not np.isfinite(header.bounds).all() or maxx < minx or maxy < miny:
        raise ValueError('invalid cloud bounds')
    west, north = math.floor(minx/cell_m)*cell_m, math.ceil(maxy/cell_m)*cell_m
    cols = math.floor((maxx-west)/cell_m)+1
    rows = math.floor((north-miny)/cell_m)+1
    if rows*cols > max_cells:
        raise ValueError('grid exceeds max_cells guard')
    keys, heights, classes = [], [], np.zeros(32, dtype=np.uint64)
    seen = 0
    source_hash = _hash(source)
    for chunk in iter_point_chunks(source, max_points=max_points):
        if not all(np.isfinite(v).all() for v in (chunk.x, chunk.y, chunk.z)):
            raise ValueError('nonfinite point coordinates')
        c = np.floor((chunk.x-west)/cell_m).astype(np.int64)
        r = np.floor((north-chunk.y)/cell_m).astype(np.int64)
        if np.any((c < 0) | (c >= cols) | (r < 0) | (r >= rows)):
            raise ValueError('points outside header bounds')
        keep = intersects_xy(include, chunk.x, chunk.y) & ~intersects_xy(exclude, chunk.x, chunk.y)
        keys.append((r[keep]*cols+c[keep]))
        heights.append(chunk.z[keep])
        classes += np.bincount(chunk.classification[keep], minlength=32).astype(np.uint64)
        seen += len(chunk)
    if seen != header.point_count or source_hash != _hash(source) or footprint_hash != _hash(footprints):
        raise ValueError('source changed during review')
    ids, z = np.concatenate(keys), np.concatenate(heights)
    if not len(z):
        raise ValueError('no original observations within supplied footprints')
    order = np.lexsort((z, ids))
    unique, starts, counts = np.unique(ids[order], return_index=True, return_counts=True)
    stats = {}
    for name, q in [('q10_observed_z', .1), ('q50_observed_z', .5), ('q90_observed_z', .9)]:
        arr = np.full(rows*cols, np.nan)
        rank = np.ceil((counts-1)*q) if q > .5 else np.floor((counts-1)*q)
        arr[unique] = z[order[starts+rank.astype(int)]]
        stats[name] = arr.reshape(rows, cols)
    count = np.zeros(rows*cols, dtype=np.uint32)
    count[unique] = counts
    count = count.reshape(rows, cols)
    spread = stats['q90_observed_z']-stats['q10_observed_z']
    supported = (count >= min_points) & (spread <= max_spread_m)
    # Full-cell footprint containment is separate from point selection. Cells
    # crossing exclusions must not bridge a parked car/building in a later mesh.
    from shapely import box, covers
    rr, cc = np.indices((rows, cols))
    boxes = box(west+cc*cell_m, north-(rr+1)*cell_m,
                west+(cc+1)*cell_m, north-rr*cell_m)
    permitted = covers(include.difference(exclude), boxes)
    candidate = np.where(supported & permitted, stats['q50_observed_z'], np.nan)
    report = {
        'schema_version': 1, 'kind': 'footprint_ground_candidate_review',
        'source': json.loads(json.dumps(header.as_dict())), 'source_sha256': source_hash,
        'footprints': str(footprints.resolve()), 'footprints_sha256': footprint_hash,
        'footprint_evidence': spec['evidence'], 'horizontal_crs': crs.to_string(),
        'crs_evidence': crs_evidence, 'crs_status': 'caller_asserted',
        'vertical_reference': None, 'verified_flood_terrain': False,
        'cell_m': cell_m, 'west': west, 'north': north, 'rows': rows, 'cols': cols,
        'screening': {'min_points': min_points, 'max_q90_q10_spread_m': max_spread_m,
                      'exclusion_buffer_m': exclusion_buffer_m, 'full_cell_inside_footprint': True,
                      'rank_policy': 'lower observed rank for q10/q50, upper for q90'},
        'points_processed': seen, 'selected_points': len(z),
        'selected_classification_counts': {str(i): int(v) for i, v in enumerate(classes) if v},
        'populated_cells': len(unique), 'fully_permitted_cells': int(permitted.sum()),
        'candidate_cells': int(np.isfinite(candidate).sum()),
        'supported_area_m2': float(np.isfinite(candidate).sum()*cell_m**2),
        'limitations': ['Draft footprints do not certify bare earth.',
                       'Quantiles select original elevations; no interpolation or vertical alignment.',
                       'Gaps, vehicles and excluded buildings remain unavailable, not inferred ground.',
                       'Spread/support thresholds are screening choices, not vertical accuracy.',
                       'No independent checkpoints, vertical datum or hydraulic boundary established.'],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        np.savez_compressed(stream, metadata=json.dumps(report), **stats, point_count=count,
                            fully_permitted=permitted, candidate_z=candidate)
    output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n')
    return report
