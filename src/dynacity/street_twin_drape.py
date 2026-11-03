"""Clip illustrative paint to imported asphalt and follow its triangle planes.

The rendered road mesh is the placement reference, not a new elevation survey.
Source lane XY, canonical IDs and evidence survive; unsupported paint is omitted.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.strtree import STRtree

from .engine_asset import export_glb


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_asphalt_triangles(path, bounds=None, expected_coordinates=None):
    """Read this pipeline's GLBs; fail closed on unsupported node transforms."""
    data = Path(path).read_bytes()
    magic, version, size = struct.unpack_from('<4sII', data)
    if magic != b'glTF' or version != 2 or size != len(data):
        raise ValueError('Invalid GLB header')
    offset, doc, binary = 12, None, None
    while offset < size:
        length, kind = struct.unpack_from('<I4s', data, offset)
        chunk = data[offset + 8:offset + 8 + length]
        if kind == b'JSON':
            doc = json.loads(chunk)
        elif kind == b'BIN\0':
            binary = chunk
        offset += length + 8
    if doc is None or binary is None:
        raise ValueError('GLB must have embedded JSON and binary')
    if expected_coordinates is not None:
        actual = doc.get('extras', {}).get('coordinates', {})
        for field in ('crs', 'origin_utm_m', 'axes', 'units'):
            if actual.get(field) != expected_coordinates.get(field):
                raise ValueError('Asphalt and paint coordinate references differ: ' + field)
    types = {5121: '<u1', 5123: '<u2', 5125: '<u4', 5126: '<f4'}
    components = {'SCALAR': 1, 'VEC3': 3}

    def accessor(index):
        item = doc['accessors'][index]
        if 'sparse' in item or item.get('normalized'):
            raise ValueError('Unsupported accessor encoding')
        view = doc['bufferViews'][item['bufferView']]
        if view.get('buffer', 0) != 0:
            raise ValueError('External buffers are unsupported')
        dtype = np.dtype(types[item['componentType']])
        width = components[item['type']]
        stride = view.get('byteStride', dtype.itemsize * width)
        start = view.get('byteOffset', 0) + item.get('byteOffset', 0)
        return np.ndarray((item['count'], width), dtype=dtype, buffer=binary,
                          offset=start, strides=(stride, dtype.itemsize)).copy()

    result = []
    for node in doc.get('nodes', []):
        if 'mesh' not in node:
            if node.get('children'):
                raise ValueError('Hierarchical GLB transforms require explicit flattening')
            continue
        if any(key in node for key in ('matrix', 'translation', 'rotation', 'scale', 'children')):
            raise ValueError('Transformed GLB nodes require explicit flattening')
        for primitive in doc['meshes'][node['mesh']]['primitives']:
            if doc['materials'][primitive.get('material', 0)].get('name') != 'Asphalt':
                continue
            if primitive.get('mode', 4) != 4:
                raise ValueError('Asphalt must use triangle primitives')
            positions = accessor(primitive['attributes']['POSITION']).astype(float)
            # glTF east/up/south -> source east/north/up, metres.
            positions = positions[:, [0, 2, 1]]
            positions[:, 1] *= -1
            triangles = positions[accessor(primitive['indices']).reshape(-1, 3)]
            if bounds is not None:
                low, high = triangles[:, :, :2].min(axis=1), triangles[:, :, :2].max(axis=1)
                keep = ((high[:, 0] >= bounds[0]) & (high[:, 1] >= bounds[1]) &
                        (low[:, 0] <= bounds[2]) & (low[:, 1] <= bounds[3]))
                triangles = triangles[keep]
            result.extend(triangles)
    return np.asarray(result, dtype=float).reshape(-1, 3, 3)


def _polygons(geometry):
    if geometry.geom_type == 'Polygon':
        return [geometry]
    return [part for item in getattr(geometry, 'geoms', []) for part in _polygons(item)]


def drape_scene(scene, surfaces, *, lift_m=.02, max_vertical_delta_m=2.):
    """Project each clipped piece onto the closest compatible asphalt layer."""
    if not np.isfinite([lift_m, max_vertical_delta_m]).all() or lift_m <= 0 or max_vertical_delta_m <= 0:
        raise ValueError('Drape dimensions must be finite and positive')
    surfaces = np.asarray(surfaces, dtype=float).reshape(-1, 3, 3)
    if not np.isfinite(surfaces).all():
        raise ValueError('Surface vertices must be finite')
    polygons, planes = [], []
    for triangle in surfaces:
        polygon = Polygon(triangle[:, :2])
        if polygon.area <= 1e-10:
            continue
        plane = np.linalg.solve(np.column_stack((triangle[:, :2], np.ones(3))), triangle[:, 2])
        polygons.append(polygon)
        planes.append(plane)
    tree = STRtree(polygons)
    output = copy.deepcopy(scene)
    output['meshes'] = []
    report = {'input_triangles': 0, 'output_triangles': 0, 'input_area_m2': 0.,
              'covered_area_m2': 0., 'omitted_area_m2': 0., 'omitted_features': [],
              'precision_omitted_area_m2': 0., 'precision_omitted_triangles': 0,
              'minimum_triangle_altitude_m': .001,
              'max_applied_vertical_delta_m': 0., 'surface_triangles': len(polygons),
              'lift_m': lift_m, 'max_vertical_delta_m': max_vertical_delta_m}
    for mesh in scene['meshes']:
        if mesh['kind'] != 'marking':
            raise ValueError('Drape input must contain paint only')
        vertices = np.asarray(mesh['vertices'], dtype=float)
        if not np.isfinite(vertices).all():
            raise ValueError('Paint vertices must be finite')
        out_vertices, out_faces = [], []
        for face in mesh['triangles']:
            triangle = vertices[face]
            paint = Polygon(triangle[:, :2])
            report['input_triangles'] += 1
            report['input_area_m2'] += paint.area
            if paint.area <= 1e-10:
                continue
            source_plane = np.linalg.solve(np.column_stack((triangle[:, :2], np.ones(3))), triangle[:, 2])
            centre = np.r_[triangle[:, :2].mean(axis=0), 1.]
            candidates = sorted(tree.query(paint), key=lambda i: (abs(float(centre @ (planes[i] - source_plane))), int(i)))
            remaining = paint
            for index in candidates:
                clipped = remaining.intersection(polygons[index])
                if clipped.is_empty or clipped.area <= 1e-10:
                    continue
                xy = shapely.get_coordinates(clipped)
                delta = np.column_stack((xy, np.ones(len(xy)))) @ (planes[index] - source_plane)
                if np.max(np.abs(delta)) > max_vertical_delta_m:
                    continue
                report['max_applied_vertical_delta_m'] = max(report['max_applied_vertical_delta_m'], float(np.max(np.abs(delta))))
                for polygon in _polygons(clipped):
                    # Constrained triangulation retains concavities and holes.
                    for piece in shapely.constrained_delaunay_triangles(polygon).geoms:
                        if piece.area <= 1e-10:
                            continue
                        xy = np.asarray(piece.exterior.coords)[:3]
                        a, b = xy[1] - xy[0], xy[2] - xy[0]
                        if a[0] * b[1] - a[1] * b[0] < 0:
                            xy = xy[[0, 2, 1]]
                        z = np.column_stack((xy, np.ones(3))) @ planes[index] + lift_m
                        # Quantize once to actual GLB precision before measuring
                        # triangles. Sub-millimetre clipping slivers otherwise
                        # collapse at this city's kilometre-sized coordinates
                        # and are silently discarded by the Unreal importer.
                        points = np.column_stack((xy, z)).astype(np.float32).astype(float)
                        cross = np.cross(points[1] - points[0], points[2] - points[0])
                        longest = max(np.linalg.norm(points[j] - points[i]) for i, j in ((0, 1), (1, 2), (2, 0)))
                        altitude = np.linalg.norm(cross) / max(longest, 1e-20)
                        if altitude < .001:
                            report['precision_omitted_triangles'] += 1
                            report['precision_omitted_area_m2'] += piece.area
                            continue
                        start = len(out_vertices)
                        out_vertices.extend(points.tolist())
                        out_faces.append([start, start + 1, start + 2])
                        report['covered_area_m2'] += piece.area
                remaining = remaining.difference(clipped)
                if remaining.is_empty:
                    break
        if out_faces:
            item = copy.deepcopy(mesh)
            item.update(vertices=out_vertices, triangles=out_faces)
            item['metadata']['drape'] = {'reference': 'existing_imported_asphalt', 'lift_m': lift_m}
            output['meshes'].append(item)
        else:
            report['omitted_features'].append(mesh['id'])
    report['output_triangles'] = sum(len(item['triangles']) for item in output['meshes'])
    report['omitted_area_m2'] = max(0., report['input_area_m2'] - report['covered_area_m2'])
    output['limitations'].append('Paint clipped and draped onto imported asphalt; surface geometry is not a survey.')
    return output, report


def export_draped_finish(manifest_path, surface_paths, output_dir, *, max_vertical_delta_m=2.):
    manifest_path, output = Path(manifest_path), Path(output_dir)
    original = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest = copy.deepcopy(original)
    output.mkdir(parents=True, exist_ok=True)
    manifest['drape_sources'] = [{'path': str(Path(path).resolve()), 'sha256': _sha(path)} for path in surface_paths]
    manifest['drape_input_manifest_sha256'] = _sha(manifest_path)
    for tile in manifest['tiles']:
        if not tile.get('asset'):
            continue
        source = manifest_path.parent / tile['scene']
        source_asset = manifest_path.parent / tile['asset']
        if _sha(source_asset) != tile['sha256']:
            raise ValueError('Input finish GLB checksum mismatch')
        scene = json.loads(source.read_text(encoding='utf-8'))
        points = np.concatenate([np.asarray(mesh['vertices'])[:, :2] for mesh in scene['meshes']])
        bounds = [*points.min(axis=0), *points.max(axis=0)]
        surfaces = np.concatenate([read_asphalt_triangles(path, bounds, scene['coordinates']) for path in surface_paths])
        draped, report = drape_scene(scene, surfaces, max_vertical_delta_m=max_vertical_delta_m)
        signature = hashlib.sha256(json.dumps({'scene': draped, 'sources': manifest['drape_sources'],
                    'max_vertical_delta_m': max_vertical_delta_m}, sort_keys=True, allow_nan=False).encode()).hexdigest()
        asset = output / ('street-draped-' + signature + '.glb')
        if not draped['meshes']:
            raise ValueError('No supported paint remains on this tile')
        if not asset.exists():
            export_glb(draped, asset, include_calibration=False)
        checksum = asset.with_suffix('.glb.sha256')
        if checksum.exists() and checksum.read_text().strip() != _sha(asset):
            raise ValueError('Cached draped asset checksum mismatch')
        checksum.write_text(_sha(asset), encoding='ascii')
        scene_path = asset.with_suffix('.scene.json')
        scene_path.write_text(json.dumps(draped, separators=(',', ':'), allow_nan=False), encoding='utf-8')
        tile.update(asset=asset.name, scene=scene_path.name, sha256=_sha(asset), content_hash=signature,
                    triangle_count=report['output_triangles'], paint_feature_count=len(draped['meshes']), drape=report)
    target = output / 'street-twin-finish.json'
    target.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest
