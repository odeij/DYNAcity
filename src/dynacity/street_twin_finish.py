"""Illustrative lane paint on the actual shared SUMO/Unreal road surfaces.

Paint is a repeatable visual finish, not a record of surveyed Beirut markings.
No crossing, turn arrow, kerb or street furniture is inferred. A default three
metre clearance at lane ends avoids painting into unsupplied junction geometry.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .engine_asset import export_glb
from .street_twin_unreal import build_unreal_scenes

SCHEMA = 'dynacity-street-twin-finish/1'
DEFAULT_STYLE = {'paint_width_m': .12, 'edge_inset_m': .25, 'paint_lift_m': .02,
                 'dash_m': 3., 'gap_m': 6., 'junction_clearance_m': 3.}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _style(values):
    style = dict(DEFAULT_STYLE)
    if values:
        if set(values) - set(style):
            raise ValueError('Unknown street finish style field')
        style.update(values)
    for key, value in style.items():
        if isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError('Street finish dimensions must be finite nonnegative numbers')
        style[key] = float(value)
    if style['paint_width_m'] <= 0 or style['dash_m'] <= 0:
        raise ValueError('Paint width and dash length must be positive')
    return style


def _strip(mesh, *, role, side, dashed, style):
    """Cut paint from the lane's existing ruled surface, retaining its grade."""
    vertices = np.asarray(mesh['vertices'], dtype=float).reshape(-1, 2, 3)
    # The road export orders each cross-section as left, right.
    left, right = vertices[:, 0], vertices[:, 1]
    width = float(mesh['metadata']['width_m'])
    paint = style['paint_width_m'] / width
    inset = style['edge_inset_m'] / width if role == 'edge_line' else 0.
    if paint >= 1 or inset + paint >= 1:
        raise ValueError('Paint is wider than its source lane')
    # Dividers occupy the source lane up to its exact left boundary. This
    # avoids extrapolating an unknown adjacent road surface or duplicating paint.
    high = 1 - inset if side == 'left' else inset + paint
    low = high - paint
    first = right + low * (left - right)
    second = right + high * (left - right)
    centre = (first + second) / 2
    station = np.r_[0., np.cumsum(np.linalg.norm(np.diff(centre[:, :2], axis=0), axis=1))]
    keep = np.r_[True, np.diff(station) > 1e-8]
    station, first, second = station[keep], first[keep], second[keep]
    clearance = style['junction_clearance_m']
    start, end = clearance, float(station[-1]) - clearance
    if end <= start:
        return None
    intervals = []
    if dashed:
        cursor = start
        while cursor < end:
            intervals.append((cursor, min(cursor + style['dash_m'], end)))
            cursor += style['dash_m'] + style['gap_m']
    else:
        intervals.append((start, end))
    out_vertices, out_faces = [], []
    for a, b in intervals:
        if b - a < .01:
            continue
        samples = np.r_[a, station[(station > a + 1e-7) & (station < b - 1e-7)], b]
        a_side = np.column_stack([np.interp(samples, station, first[:, i]) for i in range(3)])
        b_side = np.column_stack([np.interp(samples, station, second[:, i]) for i in range(3)])
        # Left then right keeps the same upward orientation as the source lane.
        part = np.stack([b_side, a_side], axis=1).reshape(-1, 3)
        part[:, 2] += style['paint_lift_m']
        offset = len(out_vertices)
        out_vertices.extend(part.round(6).tolist())
        for i in range(len(samples) - 1):
            n = offset + 2 * i
            out_faces.extend([[n, n + 1, n + 2], [n + 1, n + 3, n + 2]])
    if not out_faces:
        return None
    metadata = mesh['metadata']
    identifier = metadata['lane_id'] + '/finish/' + role + '/' + side
    return {'id': identifier, 'kind': 'marking', 'vertices': out_vertices, 'triangles': out_faces,
            'metadata': {'road_id': metadata['road_id'], 'edge_id': metadata['edge_id'],
                         'lane_id': metadata['lane_id'], 'sumo_lane_id': metadata['sumo_lane_id'],
                         'finish_role': role, 'boundary': side, 'dashed': dashed,
                         'evidence': {'status': 'assumed', 'source_ids': ['illustrative_finish_style']},
                         'style': style, 'junction_clearance_m': clearance}}


def build_street_twin_finishes(twin, *, net_xml, tile_ids=None, style=None, include_asphalt=True):
    """Build selected tile scenes with asphalt and source-aligned illustrative paint."""
    if net_xml is None:
        raise ValueError('Street finishes require actual SUMO net_xml')
    style = _style(style)
    # Input roads remain read-only; selecting one tile must not copy an entire
    # city's dense geometry and source evidence before discarding most of it.
    selected = dict(twin)
    if tile_ids is not None:
        wanted = set(tile_ids)
        found = {tile['id'] for tile in twin['tiles']}
        if wanted - found:
            raise ValueError('Unknown finish tile ID')
        selected['tiles'] = [tile for tile in twin['tiles'] if tile['id'] in wanted]
        road_ids = {road_id for tile in selected['tiles'] for road_id in tile['road_ids']}
        selected['roads'] = [road for road in twin['roads'] if road['id'] in road_ids]
    scenes, omitted = build_unreal_scenes(selected, net_xml=net_xml)
    roads = {road['id']: road for road in selected['roads']}
    for scene in scenes.values():
        source_meshes = scene['meshes']
        paint_meshes = []
        for mesh in source_meshes:
            metadata = mesh['metadata']
            road = roads[metadata['road_id']]
            edge = next(edge for edge in road['edges'] if edge['id'] == metadata['edge_id'])
            lane = next(lane for lane in edge['lanes'] if lane['id'] == metadata['lane_id'])
            index = lane['index']
            last_index = len(edge['lanes']) - 1
            treatments = []
            if index == 0:
                treatments.append(('edge_line', 'right', False))
            if index < last_index:
                treatments.append(('lane_divider', 'left', True))
            elif road['oneway']:
                treatments.append(('edge_line', 'left', False))
            elif edge['direction'] == 1:
                treatments.append(('centre_separator', 'left', True))
            for role, side, dashed in treatments:
                paint = _strip(mesh, role=role, side=side, dashed=dashed, style=style)
                if paint:
                    paint_meshes.append(paint)
        scene['meshes'] = (source_meshes if include_asphalt else []) + paint_meshes
        scene['limitations'] = scene['limitations'] + [
            'Paint position, width, colour, dash pattern and junction clearance are illustrative assumptions; not surveyed markings.',
            'No crossings, turn arrows, stop lines, kerbs or furniture are inferred.',
            f"Paint follows source lane elevation plus an assumed {style['paint_lift_m']:g} metre rendering offset.",
        ]
    return scenes, omitted


def export_street_twin_finish(twin, output_dir, *, net_xml, tile_ids=None, style=None, include_asphalt=True):
    """Write consumable GLBs and scene JSON; preserve existing generated assets."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    scenes, omitted = build_street_twin_finishes(twin, net_xml=net_xml, tile_ids=tile_ids,
                                                style=style, include_asphalt=include_asphalt)
    entries = []
    for tile_id, scene in sorted(scenes.items()):
        signature = _hash({'schema': SCHEMA, 'scene': scene})
        record = {'tile_id': tile_id, 'content_hash': signature, 'asset': None,
                  'asphalt_lane_count': sum(m['kind'] == 'road' for m in scene['meshes']),
                  'paint_feature_count': sum(m['kind'] == 'marking' for m in scene['meshes'])}
        if scene['meshes']:
            asset = output / ('street-finish-' + signature + '.glb')
            checksum = asset.with_suffix('.glb.sha256')
            if asset.exists():
                if not checksum.exists() or checksum.read_text().strip() != hashlib.sha256(asset.read_bytes()).hexdigest():
                    raise ValueError('Existing finish asset failed checksum')
            else:
                export_glb(scene, asset, include_calibration=False)
                checksum.write_text(hashlib.sha256(asset.read_bytes()).hexdigest(), encoding='ascii')
            source = asset.with_suffix('.scene.json')
            source.write_text(json.dumps(scene, separators=(',', ':'), allow_nan=False), encoding='utf-8')
            record.update(asset=asset.name, scene=source.name, sha256=checksum.read_text().strip(),
                          triangle_count=sum(len(m['triangles']) for m in scene['meshes']))
        entries.append(record)
    manifest = {'schema': SCHEMA, 'twin_content_hash': twin.get('content_hash', _hash(twin)),
                'coordinates': twin['coordinates'], 'style': _style(style),
                'evidence': {'status': 'assumed', 'source_ids': ['illustrative_finish_style']},
                'sources': [{'id': 'illustrative_finish_style', 'kind': 'assumption',
                             'title': 'Illustrative lane-paint styling; not a local markings survey'}],
                'tiles': entries, 'omitted': omitted, 'surveyed_markings': False,
                'limitations': next(iter(scenes.values()))['limitations'] if scenes else []}
    (output / 'street-twin-finish.json').write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    return manifest
