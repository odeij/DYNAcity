"""Prepare a provisional, textured OBJ in city-local coordinates.

The caller must explicitly assert the unverified source CRS. Ellipsoidal height
is retained; this does not establish a match to the city's vertical datum.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from pyproj import Transformer


def prepare(source, output, origin, *, assume_ecef=False):
    if not assume_ecef:
        raise ValueError('Explicitly acknowledge the unverified EPSG:4978 assumption')
    source, output = Path(source).resolve(), Path(output).resolve()
    origin = np.asarray(origin, dtype=float)
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError('Expected a finite three-coordinate city origin')
    if output.exists():
        raise FileExistsError(output)
    material, texture = source.with_suffix('.mtl'), source.with_suffix('.jpg')
    if not source.is_file() or not material.is_file() or not texture.is_file():
        raise FileNotFoundError('Expected OBJ and its same-named MTL and JPG')
    if f'map_Kd {texture.name}' not in material.read_text(encoding='utf-8', errors='replace'):
        raise ValueError('Material does not reference the expected local texture')
    vertices = []
    with source.open(encoding='utf-8', errors='strict') as f:
        for line in f:
            if line.startswith('v '):
                vertices.append([float(v) for v in line.split()[1:4]])
    xyz = np.asarray(vertices, dtype=float)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or not len(xyz) or not np.isfinite(xyz).all():
        raise ValueError('OBJ must contain finite vertices')
    to_geo = Transformer.from_crs(4978, 4979, always_xy=True)
    to_utm = Transformer.from_crs(4326, 32636, always_xy=True)

    def project(points):
        lon, lat, height = to_geo.transform(*points.T)
        x, y = to_utm.transform(lon, lat)
        return np.column_stack((x, y, height)) - origin

    positions = project(xyz)
    centre = xyz.mean(axis=0)
    delta = np.eye(3) * 0.5
    jac = np.column_stack([(project(np.array([centre + d]))[0] -
                            project(np.array([centre - d]))[0]) for d in delta])
    normal_matrix = np.linalg.inv(jac).T
    output.mkdir(parents=True)
    counts = dict(v=0, vn=0, vt=0, f=0)
    face_uv = hashlib.sha256()
    with source.open(encoding='utf-8') as inp, (output / source.name).open('x', encoding='utf-8', newline='\n') as out:
        out.write('# Provisional EPSG:4978 assumption; ellipsoid heights; not verified\n')
        for line in inp:
            kind = line.split(' ', 1)[0].strip()
            if kind == 'v':
                fields = line.split()
                value = positions[counts['v']]
                suffix = ' ' + ' '.join(fields[4:]) if len(fields) > 4 else ''
                out.write('v %.6f %.6f %.6f%s\n' % (*value, suffix))
                counts['v'] += 1
            elif kind == 'vn':
                vector = normal_matrix @ np.asarray([float(v) for v in line.split()[1:4]])
                length = np.linalg.norm(vector)
                if not np.isfinite(length) or length < 1e-12:
                    raise ValueError('Invalid source normal')
                out.write('vn %.9f %.9f %.9f\n' % tuple(vector / length))
                counts['vn'] += 1
            else:
                out.write(line)
                if kind in ('vt', 'f'):
                    counts[kind] += 1
                    face_uv.update(line.encode('utf-8'))
    shutil.copy2(material, output / material.name)
    shutil.copy2(texture, output / texture.name)
    with source.open('rb') as f:
        source_hash = hashlib.file_digest(f, 'sha256').hexdigest()
    metadata = {'source': str(source), 'source_sha256': source_hash, 'counts': counts,
                'face_uv_stream_sha256': face_uv.hexdigest(), 'city_origin': origin.tolist(),
                'source_crs_assumption': 'EPSG:4978, unverified',
                'output': 'UTM 36N XY and WGS84 ellipsoidal heights relative to city origin',
                'normals': 'inverse transpose of coordinate Jacobian at mesh centre',
                'purpose': 'isolated diagnostic; not a verified alignment or city replacement'}
    (output / 'provisional.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--origin', nargs=3, type=float, required=True)
    parser.add_argument('--assume-ecef', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output, args.origin, assume_ecef=args.assume_ecef), indent=2))


if __name__ == '__main__':
    main()
