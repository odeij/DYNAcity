"""Bake the bundled Kenney GLBs into indexed meshes for the standalone viewer.

Run with python scripts/prepare_car_models.py (requires numpy and Pillow).
Only the offline conversion uses Pillow; the viewer needs no model loader.
"""
import base64
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'src/dynacity/assets/cars'


def bake(name):
    raw = (ROOT / f'{name}.glb').read_bytes()
    assert raw[:4] == b'glTF'
    length = struct.unpack_from('<I', raw, 12)[0]
    gltf = json.loads(raw[20:20 + length])
    start = 20 + length
    binary = raw[start + 8:]
    palette = Image.open(ROOT / 'colormap.png').convert('RGB')

    def accessor(i):
        a = gltf['accessors'][i]
        v = gltf['bufferViews'][a['bufferView']]
        dtype = {5126:'<f4', 5123:'<u2', 5125:'<u4'}[a['componentType']]
        width = {'SCALAR':1, 'VEC2':2, 'VEC3':3, 'VEC4':4}[a['type']]
        offset = v.get('byteOffset', 0) + a.get('byteOffset', 0)
        assert not a.get('sparse')
        itemsize = np.dtype(dtype).itemsize
        stride = v.get('byteStride', width * itemsize)
        return np.ndarray((a['count'], width), dtype=dtype, buffer=binary, offset=offset, strides=(stride, itemsize)).copy()

    positions, normals, colors, indices = [], [], [], []
    count = 0

    def visit(index, parent):
        nonlocal count
        node = gltf['nodes'][index]
        assert not any(k in node for k in ('rotation', 'scale', 'matrix'))
        translation = parent + np.asarray(node.get('translation', [0, 0, 0]))
        if 'mesh' in node:
            for primitive in gltf['meshes'][node['mesh']]['primitives']:
                assert primitive.get('mode', 4) == 4
                attrs = primitive['attributes']
                p = accessor(attrs['POSITION']) + translation
                n = accessor(attrs['NORMAL'])
                uv = accessor(attrs['TEXCOORD_0'])
                rgb = [palette.getpixel((min(palette.width-1, max(0, int(u*palette.width))),
                    min(palette.height-1, max(0, int(v*palette.height))))) for u, v in uv]
                positions.append(p[:, [2, 0, 1]])  # +x is the nose, +z is up
                normals.append(n[:, [2, 0, 1]])
                colors.extend(rgb)
                indices.append(accessor(primitive['indices']).ravel() + count)
                count += len(p)
        for child in node.get('children', []):
            visit(child, translation)

    for i in gltf['scenes'][gltf.get('scene', 0)]['nodes']:
        visit(i, np.zeros(3))
    pos = np.concatenate(positions)
    pos[:, :2] -= (pos[:, :2].min(0) + pos[:, :2].max(0)) / 2
    pos[:, 2] -= pos[:, 2].min()
    dimensions = [4.4, 1.85, 1.55] if name == 'sedan' else [4.6, 1.95, 1.85]
    scale = np.asarray(dimensions) / np.ptp(pos, axis=0)
    pos *= scale
    nor = np.concatenate(normals) / scale
    nor /= np.linalg.norm(nor, axis=1, keepdims=True)
    ind = np.concatenate(indices)
    assert count < 65536
    encode = lambda a, t: base64.b64encode(np.asarray(a, dtype=t).tobytes()).decode('ascii')
    return dict(positions=encode(pos, '<f4'), normals=encode(nor, '<f4'),
        colors=encode(colors, 'u1'), indices=encode(ind, '<u2'), vertices=count, triangles=len(ind)//3)


if __name__ == '__main__':
    models = {name:bake(name) for name in ('sedan', 'suv')}
    (ROOT / 'meshes.json').write_text(json.dumps(models, separators=(',', ':')) + '\n')
    for name, m in models.items():
        print(f"{name}: {m['vertices']} vertices, {m['triangles']} triangles")
