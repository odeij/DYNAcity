"""Loader and validator for the photo-sourced facade profile artifact (``reference-profiles.json``).

The artifact is private (derived from CC BY-SA street photographs) and is read, never generated, here.
Image-space geometry is normalized (u = x/width, v = y/height, origin top-left); metres appear only in
``{est, lo, hi, basis}`` ranges and are estimates. This module only checks that a profile document is
internally consistent and that identity claims stay within the reviewed evidence; it makes no geometry.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

SCHEMA = 'dynacity-reference-facade-profiles/1'
_SHA = re.compile(r'^[0-9a-f]{64}$')
_BUILDING_ID = re.compile(r'beirut001:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def load_profiles(path) -> dict:
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    errors = validate_profiles(doc)
    if errors:
        raise ValueError('invalid reference profiles: ' + '; '.join(errors[:5]))
    return doc


def to_pixels(norm_points, image_size_px):
    """Normalized [u, v] pairs (or one [u0, v0, u1, v1] box) to pixel coordinates."""
    w, h = image_size_px
    flat = list(norm_points)
    if flat and not isinstance(flat[0], (list, tuple)):
        return [flat[0] * w, flat[1] * h, flat[2] * w, flat[3] * h]
    return [[u * w, v * h] for u, v in flat]


def observations_for(doc, family=None, photo_id=None):
    return [o for o in doc['observations']
            if (family is None or o['family'] == family) and (photo_id is None or o['photo_id'] == photo_id)]


def family_estimates(doc, family) -> dict:
    """Central estimates (metres/ratios) of a family's ``parameters_m``; bounds stay in the document."""
    return {k: v['est'] for k, v in doc['families'][family]['parameters_m'].items()}


def _unit(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v) and 0.0 <= v <= 1.0


def _range_errors(obj, where, errors):
    """Every {est, lo, hi} dict anywhere under obj must be finite, ordered and flagged as an estimate."""
    if isinstance(obj, dict):
        if {'est', 'lo', 'hi'} <= obj.keys():
            vals = (obj['lo'], obj['est'], obj['hi'])
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vals):
                errors.append(f'{where}: non-finite bounds')
            elif not obj['lo'] <= obj['est'] <= obj['hi']:
                errors.append(f'{where}: need lo <= est <= hi, got {vals}')
            if obj.get('basis') not in ('estimated', 'estimated_from_image', 'observed'):
                errors.append(f'{where}: basis missing')
        for k, v in obj.items():
            _range_errors(v, f'{where}.{k}', errors)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _range_errors(v, f'{where}[{i}]', errors)


def _box_ok(b) -> bool:
    return isinstance(b, list) and len(b) == 4 and all(_unit(x) for x in b) and b[0] < b[2] and b[1] < b[3]


def validate_profiles(doc) -> list:
    errors = []
    if doc.get('schema') != SCHEMA:
        return [f'schema must be {SCHEMA}']
    photos = {p['photo_id']: p for p in doc.get('photos', [])}
    if not photos:
        errors.append('no photos')
    for pid, p in photos.items():
        for key in ('file_sha256', 'photo_source_sha256'):
            if not _SHA.match(str(p.get(key, ''))):
                errors.append(f'photo {pid}: {key} is not a sha256')
        if p.get('license') != 'CC BY-SA 4.0' or 'KartaView' not in str(p.get('attribution', '')):
            errors.append(f'photo {pid}: license/attribution not preserved')
        if not p.get('source_url') or not p.get('capture_local'):
            errors.append(f'photo {pid}: missing source_url or capture date')
        if p.get('original_untouched') is not True:
            errors.append(f'photo {pid}: originals must stay untouched')
    families = doc.get('families', {})
    _range_errors(families, 'families', errors)
    proven = {x['source_id']: x['photo_id'] for x in doc.get('identity', {}).get('proven', [])}
    candidates = {c['source_id'] for c in doc.get('identity', {}).get('candidates_only_not_assigned', [])}
    for i, o in enumerate(doc.get('observations', [])):
        w = f'observations[{i}] ({o.get("photo_id")}/{o.get("family")})'
        if o.get('photo_id') not in photos: errors.append(f'{w}: unknown photo')
        if o.get('family') not in families: errors.append(f'{w}: unknown family')
        poly = o.get('polygon_norm', [])
        if len(poly) < 3 or not all(len(pt) == 2 and _unit(pt[0]) and _unit(pt[1]) for pt in poly):
            errors.append(f'{w}: polygon must have >= 3 finite points inside [0, 1]')
        if not _box_ok(o.get('bbox_norm')): errors.append(f'{w}: bad bbox_norm')
        for j, op in enumerate(o.get('openings', [])):
            if not _box_ok(op.get('bbox_norm')): errors.append(f'{w}: openings[{j}] bad bbox')
        _range_errors({'floors': o.get('visible_floors'), 'bays': o.get('visible_bays')}, w, errors)
        if 'occlusion' not in o: errors.append(f'{w}: occlusion must be stated (may be "unknown")')
        # identity: only reviewed IDs may appear, and only on the photo that was reviewed
        for sid in _BUILDING_ID.findall(str(o.get('identity_status', ''))):
            if sid in candidates and sid not in proven:
                errors.append(f'{w}: candidate {sid} assigned as identity')
            elif sid in proven and o.get('photo_id') != proven[sid]:
                errors.append(f'{w}: {sid} is proven for {proven[sid]} only')
    for t in doc.get('cross_view_ties', []):
        if not set(t.get('photos', [])) <= set(photos) or t.get('family') not in families:
            errors.append(f'tie {t.get("id")}: unknown photo or family')
    if not doc.get('unseen_and_unknown', {}).get('unknown_items'):
        errors.append('unseen faces must be stated as unknown')
    return errors
