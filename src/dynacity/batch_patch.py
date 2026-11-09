"""Source-preserving batch patch with explicit triangle omission and optional material-slot split.

``patch_batch`` copies one batch node of a city GLB, drops exactly the listed triangles, and (optionally) moves named
triangle groups onto their own primitive/material so a batch that already carries a separate reference material
(e.g. the LGB tower's triangles) keeps that split. Positions, normals, UVs and every other triangle are untouched.
"""
from __future__ import annotations

import copy
import hashlib
import json
import struct
from pathlib import Path

import numpy as np

from .engine_city_batches import Glb


def patch_batch(asset, batch, omit, groups=None, output=None, primitive_names=('SourceExisting',)):
    """Write a one-node GLB for ``batch``.

    omit: absolute triangle indices (within the batch) to remove.
    groups: {material_name: absolute triangle indices} moved to their own primitive (and cloned material).
    Raises when an index is out of range, duplicated across ``omit``/groups, or when nothing would remain.
    """
    original = Glb(Path(asset))
    doc = copy.deepcopy(original.doc)
    binary = bytearray(original.binary)
    nodes = [n for n in doc['nodes'] if n.get('name') == batch]
    if len(nodes) != 1:
        raise ValueError('batch node missing or duplicated: ' + batch)
    node = nodes[0]
    mesh = copy.deepcopy(doc['meshes'][node['mesh']])
    if len(mesh['primitives']) != 1:
        raise ValueError('expected one indexed primitive')
    prim = mesh['primitives'][0]
    indices = original.accessor(prim['indices']).reshape(-1, 3)
    omit = np.unique(np.asarray(omit, dtype=np.int64))
    groups = {k: np.unique(np.asarray(v, dtype=np.int64)) for k, v in (groups or {}).items()}
    claimed = [omit] + list(groups.values())
    allidx = np.concatenate(claimed) if claimed else np.array([], dtype=np.int64)
    if len(allidx) != len(np.unique(allidx)):
        raise ValueError('triangle listed twice across omit/groups')
    if len(allidx) and (allidx.min() < 0 or allidx.max() >= len(indices)):
        raise ValueError('triangle index outside the batch')
    keep = np.ones(len(indices), bool)
    keep[allidx] = False
    main = np.flatnonzero(keep)
    if not len(main):
        raise ValueError('nothing would remain in the batch')
    base_material = copy.deepcopy(doc['materials'][prim['material']]) if 'material' in prim else {}

    def add_accessor(idx):
        array = np.ascontiguousarray(indices[idx].reshape(-1).astype('<u4'))
        while len(binary) % 4:
            binary.append(0)
        view = len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes})
        binary.extend(array.tobytes())
        doc['accessors'].append({'bufferView': view, 'componentType': 5125, 'count': len(array), 'type': 'SCALAR'})
        return len(doc['accessors']) - 1, hashlib.sha256(array.tobytes()).hexdigest()

    prims, report = [], {}
    main_acc, main_sha = add_accessor(main)
    main_prim = copy.deepcopy(prim)
    main_prim['indices'] = main_acc
    if base_material:
        base_material['name'] = primitive_names[0]
        doc['materials'].append(base_material)
        main_prim['material'] = len(doc['materials']) - 1
    prims.append(main_prim)
    report['main'] = {'triangles': int(len(main)), 'indices_sha256': main_sha, 'material': primitive_names[0]}
    for name, idx in groups.items():
        acc, sha = add_accessor(idx)
        gp = copy.deepcopy(prim)
        gp['indices'] = acc
        gm = copy.deepcopy(base_material) or {}
        gm['name'] = name
        doc['materials'].append(gm)
        gp['material'] = len(doc['materials']) - 1
        prims.append(gp)
        report[name] = {'triangles': int(len(idx)), 'indices_sha256': sha, 'material': name}
    mesh['primitives'] = prims
    doc['meshes'] = [mesh]
    node['mesh'] = 0
    doc['nodes'] = [node]
    doc['scenes'] = [{'nodes': [0]}]
    doc['scene'] = 0
    doc['buffers'] = [{'byteLength': len(binary)}]
    js = json.dumps(doc, separators=(',', ':')).encode()
    js += b' ' * (-len(js) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    raw = (struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(js) + 8 + len(binary)) + struct.pack('<I4s', len(js), b'JSON') + js
           + struct.pack('<I4s', len(binary), b'BIN\0') + bytes(binary))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    verify = Glb(output)
    vprims = verify.doc['meshes'][0]['primitives']
    counts = [len(verify.accessor(p['indices']).reshape(-1, 3)) for p in vprims]
    expected = [report['main']['triangles']] + [report[k]['triangles'] for k in groups]
    if counts != expected or sum(counts) + len(omit) != len(indices):
        raise RuntimeError('patched triangle accounting failed: %s vs %s' % (counts, expected))
    return {'source_asset': str(asset), 'source_sha256': hashlib.sha256(Path(asset).read_bytes()).hexdigest(), 'batch': batch,
            'asset': str(output), 'sha256': hashlib.sha256(raw).hexdigest(), 'omitted_triangles': int(len(omit)),
            'source_triangles': int(len(indices)), 'primitives': report, 'source_modified': False}
