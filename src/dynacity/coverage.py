"""Deterministic coverage checks; counts describe the extract, never the whole city."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math

import pandas as pd
from shapely.geometry import shape
from shapely.errors import ShapelyError

from .bbed import BBED_FIELDS, resolved_object_ids
from .status import CanonicalState, canonicalize_status

STATUS_FIELDS = {2018: 'Status2018', 2022: 'Status2022', 2024: 'F5__Current_status'}


def present(value):
    return value is not None and bool(str(value).strip())


def positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def content_hash(collection):
    return hashlib.sha256(json.dumps(collection, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def geometry_issue(geometry):
    if not geometry:
        return 'missing'
    try:
        obj = shape(geometry)
        if obj.geom_type not in ('Polygon', 'MultiPolygon'):
            return 'non_polygon'
        if obj.is_empty:
            return 'empty'
        if not obj.is_valid or not all(math.isfinite(v) for v in obj.bounds):
            return 'invalid'
        if obj.area <= 0:
            return 'empty'
    except (ValueError, TypeError, KeyError, IndexError, ShapelyError):
        return 'malformed'
    return None


def morphology_lookup(frame: pd.DataFrame | None):
    if frame is None or frame.empty:
        return {}
    if 'object_id' not in frame or frame.object_id.isna().any():
        raise ValueError('morphology requires non-null object_id')
    ids = frame.object_id.astype(str)
    if ids.str.strip().eq('').any() or ids.duplicated().any():
        raise ValueError('morphology object_id must be nonblank and unique')
    return {str(row['object_id']): row.to_dict() for _, row in frame.iterrows()}


def coverage_report(collection, *, manifest=None, snapshot=None, morphology=None, previous=None, panel=None):
    features = collection.get('features', [])
    errors, warnings = [], []
    if collection.get('type') != 'FeatureCollection' or not features:
        errors.append('Expected a nonempty FeatureCollection')
    unexpected_fields = set().union(*(set(f.get('properties', {})) for f in features)) - set(BBED_FIELDS)
    if unexpected_fields:
        errors.append('Source properties exceed BBED privacy allowlist')
    crs = collection.get('crs', {}).get('properties', {}).get('name', '')
    if crs not in ('EPSG:32636', 'urn:ogc:def:crs:EPSG::32636'):
        errors.append('Source CRS must explicitly be EPSG:32636')
    try:
        ids = resolved_object_ids(features)
    except ValueError as exc:
        errors.append(str(exc)); ids = []
    # ArcGIS row keys must be unique even if different domain IDs mask duplicates.
    row_ids = [str(f.get('properties', {}).get('OBJECTID')) for f in features]
    if len(set(row_ids)) != len(row_ids):
        errors.append('Duplicate ArcGIS OBJECTID')
    geometry = Counter(geometry_issue(f.get('geometry')) or 'valid' for f in features)
    if sum(v for k, v in geometry.items() if k != 'valid'):
        errors.append('Missing or unusable polygon geometry; review source, do not auto-repair')
    sha = content_hash(collection)
    if manifest is not None:
        if manifest.get('sha256') != sha or manifest.get('feature_count') != len(features):
            errors.append('Manifest hash or feature_count mismatch')
        if manifest.get('crs') != 'EPSG:32636' or not manifest.get('source') or not manifest.get('retrieved_at'):
            errors.append('Manifest missing source, retrieval time or supported CRS')
    else:
        warnings.append('No manifest supplied; source retrieval is unverified')
    periods = {}
    for year, field in STATUS_FIELDS.items():
        values = [f.get('properties', {}).get(field) for f in features]
        periods[str(year)] = {'field': field, 'present': sum(present(v) for v in values),
                              'missing': sum(not present(v) for v in values),
                              'unknown': sum(canonicalize_status(v) == CanonicalState.UNKNOWN for v in values)}
    height = Counter()
    latest = Counter()
    for f in features:
        p = f.get('properties', {})
        height['recorded_positive' if positive(p.get('Building_Hight_m')) else
               'floor_estimate' if positive(p.get('NoofFloor')) else 'fallback_estimate'] += 1
        latest[str(next((y for y in (2024, 2022, 2018) if present(p.get(STATUS_FIELDS[y]))), 'unknown'))] += 1
    missing_domain_ids = sum(not present(f.get('properties', {}).get('BULBuildingID')) for f in features)
    report = {'schema': 'dynacity-coverage/1', 'scope': 'supplied extract; citywide completeness unknown',
              'source_sha256': sha, 'source_records': len(features), 'geometry': dict(geometry),
              'missing_building_id': missing_domain_ids,
              'numeric_quality': {field: {
                  'missing': sum(not present(f.get('properties', {}).get(field)) for f in features),
                  'nonpositive_or_invalid': sum(present(f.get('properties', {}).get(field)) and not positive(f.get('properties', {}).get(field)) for f in features)
              } for field in ('Building_Hight_m', 'NoofFloor', 'Shape__Area')}, 'status_periods': periods,
              'latest_available_status_period': dict(latest), 'height': {k: height[k] for k in ('recorded_positive', 'floor_estimate', 'fallback_estimate')},
              'panel_intervals': {f'{a}-{b}': {
                  'eligible': sum(present(f.get('properties', {}).get(STATUS_FIELDS[a])) and present(f.get('properties', {}).get(STATUS_FIELDS[b])) for f in features),
                  'skipped_missing_endpoint': sum(not present(f.get('properties', {}).get(STATUS_FIELDS[a])) or not present(f.get('properties', {}).get(STATUS_FIELDS[b])) for f in features)
              } for a, b in ((2018, 2022), (2022, 2024), (2018, 2024))},
              'source': {k: manifest.get(k) for k in ('source', 'retrieved_at', 'license')} if manifest else None,
              'errors': errors, 'warnings': warnings}
    if snapshot is not None:
        snapshot_ids = [o.object_id for o in snapshot.objects]
        if len(set(snapshot_ids)) != len(snapshot_ids):
            errors.append('Duplicate snapshot object_id')
        if snapshot.crs != 'EPSG:32636':
            errors.append('Snapshot CRS mismatch')
        report['snapshot_join'] = {'source_only': len(set(ids) - set(snapshot_ids)),
                                   'snapshot_only': len(set(snapshot_ids) - set(ids)),
                                   'matched': len(set(ids) & set(snapshot_ids)),
                                   'as_of': snapshot.as_of.isoformat(),
                                   'data_version': snapshot.data_version}
        if report['snapshot_join']['source_only'] or report['snapshot_join']['snapshot_only']:
            errors.append('Source/snapshot object IDs do not match')
        source_by_id = dict(zip(ids, features))
        stale = sum(o.raw_state != (source_by_id[o.object_id].get('properties', {}).get('F5__Current_status')
                    if present(source_by_id[o.object_id].get('properties', {}).get('F5__Current_status')) else
                    source_by_id[o.object_id].get('properties', {}).get('Status2022'))
                    for o in snapshot.objects if o.object_id in source_by_id)
        report['snapshot_join']['status_value_mismatch'] = stale
        report['snapshot_join']['centroid_on_unusable_geometry'] = sum(
            o.centroid_x_m is not None and geometry_issue(source_by_id[o.object_id].get('geometry')) is not None
            for o in snapshot.objects if o.object_id in source_by_id)
        report['snapshot_join']['missing_centroid'] = sum(o.centroid_x_m is None or o.centroid_y_m is None for o in snapshot.objects)
        def number(value):
            try:
                n = float(value)
                return n if math.isfinite(n) and n >= 0 else None
            except (TypeError, ValueError):
                return None
        report['snapshot_join']['height_or_floor_mismatch'] = sum(
            o.height_m != number(source_by_id[o.object_id].get('properties', {}).get('Building_Hight_m')) or
            o.floors != number(source_by_id[o.object_id].get('properties', {}).get('NoofFloor'))
            for o in snapshot.objects if o.object_id in source_by_id)
        if report['snapshot_join']['height_or_floor_mismatch']:
            errors.append('Snapshot height/floor values differ from supplied source')
        if stale:
            errors.append('Snapshot status values differ from supplied source')
        if snapshot.as_of.year > 2024:
            warnings.append('Snapshot as_of is later than latest supported survey wave (2024)')
    if morphology is not None:
        try:
            lookup = morphology_lookup(morphology)
            report['morphology_join'] = {'matched': len(set(ids) & set(lookup)),
                                        'source_only': len(set(ids) - set(lookup)),
                                        'morphology_only': len(set(lookup) - set(ids))}
            report['morphology_join']['zero_or_missing_point_count'] = sum(not positive(row.get('point_count')) for row in lookup.values())
            if set(lookup) - set(ids):
                errors.append('Morphology contains unmatched object IDs')
        except ValueError as exc:
            errors.append(str(exc))
    if panel is not None:
        required = {'object_id', 'start_year', 'target_year', 'current_state', 'target_state'}
        if not required.issubset(panel.columns):
            errors.append('Panel missing required identity, interval or state columns')
        else:
            expected = {}
            for object_id, feature in zip(ids, features):
                p = feature.get('properties', {})
                for a, b in ((2018, 2022), (2022, 2024), (2018, 2024)):
                    if present(p.get(STATUS_FIELDS[a])) and present(p.get(STATUS_FIELDS[b])):
                        expected[(object_id, a, b)] = (canonicalize_status(p[STATUS_FIELDS[a]]).value,
                                                     canonicalize_status(p[STATUS_FIELDS[b]]).value)
            actual = {}
            invalid = duplicates = mismatched = 0
            for row in panel.to_dict('records'):
                try:
                    a, b = float(row['start_year']), float(row['target_year'])
                    if not a.is_integer() or not b.is_integer():
                        raise ValueError('Nonintegral interval')
                    key = (str(row['object_id']), int(a), int(b))
                    duplicates += key in actual
                    actual[key] = (row['current_state'], row['target_state'])
                    mismatched += key in expected and actual[key] != expected[key]
                except (ValueError, TypeError, OverflowError):
                    invalid += 1
            report['panel_join'] = {'rows': len(panel), 'missing_rows': len(expected.keys() - actual.keys()),
                                    'unexpected_rows': len(actual.keys() - expected.keys()),
                                    'duplicate_rows': duplicates, 'invalid_intervals': invalid,
                                    'state_value_mismatch': mismatched}
            if any(report['panel_join'][k] for k in report['panel_join'] if k != 'rows'):
                errors.append('Panel rows or state values differ from source intervals')
    if previous is not None:
        old = dict(zip(resolved_object_ids(previous.get('features', [])), previous.get('features', [])))
        new = dict(zip(ids, features))
        report['update'] = {'previous_sha256': content_hash(previous), 'added': len(new.keys() - old.keys()),
                            'removed': len(old.keys() - new.keys()),
                            'changed': sum(new[k] != old[k] for k in new.keys() & old.keys())}
        if report['update']['removed']:
            warnings.append('Update removes source IDs; require explicit removal acknowledgement')
    report['passed'] = not errors
    return report
