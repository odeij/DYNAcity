"""Prepare a private, read-only Beirut 001 + BBED hover pilot.

Requires rhino3dm and the project's geometry dependencies. Inputs/outputs must
stay outside Git. Registration is an explicit inferred rigid-transform JSON;
no CRS is silently assigned to the Rhino source. Serve output on localhost.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import rhino3dm as rhino
from shapely.geometry import MultiPoint, shape
from pyproj import CRS

from dynacity.beirut_overlay import match_footprints


def build(model_path, bbed_path, registration_path, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    model = rhino.File3dm.Read(str(model_path))
    if model is None:
        raise ValueError('Rhino model could not be read')
    collection = json.loads(Path(bbed_path).read_text())
    source_crs = CRS.from_user_input(collection.get('crs', {}).get('properties', {}).get('name', ''))
    if source_crs != CRS.from_epsg(32636):
        raise ValueError('This pilot requires BBED in EPSG:32636; reproject explicitly first')
    source_features = [f for f in collection['features'] if f.get('geometry')]
    excluded = [f.get('properties', {}).get('OBJECTID') for f in source_features if not shape(f['geometry']).is_valid]
    features = [f for f in source_features if shape(f['geometry']).is_valid]
    polygons = [shape(f['geometry']) for f in features]
    registration = json.loads(Path(registration_path).read_text())
    rotation = np.asarray(registration['rotation_matrix'], dtype=float)
    translation = np.asarray(registration['translation_xy_m'], dtype=float)
    if rotation.shape != (2, 2) or translation.shape != (2,) or not np.isfinite(rotation).all() or not np.isfinite(translation).all():
        raise ValueError('Invalid rigid registration')
    if not np.allclose(rotation.T @ rotation, np.eye(2), atol=1e-6) or np.linalg.det(rotation) < .999:
        raise ValueError('Registration must preserve scale and orientation')
    positions, indices, owners, records, footprints = [], [], [], [], []
    vertex_offset = face_offset = 0
    for obj in model.Objects:
        g = obj.Geometry
        if model.Layers[obj.Attributes.LayerIndex].Name != '05_BUILDINGS':
            continue
        kind = type(g).__name__
        meshes = [f.GetMesh(rhino.MeshType.Render) for f in g.Faces] if kind == 'Brep' else [g.GetMesh(rhino.MeshType.Render)] if kind == 'Extrusion' else [g] if kind == 'Mesh' else []
        vertices, triangles = [], []
        local_offset = 0
        for mesh in meshes:
            if mesh is None or not len(mesh.Faces):
                continue
            v = np.asarray([(p.X, p.Y, p.Z) for p in mesh.Vertices], dtype=np.float32)
            f = []
            for face in mesh.Faces:
                f.append(face[:3])
                if face[2] != face[3]:
                    f.append((face[0], face[2], face[3]))
            vertices.append(v)
            triangles.append(np.asarray(f, dtype=np.uint32) + local_offset)
            local_offset += len(v)
        if not vertices:
            continue
        v, f = np.concatenate(vertices), np.concatenate(triangles)
        footprint = MultiPoint(v[:, :2]).convex_hull
        # Matching proxy only: preserve the original mesh for rendering.
        xy = np.asarray(footprint.exterior.coords) @ rotation.T + translation if footprint.geom_type == 'Polygon' else None
        footprints.append(MultiPoint(xy).convex_hull if xy is not None else footprint)
        bounds = [*v.min(0).astype(float), *v.max(0).astype(float)]
        record = {'id': str(obj.Attributes.Id), 'name': obj.Attributes.Name or None,
                  'kind': kind, 'layer': '05_BUILDINGS', 'bounds': bounds,
                  'index_start': face_offset * 3, 'index_count': len(f) * 3,
                  'triangles': len(f), 'vertices': len(v), 'source_date': 'January 2020 release'}
        records.append(record)
        # Rendering frame remains Rhino local XY/Z-up, mapped to Three Y-up.
        vv = v[:, [0, 2, 1]].copy(); vv[:, 2] *= -1
        positions.append(vv); indices.append(f + vertex_offset)
        owners.append(np.full(len(f), len(records) - 1, dtype=np.uint32))
        vertex_offset += len(v); face_offset += len(f)
    links = match_footprints(footprints, polygons)
    for rec, link in zip(records, links):
        rec['link'] = link
    lines = []
    # Draw BBED outlines above the linked model roof for visible plan comparison.
    # This visual placement is not a datum transformation or hydraulic terrain.
    reverse = {x['link'].get('bbed_index'): i for i, x in enumerate(records) if x['link']['status'] == 'provisional'}
    bbed = []
    for i, (feature, polygon) in enumerate(zip(features, polygons)):
        bbed.append({'properties': feature.get('properties', {}), 'model_index': reverse.get(i)})
        z = records[reverse[i]]['bounds'][5] + .6 if i in reverse else None
        if z is None:
            continue
        for part in polygon.geoms if polygon.geom_type == 'MultiPolygon' else [polygon]:
            for ring in [part.exterior, *part.interiors]:
                coords = (np.asarray(ring.coords) - translation) @ rotation
                for a, b in zip(coords[:-1], coords[1:]):
                    lines.extend([[float(a[0]), z, float(-a[1])], [float(b[0]), z, float(-b[1])]])
    for name, data, dtype in [('positions', positions, '<f4'), ('indices', indices, '<u4'), ('owners', owners, '<u4')]:
        np.concatenate(data).astype(dtype).tofile(output / f'{name}.bin')
    np.asarray(lines, dtype='<f4').tofile(output / 'outlines.bin')
    matched = sum(l['status'] == 'provisional' for l in links)
    focus = max((i for i, x in enumerate(records) if x['link']['status'] == 'provisional'), key=lambda i: records[i]['link']['iou'])
    hashes = {}
    for name, path in [('model', model_path), ('bbed', bbed_path)]:
        with Path(path).open('rb') as stream: hashes[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    payload = {'schema': 'dynacity-beirut-bbed-pilot/1', 'objects': records, 'bbed': bbed,
               'registration': registration, 'hashes': hashes, 'matched': matched,
               'unlinked': len(records) - matched, 'focus': focus, 'excluded_invalid_bbed_objectids': excluded,
               'attribution': 'Beirut 001: Salim al-Kadi and team, January 2020. BBED: Beirut Urban Lab, AUB / CNRS, ODbL.',
               'matching_note': 'Provisional geometric links: IoU ≥ 65%, centroid ≤ 8 m, runner-up gap ≥ 15 percentage points, one-to-one only. Registration inferred; not surveyed.'}
    (output / 'data.json').write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False))
    print(json.dumps({'objects': len(records), 'provisional_links': matched, 'unlinked': len(records) - matched, 'triangles': face_offset, 'output': str(output)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ['model', 'bbed', 'registration', 'output']: parser.add_argument('--' + flag, required=True, type=Path)
    args = parser.parse_args()
    build(args.model, args.bbed, args.registration, args.output)
