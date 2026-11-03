"""Read vertex positions from batched city GLBs and recover per-building edges.

Batched GLBs hold one mesh per material slot; ``objects.json`` names each source
building's vertex range within a named mesh. Only POSITION and indices are read.
"""
from __future__ import annotations

import json
import struct

import numpy as np

_COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_WIDTH = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}


def read_glb(path):
    """Return (json_doc, bin_bytes)."""
    data = open(path, 'rb').read()
    magic, _version, _length = struct.unpack_from('<4sII', data, 0)
    if magic != b'glTF':
        raise ValueError('not a GLB file')
    offset, doc, blob = 12, None, b''
    while offset < len(data):
        size, kind = struct.unpack_from('<I4s', data, offset)
        chunk = data[offset + 8:offset + 8 + size]
        if kind == b'JSON':
            doc = json.loads(chunk)
        elif kind == b'BIN\x00':
            blob = chunk
        offset += 8 + size
    return doc, blob


def accessor(doc, blob, index):
    spec = doc['accessors'][index]
    view = doc['bufferViews'][spec['bufferView']]
    dtype = np.dtype(_COMPONENT[spec['componentType']])
    width = _WIDTH[spec['type']]
    stride = view.get('byteStride') or dtype.itemsize * width
    start = view.get('byteOffset', 0) + spec.get('byteOffset', 0)
    count = spec['count']
    raw = np.frombuffer(blob, dtype=np.uint8, count=stride * (count - 1) + dtype.itemsize * width, offset=start)
    rows = np.lib.stride_tricks.as_strided(raw, shape=(count, dtype.itemsize * width), strides=(stride, 1))
    return np.ascontiguousarray(rows).view(dtype).reshape(count, width)


def mesh_primitives(path):
    """{mesh name: (positions (N,3) east/north/up metres, triangles (M,3) int)} with primitives merged."""
    doc, blob = read_glb(path)
    out = {}
    for mesh in doc['meshes']:
        pos, tri, base = [], [], 0
        for prim in mesh['primitives']:
            p = accessor(doc, blob, prim['attributes']['POSITION']).astype(float)
            p = np.stack([p[:, 0], -p[:, 2], p[:, 1]], axis=1)  # glTF Y-up, -Z north -> east, north, up
            idx = accessor(doc, blob, prim['indices']).reshape(-1, 3).astype(np.int64)
            pos.append(p)
            tri.append(idx + base)
            base += len(p)
        out[mesh.get('name', str(len(out)))] = (np.concatenate(pos), np.concatenate(tri))
    return out


def vertical_edges(positions, triangles, min_height_m=2.0, merge_m=0.15):
    """Corner columns of a building: unique XY positions with their (base_z, top_z).

    Source buildings are extruded footprints, so every wall corner is a vertical
    edge shared by wall triangles. Returns an (K, 4) array of x, y, z_base, z_top.
    """
    keys = np.round(positions[:, :2] / merge_m).astype(np.int64)
    columns = {}
    for (kx, ky), z in zip(map(tuple, keys), positions[:, 2]):
        lo, hi = columns.get((kx, ky), (z, z))
        columns[(kx, ky)] = (min(lo, z), max(hi, z))
    rows = [(kx * merge_m, ky * merge_m, lo, hi) for (kx, ky), (lo, hi) in columns.items() if hi - lo >= min_height_m]
    return np.array(rows, dtype=float).reshape(-1, 4)
