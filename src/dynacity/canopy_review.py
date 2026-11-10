"""Allowlisted web evidence from crown peaks and their matching offline screen."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} outside supported range')
    return float(value)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string')
    return value


def load_canopy_review(candidate_path: str | Path, screening_path: str | Path) -> dict:
    """Verify exact candidate bytes and publish review data without local XYZ."""
    raw = Path(candidate_path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    screen = json.loads(Path(screening_path).read_text(encoding='utf-8'))
    if not isinstance(screen, dict) or not isinstance(screen.get('inputs_sha256'), dict):
        raise ValueError('screening must contain input hashes')
    if screen['inputs_sha256'].get('corridor-canopy-candidates.geojson') != digest:
        raise ValueError('candidate SHA256 mismatch')
    collection = json.loads(raw)
    if not isinstance(collection, dict) or collection.get('type') != 'FeatureCollection':
        raise ValueError('candidates must be a FeatureCollection')
    features = collection.get('features')
    if not isinstance(features, list) or not features:
        raise ValueError('candidates must contain a nonempty feature array')
    candidates = {}
    for feature in features:
        if not isinstance(feature, dict) or feature.get('type') != 'Feature':
            raise ValueError('candidate must be a Feature')
        geometry, props = feature.get('geometry'), feature.get('properties')
        if not isinstance(geometry, dict) or geometry.get('type') != 'Point' or not isinstance(props, dict):
            raise ValueError('candidate must have Point geometry and properties')
        xy = geometry.get('coordinates')
        if not isinstance(xy, list) or len(xy) != 2:
            raise ValueError('candidate coordinates must be [longitude, latitude]')
        identity = _text(props.get('id'), 'candidate id')
        if identity in candidates:
            raise ValueError('duplicate candidate id')
        height = _number(props.get('canopy_height_at_peak_m'), 'peak height', 0, math.inf)
        if height == 0:
            raise ValueError('peak height must be positive')
        candidates[identity] = {
            'id': identity,
            'position': [_number(xy[0], 'longitude', -180, 180), _number(xy[1], 'latitude', -85, 85)],
            'height_m': height,
        }
    screened = {}
    for name, status in [('native_trace_queue', 'pending'), ('held_offline', 'held')]:
        items = screen.get(name)
        if not isinstance(items, list):
            raise ValueError(f'{name} must be an array')
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('screening item must be an object')
            identity = _text(item.get('id'), 'screening id')
            if identity not in candidates or identity in screened:
                raise ValueError('unknown, duplicate or overlapping screening id')
            conflicts = item.get('conflicts')
            if not isinstance(conflicts, list) or (status == 'pending' and conflicts) or (status == 'held' and not conflicts):
                raise ValueError('screening conflicts disagree with review status')
            clean = []
            for conflict in conflicts:
                if not isinstance(conflict, dict):
                    raise ValueError('conflict must be an object')
                clean.append({'kind': _text(conflict.get('kind'), 'conflict kind'),
                              'source_id': _text(conflict.get('source_id'), 'conflict source id')})
            if 'canopy_peak_height_m' in item:
                height = _number(item['canopy_peak_height_m'], 'screened height', 0, math.inf)
                if height != candidates[identity]['height_m']:
                    raise ValueError('screening height disagrees with candidate')
            screened[identity] = {'review_status': status, 'conflicts': clean}
    if screened.keys() != candidates.keys():
        raise ValueError('screening ids must exactly cover candidates')
    output = [{**candidate, **screened[identity]} for identity, candidate in candidates.items()]
    xs, ys = [c['position'][0] for c in output], [c['position'][1] for c in output]
    return {
        'schema': 'dynacity-canopy-review/1', 'candidates': output,
        'counts': {'raw': len(output), 'pending': sum(c['review_status'] == 'pending' for c in output),
                   'held': sum(c['review_status'] == 'held' for c in output)},
        'bounds': [min(xs), min(ys), max(xs), max(ys)], 'candidate_sha256': digest,
        'source': {
            'name': 'Meta and WRI canopy height model v1',
            'url': 'https://registry.opendata.aws/dataforgood-fb-forests/', 'license': 'CC BY 4.0',
            'attribution': 'Meta and World Resources Institute (WRI), 2024. High Resolution Canopy Height Maps. Source imagery for CHM © Maxar.',
            'capture_date': None, 'accessed_at': datetime.now(timezone.utc).date().isoformat(),
        },
        'limits': ['Canopy peaks approximate crown centres; trunk locations are unverified.',
                   'Mapped walkway and water coverage is incomplete.',
                   'Ground contact, clearance and present-day conditions remain unverified.'],
    }
