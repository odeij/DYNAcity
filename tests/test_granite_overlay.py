import copy
import hashlib
import json
from pathlib import Path
import struct

import pytest
from pyproj import Transformer

from dynacity import granite_overlay as adapter


def make_glb(mutate=None, degenerate=False):
    positions, indices = [], []
    faces = [0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,3,7,6,3,6,2,0,4,7,0,7,3,1,2,6,1,6,5]
    for i in range(91):
        x, y, z = 2450+i*.04, 20., -1480.
        corners = [(x+dx, y+dy, z+dz) for dx,dy,dz in
                   [(0,0,0),(.02,0,0),(.02,.7,0),(0,.7,0),(0,0,.06),(.02,0,.06),(.02,.7,.06),(0,.7,.06)]]
        positions.extend(corners)
        indices.extend(v+i*8 for v in faces)
    if degenerate: positions[1] = positions[0]
    pos = b''.join(struct.pack('<fff', *p) for p in positions)
    ix = struct.pack('<'+'I'*len(indices), *indices)
    binary = pos+ix
    gltf = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
            'nodes': [{'mesh': 0}], 'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
            'buffers': [{'byteLength': len(binary)}],
            'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(pos)},
                            {'buffer': 0, 'byteOffset': len(pos), 'byteLength': len(ix)}],
            'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 728, 'type': 'VEC3'},
                          {'bufferView': 1, 'componentType': 5125, 'count': 3276, 'type': 'SCALAR'}]}
    if mutate: mutate(gltf)
    text = json.dumps(gltf).encode(); text += b' '*(-len(text)%4)
    raw = struct.pack('<III', 0x46546c67, 2, 28+len(text)+len(binary))
    raw += struct.pack('<II', len(text), 0x4e4f534a)+text+struct.pack('<II',len(binary),0x004e4942)+binary
    return raw, positions, indices


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    raw, positions, indices = make_glb()
    sha = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(adapter, 'ASSET_SHA256', sha)  # Synthetic fixture only; production remains pinned.
    (tmp_path/'asset.glb').write_bytes(raw)
    manifest = {'schema': 'dynacity-corridor-appearance-bundle/1', 'horizontal_crs': 'EPSG:32636',
                'coordinate_system': {'horizontal_crs': 'EPSG:32636', 'units': 'metres',
                    'gltf_axis': 'Y-up (X=East, Y=Up, Z=-North)', 'local_enu_axis': 'X=East, Y=North, Z=Up'},
                'origin_utm_m': adapter.ORIGIN.copy(), 'candidates': [{'key': 'granite91',
                    'source_building_uuid': 'beirut001:'+adapter.PARENT_ID, 'geometry_kind': 'optional_per_balcony_panels',
                    'source_coordinate_units': 'metres', 'source_photo_date': '2018-07-21',
                    'source_license': adapter.PHOTO_LICENSE, 'source_uri': adapter.PHOTO_URI,
                    'glb_asset': {'path': 'asset.glb', 'sha256': sha, 'size_bytes': len(raw)}}]}
    project = Transformer.from_crs(32636,4326,always_xy=True)
    ring = [list(project.transform(adapter.ORIGIN[0]+x, adapter.ORIGIN[1]+y)) for x,y in
            [(2449.8,1479.7),(2454,1479.7),(2454,1480.3),(2449.8,1480.3)]]
    parent = {'id': adapter.PARENT_ID, 'parts': [[ring]], 'ground_z':63.52,'top_z':109.07,'height_m':45.55}
    return tmp_path, manifest, {'buildings': [parent]}, positions, indices


def load(bundle, manifest=None, snapshot=None):
    folder, original, parent, *_ = bundle
    (folder/'manifest.json').write_text(json.dumps(manifest if manifest is not None else original), encoding='utf-8')
    (folder/'parent.json').write_text(json.dumps(snapshot if snapshot is not None else parent), encoding='utf-8')
    return adapter.load_granite_overlay(folder/'manifest.json', folder/'parent.json')


def test_numeric_axes_model_relative_height_and_winding_preservation(bundle):
    folder, manifest, snapshot, positions, indices = bundle
    before = (folder/'asset.glb').read_bytes()
    result = load(bundle)
    assert result['parent']['id'] == adapter.PARENT_ID
    assert result['triangle_count'] == 1092 and result['panel_count'] == 91
    assert len(result['vertices_lonlat_height']) == 3276
    inverse = Transformer.from_crs(4326,32636,always_xy=True)
    for out, index in zip(result['vertices_lonlat_height'], indices):
        east, north = inverse.transform(*out[:2])
        p = struct.unpack('<fff', struct.pack('<fff', *positions[index]))
        assert east == pytest.approx(adapter.ORIGIN[0]+p[0], abs=1e-7)
        assert north == pytest.approx(adapter.ORIGIN[1]-p[2], abs=1e-7)
        assert out[2] == pytest.approx(adapter.ORIGIN[2]+p[1]-63.52, abs=1e-10)
    assert (folder/'asset.glb').read_bytes() == before
    assert 'position_local_m' not in json.dumps(result) and str(folder) not in json.dumps(result)


@pytest.mark.parametrize('fault', ['axis','origin','units','identity','duplicate','absolute','escape','hash','parent_id','parent_z','bool_coordinate','far_parent'])
def test_rejects_changed_frames_paths_identity_and_parent(bundle, fault):
    folder, m, s, *_ = copy.deepcopy(bundle)
    if fault == 'axis': m['coordinate_system']['gltf_axis'] = 'Z-up'
    if fault == 'origin': m['origin_utm_m'][0] += 10
    if fault == 'units': m['coordinate_system']['units'] = 'centimetres'
    if fault == 'identity': m['candidates'][0]['source_building_uuid'] = 'tower70'
    if fault == 'duplicate': m['candidates'] *= 2
    if fault == 'absolute': m['candidates'][0]['glb_asset']['path'] = str(folder/'asset.glb')
    if fault == 'escape': m['candidates'][0]['glb_asset']['path'] = '../asset.glb'
    if fault == 'hash': (folder/'asset.glb').write_bytes((folder/'asset.glb').read_bytes()+b' ')
    if fault == 'parent_id': s['buildings'][0]['id'] = 'beirut001:'+adapter.PARENT_ID
    if fault == 'parent_z': s['buildings'][0].update(ground_z=100,top_z=145.55)
    if fault == 'bool_coordinate': s['buildings'][0]['parts'][0][0][0][0] = True
    if fault == 'far_parent': s['buildings'][0]['parts'][0][0] = [[35,33],[35.001,33],[35.001,33.001]]
    with pytest.raises(ValueError): load(bundle, m, s)


@pytest.mark.parametrize('mutation', [
    lambda g:g['nodes'][0].update(matrix=[1]*16),
    lambda g:g['accessors'][0].update(byteOffset=4),
    lambda g:g['accessors'][0].update(count=True),
    lambda g:g['meshes'][0]['primitives'][0]['attributes'].update(POSITION=-1),
    lambda g:g['bufferViews'][0].update(byteOffset=-4),
    lambda g:g['buffers'][0].update(uri='external.bin'),
    lambda g:g['accessors'][1].update(componentType=5123),
])
def test_parser_rejects_unsupported_layouts(mutation):
    raw, *_ = make_glb(mutation)
    with pytest.raises(ValueError): adapter._parse_glb(raw)


def test_degenerate_panel_is_rejected():
    raw, *_ = make_glb(degenerate=True)
    positions, indices = adapter._parse_glb(raw)
    with pytest.raises(ValueError, match='Degenerate'): adapter._validate_components(positions, indices)
