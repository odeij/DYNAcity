"""Tests for prepare_corridor_material_uv.py - actual binary decode verification."""
import hashlib
import importlib.util
import json
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple
import pytest

spec = importlib.util.spec_from_file_location('material_uv_tested', Path(__file__).parents[1]/'scripts/prepare_corridor_material_uv.py')
uvmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uvmod)


def glb_input(path: Path, positions: List[Tuple[float,float,float]], indices: List[int]):
    """Create minimal valid GLB with POSITION + indices, single primitive, identity node."""
    pos_bytes = b''.join(struct.pack('<fff', *v) for v in positions)
    idx_bytes = b''.join(struct.pack('<I', i) for i in indices)
    doc = {
        'asset': {'version': '2.0'},
        'scene': 0,
        'scenes': [{'nodes': [0]}],
        'nodes': [{'mesh': 0}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'material': 0}]}],
        'materials': [{'pbrMetallicRoughness': {'baseColorFactor': [0.2, 0.3, 0.4, 1.0]}, 'doubleSided': True}],
        'buffers': [{'byteLength': len(pos_bytes) + len(idx_bytes)}],
        'bufferViews': [
            {'buffer': 0, 'byteOffset': 0, 'byteLength': len(pos_bytes), 'target': 34962},
            {'buffer': 0, 'byteOffset': len(pos_bytes), 'byteLength': len(idx_bytes), 'target': 34963},
        ],
        'accessors': [
            {'bufferView': 0, 'componentType': 5126, 'count': len(positions), 'type': 'VEC3'},
            {'bufferView': 1, 'componentType': 5125, 'count': len(indices), 'type': 'SCALAR'},
        ],
    }
    js = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    js += b' ' * ((-len(js)) % 4)
    raw = struct.pack('<III', 0x46546c67, 2, 12 + 8 + len(js) + 8 + len(pos_bytes) + len(idx_bytes))
    raw += struct.pack('<II', len(js), 0x4E4F534A) + js
    raw += struct.pack('<II', len(pos_bytes) + len(idx_bytes), 0x004E4942) + pos_bytes + idx_bytes
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def parse_output_glb(path: Path):
    data = path.read_bytes()
    magic, version, length = struct.unpack('<III', data[:12])
    offset = 12
    json_data = None
    bin_data = None
    while offset < len(data):
        chunk_len, chunk_type = struct.unpack('<II', data[offset:offset+8])
        offset += 8
        chunk = data[offset:offset+chunk_len]
        offset += chunk_len
        if chunk_len % 4 != 0:
            offset += 4 - (chunk_len % 4)
        if chunk_type == 0x4E4F534A:
            json_data = json.loads(chunk.decode('utf-8'))
        elif chunk_type == 0x004E4942:
            bin_data = chunk
    return json_data, bin_data


def read_accessor_f32(gltf, bin_data, idx):
    acc = gltf['accessors'][idx]
    bv = gltf['bufferViews'][acc.get('bufferView', 0)]
    off = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
    count = acc['count']
    stride = bv.get('byteStride', 0)
    if stride == 0:
        slc = bin_data[off:off + count * 12]
        return list(struct.unpack(f'<{count*3}f', slc))
    res = []
    for i in range(count):
        slc = bin_data[off + i*stride:off + i*stride + 12]
        res.extend(struct.unpack('<fff', slc))
    return res


def read_indices_u32(gltf, bin_data, idx):
    acc = gltf['accessors'][idx]
    bv = gltf['bufferViews'][acc.get('bufferView', 0)]
    off = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
    count = acc['count']
    slc = bin_data[off:off + count * 4]
    return list(struct.unpack(f'<{count}I', slc))


@pytest.fixture
def input_glb(tmp_path):
    # 2 triangles, 4 vertices, 6 indices
    positions = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
    ]
    # Two triangles sharing edge: (0,1,2) and (1,3,2) - CCW winding, normal +Z
    indices = [0, 1, 2, 1, 3, 2]
    path = tmp_path / 'input.glb'
    glb_input(path, positions, indices)
    return path, positions, indices


def test_cli_basic_run(input_glb, tmp_path):
    path, _, _ = input_glb
    out_dir = tmp_path / 'out'
    result = subprocess.run([
        sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '1.0'
    ], capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out['triangles'] == 2
    assert out['output_vertices'] == 6  # 2 tris * 3 vertices
    assert Path(out['report']).exists()
    assert Path(out_dir / 'input_uv.glb').exists()


def test_output_triangle_coords_exact_match(input_glb, tmp_path):
    path, orig_pos, orig_idx = input_glb
    out_dir = tmp_path / 'out2'
    subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '1.0'],
        capture_output=True, check=True, cwd=Path(__file__).parents[1])
    gltf, bin_data = parse_output_glb(out_dir / 'input_uv.glb')
    pos_flat = read_accessor_f32(gltf, bin_data, 0)  # POSITION
    idx = read_indices_u32(gltf, bin_data, 3)
    # Each triangle has 3 unique vertices in output (split)
    assert len(idx) == 6
    assert idx == [0, 1, 2, 3, 4, 5]
    # Positions should match original triangle vertices exactly
    # Tri 0: vertices 0,1,2 -> orig indices 0,1,2
    tri0 = [(pos_flat[i*3], pos_flat[i*3+1], pos_flat[i*3+2]) for i in range(3)]
    assert tri0[0] == orig_pos[0]
    assert tri0[1] == orig_pos[1]
    assert tri0[2] == orig_pos[2]
    # Tri 1: vertices 3,4,5 -> orig indices 1,3,2
    tri1 = [(pos_flat[i*3], pos_flat[i*3+1], pos_flat[i*3+2]) for i in range(3, 6)]
    assert tri1[0] == orig_pos[1]
    assert tri1[1] == orig_pos[3]
    assert tri1[2] == orig_pos[2]


def test_normals_outward_and_unit(input_glb, tmp_path):
    path, _, _ = input_glb
    out_dir = tmp_path / 'out3'
    subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '1.0'],
        capture_output=True, check=True, cwd=Path(__file__).parents[1])
    gltf, bin_data = parse_output_glb(out_dir / 'input_uv.glb')
    norm_flat = read_accessor_f32(gltf, bin_data, 1)  # NORMAL
    # Each triangle has 3 identical normals (per-face)
    for t in range(2):
        n0 = norm_flat[t*9:t*9+3]
        n1 = norm_flat[t*9+3:t*9+6]
        n2 = norm_flat[t*9+6:t*9+9]
        assert n0 == n1 == n2
        # Unit length
        l = math.sqrt(n0[0]**2 + n0[1]**2 + n0[2]**2)
        assert abs(l - 1.0) < 1e-5
        # For XY plane CCW, normal should be +Z
        assert abs(n0[2] - 1.0) < 1e-5


def test_uv_dominant_axis_and_scaling(input_glb, tmp_path):
    path, _, _ = input_glb
    out_dir = tmp_path / 'out4'
    subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '0.5'],
        capture_output=True, check=True, cwd=Path(__file__).parents[1])
    gltf, bin_data = parse_output_glb(out_dir / 'input_uv.glb')
    uv_flat = read_accessor_f32(gltf, bin_data, 2)  # TEXCOORD_0 (VEC2)
    # Normal is +Z, so dominant axis Z -> project onto XY
    # UV = (x/tile, y/tile)
    # Vertex 0: (0,0,0) -> (0, 0)
    u0, v0 = uv_flat[0], uv_flat[1]
    assert abs(u0 - 0.0) < 1e-5 and abs(v0 - 0.0) < 1e-5
    # Vertex 1: (1,0,0) -> (2, 0) at tile=0.5
    u1, v1 = uv_flat[2], uv_flat[3]
    assert abs(u1 - 2.0) < 1e-5 and abs(v1 - 0.0) < 1e-5
    # Vertex 2: (0,1,0) -> (0, 2)
    u2, v2 = uv_flat[4], uv_flat[5]
    assert abs(u2 - 0.0) < 1e-5 and abs(v2 - 2.0) < 1e-5


def test_uv_negative_positions_and_axes(tmp_path):
    # Triangle on YZ plane (normal +X), vertices with negative coords
    positions = [
        (-2.0, 0.0, 0.0),
        (-2.0, 1.0, 0.0),
        (-2.0, 0.0, 1.0),
    ]
    indices = [0, 1, 2]
    path = tmp_path / 'neg.glb'
    glb_input(path, positions, indices)
    out_dir = tmp_path / 'out5'
    subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '1.0'],
        capture_output=True, check=True, cwd=Path(__file__).parents[1])
    gltf, bin_data = parse_output_glb(out_dir / 'neg_uv.glb')
    norm_flat = read_accessor_f32(gltf, bin_data, 1)
    # Normal should be +X
    nx, ny, nz = norm_flat[0], norm_flat[1], norm_flat[2]
    assert abs(nx - 1.0) < 1e-5 and abs(ny) < 1e-5 and abs(nz) < 1e-5
    # UV: dominant X -> project onto YZ
    uv_flat = read_accessor_f32(gltf, bin_data, 2)
    u0, v0 = uv_flat[0], uv_flat[1]  # y=0, z=0 -> (0,0)
    assert abs(u0 - 0.0) < 1e-5 and abs(v0 - 0.0) < 1e-5
    u1, v1 = uv_flat[2], uv_flat[3]  # y=1, z=0 -> (1,0)
    assert abs(u1 - 1.0) < 1e-5 and abs(v1 - 0.0) < 1e-5


def test_metadata_preserved(input_glb, tmp_path):
    path, _, _ = input_glb
    out_dir = tmp_path / 'out6'
    subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '1.0'],
        capture_output=True, check=True, cwd=Path(__file__).parents[1])
    gltf, _ = parse_output_glb(out_dir / 'input_uv.glb')
    # Materials preserved
    assert len(gltf['materials']) == 1
    assert gltf['materials'][0]['pbrMetallicRoughness']['baseColorFactor'] == [0.2, 0.3, 0.4, 1.0]
    # Extras present
    assert 'dynacity_material_uv' in gltf['extras']
    assert gltf['extras']['dynacity_material_uv']['tile_metres'] == 1.0
    assert gltf['extras']['dynacity_material_uv']['uv_strategy'] == 'dominant_axis_planar_per_face'
    # Source UUID in extras
    assert gltf['extras']['dynacity_material_uv']['source_building_uuid'] == 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1'


def test_invalid_tile_metres_rejected(tmp_path):
    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    indices = [0, 1, 2]
    path = tmp_path / 'in.glb'
    glb_input(path, positions, indices)
    out_dir = tmp_path / 'out'
    result = subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '0'],
        capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode != 0
    assert 'must be positive' in result.stderr or 'must be positive' in result.stdout


def test_input_with_normal_already_present_rejected(tmp_path):
    # Create GLB that already has NORMAL attribute
    positions = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    indices = [0, 1, 2]
    pos_bytes = b''.join(struct.pack('<fff', *v) for v in positions)
    idx_bytes = b''.join(struct.pack('<I', i) for i in indices)
    norm_bytes = b''.join(struct.pack('<fff', 0, 0, 1) for _ in range(3))
    doc = {
        'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
        'nodes': [{'mesh': 0}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 2}, 'indices': 1, 'material': 0}]}],
        'materials': [{'pbrMetallicRoughness': {'baseColorFactor': [0.2, 0.3, 0.4, 1.0]}}],
        'buffers': [{'byteLength': len(pos_bytes) + len(idx_bytes) + len(norm_bytes)}],
        'bufferViews': [
            {'buffer': 0, 'byteOffset': 0, 'byteLength': len(pos_bytes), 'target': 34962},
            {'buffer': 0, 'byteOffset': len(pos_bytes), 'byteLength': len(idx_bytes), 'target': 34963},
            {'buffer': 0, 'byteOffset': len(pos_bytes) + len(idx_bytes), 'byteLength': len(norm_bytes), 'target': 34962},
        ],
        'accessors': [
            {'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3'},
            {'bufferView': 1, 'componentType': 5125, 'count': 3, 'type': 'SCALAR'},
            {'bufferView': 2, 'componentType': 5126, 'count': 3, 'type': 'VEC3'},
        ],
    }
    js = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    js += b' ' * ((-len(js)) % 4)
    raw = struct.pack('<III', 0x46546c67, 2, 12 + 8 + len(js) + 8 + len(pos_bytes) + len(idx_bytes) + len(norm_bytes))
    raw += struct.pack('<II', len(js), 0x4E4F534A) + js
    raw += struct.pack('<II', len(pos_bytes) + len(idx_bytes) + len(norm_bytes), 0x004E4942) + pos_bytes + idx_bytes + norm_bytes
    path = tmp_path / 'has_normal.glb'
    path.write_bytes(raw)
    out_dir = tmp_path / 'out'
    result = subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '1.0'],
        capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode != 0
    assert 'already present' in result.stderr or 'already present' in result.stdout


def test_output_overwrite_refused(input_glb, tmp_path):
    path, _, _ = input_glb
    out_dir = tmp_path / 'out'
    subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '1.0'],
        capture_output=True, check=True, cwd=Path(__file__).parents[1])
    # Second run should fail
    result = subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(path), '--output', str(out_dir), '--tile-metres', '1.0'],
        capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode != 0
    assert 'not empty' in result.stderr or 'not empty' in result.stdout


def test_actual_granite91_input(tmp_path):
    """Integration test with real granite91 GLB from reference assets."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reference-assets/iteration30-shared-package/root-georeferenced/assets/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f.glb')
    if not src.exists():
        pytest.skip("Reference GLB not available")
    out_dir = tmp_path / 'granite91_uv'
    result = subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv', '--source-building-uuid', 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
        '--input', str(src), '--output', str(out_dir), '--tile-metres', '1.0'],
        capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out['triangles'] == 1092
    assert out['output_vertices'] == 1092 * 3  # per-face split
    report_path = Path(out['report'])
    report = json.loads(report_path.read_text())
    assert report['input']['triangle_count'] == 1092
    assert report['output']['triangle_count'] == 1092
    assert report['uv']['tile_metres'] == 1.0
    # Check dominant axes - granite panels are mostly vertical (Y or Z dominant)
    dom = report['uv']['dominant_axis_counts']
    assert sum(dom.values()) == 1092
    # Verify output GLB hash
    out_glb = Path(report['output']['glb_path'])
    assert out_glb.exists()
    assert hashlib.sha256(out_glb.read_bytes()).hexdigest() == report['output']['sha256']


def test_uv_accessor_cardinality_and_actual_values(input_glb, tmp_path):
    src, positions, indices = input_glb
    dest = tmp_path/'uv-cardinality'
    subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv',
        '--input', str(src), '--output', str(dest), '--tile-metres', '2',
        '--source-building-uuid', 'fixture'], check=True, capture_output=True)
    doc, binary = parse_output_glb(next(dest.glob('*.glb')))
    attrs = doc['meshes'][0]['primitives'][0]['attributes']
    assert {doc['accessors'][i]['count'] for i in attrs.values()} == {len(indices)}
    uv = doc['accessors'][attrs['TEXCOORD_0']]; view=doc['bufferViews'][uv['bufferView']]
    actual = struct.unpack_from('<'+'f'*(uv['count']*2), binary, view['byteOffset'])
    expected=tuple(value/2 for i in indices for value in positions[i][:2])
    assert actual == expected


@pytest.mark.parametrize('tile', ['nan', 'inf', '-inf', '0'])
def test_invalid_tile_leaves_no_output(input_glb, tmp_path, tile):
    src, _, _=input_glb;dest=tmp_path/'invalid-tile'
    result=subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv',
        '--input', str(src), '--output', str(dest), '--tile-metres='+tile,
        '--source-building-uuid', 'fixture'], capture_output=True)
    assert result.returncode != 0
    assert not dest.exists()


@pytest.mark.parametrize('kind', ['transform','multiple','sparse','stride','range','buffer','extra_attribute'])
def test_unsupported_document_leaves_no_output(input_glb, tmp_path, kind):
    src, _, _=input_glb;doc,binary=parse_output_glb(src)
    if kind=='transform':doc['nodes'][0]['translation']=[1,0,0]
    elif kind=='multiple':doc['meshes'].append(doc['meshes'][0])
    elif kind=='sparse':doc['accessors'][0]['sparse']={}
    elif kind=='stride':doc['bufferViews'][0]['byteStride']=16
    elif kind=='range':doc['bufferViews'][0]['byteLength']=4
    elif kind=='buffer':doc['bufferViews'][0]['buffer']=1
    elif kind=='extra_attribute':doc['meshes'][0]['primitives'][0]['attributes']['COLOR_0']=0
    uvmod.write_glb(doc,binary,src);dest=tmp_path/'bad-document'
    result=subprocess.run([sys.executable, '-m', 'scripts.prepare_corridor_material_uv',
        '--input',str(src),'--output',str(dest),'--source-building-uuid','fixture'],capture_output=True)
    assert result.returncode != 0
    assert not dest.exists()
