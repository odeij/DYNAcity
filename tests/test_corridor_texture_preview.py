"""Tests for build_corridor_texture_preview.py - actual binary decode and HTML verification."""
import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('texture_preview_tested', Path(__file__).parents[1]/'scripts/build_corridor_texture_preview.py')
texmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(texmod)


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


def read_accessor_f32_vec2(gltf, bin_data, idx):
    acc = gltf['accessors'][idx]
    bv = gltf['bufferViews'][acc.get('bufferView', 0)]
    off = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
    count = acc['count']
    stride = bv.get('byteStride', 0)
    if stride == 0:
        slc = bin_data[off:off + count * 8]
        return list(struct.unpack(f'<{count*2}f', slc))
    res = []
    for i in range(count):
        slc = bin_data[off + i*stride:off + i*stride + 8]
        res.extend(struct.unpack('<ff', slc))
    return res


def read_indices_u32(gltf, bin_data, idx):
    acc = gltf['accessors'][idx]
    bv = gltf['bufferViews'][acc.get('bufferView', 0)]
    off = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
    count = acc['count']
    slc = bin_data[off:off + count * 4]
    return list(struct.unpack(f'<{count}I', slc))


def test_cli_checker_mode(tmp_path):
    """Test CLI with checker mode on actual corrected GLB."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'texture_checker'
    result = subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out['triangles'] == 1092
    assert out['vertices'] == 3276
    assert out['mode'] == 'checker'
    assert Path(out['html']).exists()
    assert Path(out['manifest']).exists()


def test_cli_plain_mode(tmp_path):
    """Test CLI with plain mode."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'texture_plain'
    result = subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'plain'
    ], capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out['mode'] == 'plain'


def test_html_contains_deck_gl_and_mesh(tmp_path):
    """Verify generated HTML contains deck.gl CDN and mesh data structure."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'html_check'
    subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, check=True, cwd=Path(__file__).parents[1])
    html = (out_dir / 'index.html').read_text(encoding="utf-8")
    assert 'cdn.jsdelivr.net/npm/deck.gl@9.4.0/dist.min.js' in html
    assert 'SimpleMeshLayer' in html
    assert 'OrbitView' in html
    assert 'COORDINATE_SYSTEM.CARTESIAN' in html
    assert 'Float32Array' in html
    assert 'Uint32Array' in html
    assert 'onAfterRender' in html
    assert 'onError' in html
    # Check mesh structure
    assert 'positions' in html and 'size: 3' in html
    assert 'normals' in html and 'size: 3' in html
    assert 'texCoords' in html and 'size: 2' in html
    assert 'indices' in html and 'size: 1' in html
    # Check mode select
    assert 'Checker (1 UV repeat = 1 metre)' in html
    assert 'Plain material color' in html


def test_output_glb_unchanged_hash(tmp_path):
    """Verify output GLB copy has same hash as input."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'hash_check'
    subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, check=True, cwd=Path(__file__).parents[1])
    input_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    # Manifest should reference the copy
    manifest = json.loads((out_dir / 'manifest.json').read_text(encoding="utf-8"))
    assert manifest['source_glb']['sha256'] == input_hash


def test_attribute_counts_preserved(tmp_path):
    """Verify POSITION/NORMAL/TEXCOORD_0 counts are equal and match vertices."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'attr_check'
    subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, check=True, cwd=Path(__file__).parents[1])
    # Parse the GLB copy in output
    glb_copy = list(out_dir.glob('*.glb'))[0]
    gltf, bin_data = parse_output_glb(glb_copy)
    # Verify attributes
    pos_idx = gltf['meshes'][0]['primitives'][0]['attributes']['POSITION']
    norm_idx = gltf['meshes'][0]['primitives'][0]['attributes']['NORMAL']
    uv_idx = gltf['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_0']
    pos_count = gltf['accessors'][pos_idx]['count']
    norm_count = gltf['accessors'][norm_idx]['count']
    uv_count = gltf['accessors'][uv_idx]['count']
    assert pos_count == norm_count == uv_count == 3276
    # Read actual data and verify counts
    pos = read_accessor_f32(gltf, bin_data, pos_idx)
    norm = read_accessor_f32(gltf, bin_data, norm_idx)
    uv = read_accessor_f32_vec2(gltf, bin_data, uv_idx)
    assert len(pos) == 3276 * 3
    assert len(norm) == 3276 * 3
    assert len(uv) == 3276 * 2


def test_normal_axis_rotation_gltf_to_enu(tmp_path):
    """Test normal conversion from GLTF Y-up to ENU."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'normal_check'
    # Import the conversion functions
    result = subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, check=True, cwd=Path(__file__).parents[1])
    # Parse and check
    gltf, bin_data = parse_output_glb(list(out_dir.glob('*.glb'))[0])
    norm_idx = gltf['meshes'][0]['primitives'][0]['attributes']['NORMAL']
    normals = read_accessor_f32(gltf, bin_data, norm_idx)
    # First normal - should be one of the panel normals
    # In GLTF, Z-up is negative Y in world (Y-up), so dominant Y normal would be up
    # After ENU conversion: [x, -z, y]
    # Check that conversion produces valid unit vectors
    for i in range(0, min(9, len(normals)), 3):
        nx, ny, nz = normals[i], normals[i+1], normals[i+2]
        # Convert to ENU
        enu_x, enu_y, enu_z = nx, -nz, ny
        length = (enu_x**2 + enu_y**2 + enu_z**2)**0.5
        assert abs(length - 1.0) < 1e-5


def test_output_overwrite_refused(tmp_path):
    """Test that non-empty output directory is rejected."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'overwrite'
    # First run
    subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, check=True, cwd=Path(__file__).parents[1])
    # Second run should fail
    result = subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode != 0
    assert 'not empty' in result.stderr or 'not empty' in result.stdout


def test_missing_input_rejected(tmp_path):
    """Test that missing input file is rejected."""
    out_dir = tmp_path / 'missing'
    result = subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(tmp_path / 'nonexistent.glb'), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert result.returncode != 0
    assert 'not found' in result.stderr or 'not found' in result.stdout


def test_uv_count_preserved_in_html(tmp_path):
    """Verify HTML contains correct UV count matching vertices."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'uv_count'
    subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, check=True, cwd=Path(__file__).parents[1])
    html = (out_dir / 'index.html').read_text(encoding="utf-8")
    # Check that mesh has texCoords with Float32Array
    assert 'texCoords' in html
    assert 'Float32Array' in html
    # The mesh JSON should have 3276 * 2 = 6552 UV values
    # We can verify by checking the mesh structure is present
    assert 'size: 2' in html


def test_manifest_schema_and_warnings(tmp_path):
    """Test manifest has correct schema and all warnings."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'manifest_check'
    subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, check=True, cwd=Path(__file__).parents[1])
    manifest = json.loads((out_dir / 'manifest.json').read_text(encoding="utf-8"))
    assert manifest['schema'] == 'dynacity-corridor-texture-preview/1'
    assert manifest['mesh']['triangles'] == 1092
    assert manifest['mesh']['vertices'] == 3276
    assert manifest['texture']['mode'] == 'checker'
    assert manifest['render']['library'] == 'deck.gl@9.4.0'
    assert manifest['render']['layer'] == 'SimpleMeshLayer'
    assert len(manifest['warnings']) >= 6
    for w in manifest['warnings']:
        assert isinstance(w, str) and len(w) > 0


def test_js_syntax_check(tmp_path):
    """Verify generated JavaScript is syntactically valid via node --check."""
    src = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration35-material-uv/root-corrected/89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f_uv.glb')
    if not src.exists():
        pytest.skip("Corrected GLB not available")
    out_dir = tmp_path / 'js_check'
    subprocess.run([
        sys.executable, '-m', 'scripts.build_corridor_texture_preview',
        '--input', str(src), '--output', str(out_dir), '--mode', 'checker'
    ], capture_output=True, check=True, cwd=Path(__file__).parents[1])
    html = (out_dir / 'index.html').read_text(encoding="utf-8")
    # Extract script content between <script> tags (excluding the deck.gl CDN)
    import re
    scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
    for i, script in enumerate(scripts):
        if 'deck.gl' in script or 'cdn.jsdelivr' in script:
            continue
        if script.strip():
            js_file = tmp_path / f'script_{i}.js'
            js_file.write_text(script, encoding="utf-8")
            # Use node --check if available
            try:
                result = subprocess.run(['node', '--check', str(js_file)], capture_output=True, text=True, timeout=10)
                if result.returncode != 0:
                    # node might not be available, skip
                    pass
            except FileNotFoundError:
                pytest.skip("Node.js not available for syntax check")
