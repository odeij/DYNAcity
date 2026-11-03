"""Bind existing city elevations to traffic roads without relying on array IDs."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import math
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, box


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def select_roads(model, bounds_utm=None):
    """Select whole roads; the SUMO network itself must remain connected citywide."""
    if bounds_utm is None:
        return list(range(len(model)))
    if (len(bounds_utm) != 4 or not all(math.isfinite(v) for v in bounds_utm)
            or bounds_utm[0] >= bounds_utm[2] or bounds_utm[1] >= bounds_utm[3]):
        raise ValueError('Bounds require finite west, south, east, north in projected metres')
    area = box(*bounds_utm)
    return [i for i, path in enumerate(model.geometry) if LineString(path).intersects(area)]


def elevation_overrides(model, metadata, indices, *, tolerance_m=.25, sample_step_m=5):
    """Match way + complete XY shape, then interpolate model elevations.

    The saved metadata may belong to a differently ordered traffic model. Only
    unique geometric matches are accepted. Existing heights remain modelled,
    with their original datum/coverage limitations; no ground is invented.
    """
    if not math.isfinite(tolerance_m) or tolerance_m <= 0:
        raise ValueError('Matching tolerance must be positive and finite')
    if not math.isfinite(sample_step_m) or sample_step_m <= 0:
        raise ValueError('Elevation sample spacing must be positive and finite')
    coordinates = metadata['coordinates']
    if coordinates['crs'] != model.crs or coordinates.get('units') != 'metres':
        raise ValueError('Elevation metadata must use the traffic projected metre CRS')
    origin = np.asarray(coordinates['origin_utm_m'], dtype=float)
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError('Elevation origin must contain three finite coordinates')
    candidates = defaultdict(list)
    for road in metadata['roads']:
        if 'source_way' not in road:
            continue
        points = np.asarray(road['path'], dtype=float)
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 2 or not np.isfinite(points).all():
            continue
        points = points + origin
        candidates[int(road['source_way'])].append((points, LineString(points[:, :2]), road))
    overrides, unmatched = {}, []
    for i in indices:
        source = np.asarray(model.geometry[i], dtype=float)
        line = LineString(source)
        matches = []
        for points, candidate, record in candidates[int(model.way_id[i])]:
            endpoints = min(max(np.linalg.norm(source[0]-points[0, :2]), np.linalg.norm(source[-1]-points[-1, :2])),
                            max(np.linalg.norm(source[0]-points[-1, :2]), np.linalg.norm(source[-1]-points[0, :2])))
            if endpoints <= tolerance_m and line.hausdorff_distance(candidate) <= tolerance_m:
                matches.append((points, record))
        if len(matches) != 1:
            unmatched.append({'source_index': i, 'reason': 'ambiguous elevation match' if matches else 'no matching elevation geometry'})
            continue
        points, record = matches[0]
        starts, deltas = points[:-1, :2], np.diff(points[:, :2], axis=0)
        lengths2 = np.einsum('ij,ij->i', deltas, deltas)
        path = []
        # Retain source polyline vertices and densify every segment so a known
        # grade profile cannot collapse into a line between sparse endpoints.
        # Also sample each original elevation vertex projected onto the traffic
        # line; this retains every known slope break while preserving traffic XY.
        distances = {0., line.length}
        distance = 0.
        for a, b in zip(source, source[1:]):
            length = float(np.linalg.norm(b-a))
            intervals = max(1, math.ceil(length / sample_step_m))
            distances.update(distance + length*j/intervals for j in range(intervals + 1))
            distance += length
        from shapely.geometry import Point
        distances.update(line.project(Point(p[:2])) for p in points)
        for sample in sorted(distances):
            xy = np.asarray(line.interpolate(sample).coords[0])
            t = np.clip(np.einsum('ij,ij->i', xy-starts, deltas) / np.maximum(lengths2, 1e-20), 0, 1)
            distances = np.linalg.norm(starts + t[:, None]*deltas - xy, axis=1)
            k = int(np.argmin(distances))
            z = points[k, 2] + t[k]*(points[k+1, 2]-points[k, 2])
            path.append([float(xy[0]), float(xy[1]), float(z)])
        overrides[i] = {'path': path, 'evidence': {
            'geometry': {'status': 'mapped', 'source_ids': ['traffic_model']},
            'elevation': {'status': 'modelled', 'source_ids': ['city_elevations'],
                          'height_source': record.get('height_source', 'undeclared'),
                          'level': record.get('level'),
                          'vertical_datum': coordinates.get('vertical_datum', 'undeclared'),
                          'matching_tolerance_m': tolerance_m}}}
    return overrides, {'matched': len(overrides), 'selected': len(indices), 'unmatched': unmatched}
