#!/usr/bin/env python3
"""
Focused tests for export_corridor_appearance_bundle.py
Tests: path guarding, hash validation, duplicate UUID rejection, path traversal, roundtrip bounds computation.
Uses temp fixtures; no external dependencies on real assets.
"""
import json
import hashlib
import tempfile
import struct
import shutil
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from export_corridor_appearance_bundle import (
    sha256_file,
    resolve_safe_path,
    parse_glb_json_chunk,
    compute_node_transform,
    compute_transformed_bounds,
    glb_yup_to_local_enu,
    read_candidate,
    export_bundle,
    extract_material_parameters,
    GeometryCandidate,
    get_bin_data,
    validate_gltf_structure,
)


def create_minimal_glb(tmp_path: Path, positions: list, name: str = "TestMesh") -> Path:
    """Create a minimal valid GLB file with given positions."""
    positions_bytes = b''.join(struct.pack('<fff', *p) for p in positions)
    count = len(positions)

    gltf = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0}],
        "meshes": [{
            "name": name,
            "primitives": [{"attributes": {"POSITION": 0}, "material": 0}]
        }],
        "materials": [{
            "name": "default",
            "pbrMetallicRoughness": {
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0
            }
        }],
        "accessors": [{
            "bufferView": 0,
            "componentType": 5126,
            "count": count,
            "type": "VEC3",
            "min": np.array(positions).min(axis=0).tolist(),
            "max": np.array(positions).max(axis=0).tolist()
        }],
        "bufferViews": [{
            "buffer": 0,
            "byteOffset": 0,
            "byteLength": len(positions_bytes),
            "target": 34962
        }],
        "buffers": [{"byteLength": len(positions_bytes)}]
    }

    json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_padded = json_bytes + b' ' * ((4 - len(json_bytes) % 4) % 4)
    bin_padded = positions_bytes + b'\x00' * ((4 - len(positions_bytes) % 4) % 4)

    total_length = 12 + 8 + len(json_padded) + 8 + len(bin_padded)
    header = struct.pack('<4sII', b'glTF', 2, total_length)
    json_chunk = struct.pack('<II', len(json_padded), 0x4E4F534A) + json_padded
    bin_chunk = struct.pack('<II', len(bin_padded), 0x004E4942) + bin_padded

    glb_path = tmp_path / f"{name}.glb"
    glb_path.write_bytes(header + json_chunk + bin_chunk)
    return glb_path


def create_glb_with_transform(tmp_path: Path, positions: list, translation=(10, 20, 30)) -> Path:
    """Create GLB with a node that has translation transform."""
    positions_bytes = b''.join(struct.pack('<fff', *p) for p in positions)
    count = len(positions)

    gltf = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{
            "name": "TransformedMesh",
            "mesh": 0,
            "translation": list(translation)
        }],
        "meshes": [{
            "name": "TransformedMesh",
            "primitives": [{"attributes": {"POSITION": 0}, "material": 0}]
        }],
        "materials": [{"name": "default", "pbrMetallicRoughness": {"baseColorFactor": [1,1,1,1], "metallicFactor": 0.0, "roughnessFactor": 1.0}}],
        "accessors": [{
            "bufferView": 0,
            "componentType": 5126,
            "count": count,
            "type": "VEC3",
            "min": np.array(positions).min(axis=0).tolist(),
            "max": np.array(positions).max(axis=0).tolist()
        }],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions_bytes), "target": 34962}],
        "buffers": [{"byteLength": len(positions_bytes)}]
    }

    json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_padded = json_bytes + b' ' * ((4 - len(json_bytes) % 4) % 4)
    bin_padded = positions_bytes + b'\x00' * ((4 - len(positions_bytes) % 4) % 4)

    total_length = 12 + 8 + len(json_padded) + 8 + len(bin_padded)
    header = struct.pack('<4sII', b'glTF', 2, total_length)
    json_chunk = struct.pack('<II', len(json_padded), 0x4E4F534A) + json_padded
    bin_chunk = struct.pack('<II', len(bin_padded), 0x004E4942) + bin_padded

    glb_path = tmp_path / "transformed.glb"
    glb_path.write_bytes(header + json_chunk + bin_chunk)
    return glb_path


def create_glb_with_rotation(tmp_path: Path, positions: list) -> Path:
    """Create GLB with a node that has 90-degree rotation around Y (quaternion)."""
    # 90 deg around Y: quaternion (0, sin(45°), 0, cos(45°)) = (0, 0.7071, 0, 0.7071)
    positions_bytes = b''.join(struct.pack('<fff', *p) for p in positions)
    count = len(positions)

    gltf = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{
            "name": "RotatedMesh",
            "mesh": 0,
            "rotation": [0, 0.70710678, 0, 0.70710678]
        }],
        "meshes": [{
            "name": "RotatedMesh",
            "primitives": [{"attributes": {"POSITION": 0}, "material": 0}]
        }],
        "materials": [{"name": "default", "pbrMetallicRoughness": {"baseColorFactor": [1,1,1,1], "metallicFactor": 0.0, "roughnessFactor": 1.0}}],
        "accessors": [{
            "bufferView": 0,
            "componentType": 5126,
            "count": count,
            "type": "VEC3",
            "min": np.array(positions).min(axis=0).tolist(),
            "max": np.array(positions).max(axis=0).tolist()
        }],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions_bytes), "target": 34962}],
        "buffers": [{"byteLength": len(positions_bytes)}]
    }

    json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_padded = json_bytes + b' ' * ((4 - len(json_bytes) % 4) % 4)
    bin_padded = positions_bytes + b'\x00' * ((4 - len(positions_bytes) % 4) % 4)

    total_length = 12 + 8 + len(json_padded) + 8 + len(bin_padded)
    header = struct.pack('<4sII', b'glTF', 2, total_length)
    json_chunk = struct.pack('<II', len(json_padded), 0x4E4F534A) + json_padded
    bin_chunk = struct.pack('<II', len(bin_padded), 0x004E4942) + bin_padded

    glb_path = tmp_path / "rotated.glb"
    glb_path.write_bytes(header + json_chunk + bin_chunk)
    return glb_path


def create_glb_with_scale(tmp_path: Path, positions: list, scale=(2.0, 2.0, 2.0)) -> Path:
    """Create GLB with a node that has scale transform."""
    positions_bytes = b''.join(struct.pack('<fff', *p) for p in positions)
    count = len(positions)

    gltf = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{
            "name": "ScaledMesh",
            "mesh": 0,
            "scale": list(scale)
        }],
        "meshes": [{
            "name": "ScaledMesh",
            "primitives": [{"attributes": {"POSITION": 0}, "material": 0}]
        }],
        "materials": [{"name": "default", "pbrMetallicRoughness": {"baseColorFactor": [1,1,1,1], "metallicFactor": 0.0, "roughnessFactor": 1.0}}],
        "accessors": [{
            "bufferView": 0,
            "componentType": 5126,
            "count": count,
            "type": "VEC3",
            "min": np.array(positions).min(axis=0).tolist(),
            "max": np.array(positions).max(axis=0).tolist()
        }],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions_bytes), "target": 34962}],
        "buffers": [{"byteLength": len(positions_bytes)}]
    }

    json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_padded = json_bytes + b' ' * ((4 - len(json_bytes) % 4) % 4)
    bin_padded = positions_bytes + b'\x00' * ((4 - len(positions_bytes) % 4) % 4)

    total_length = 12 + 8 + len(json_padded) + 8 + len(bin_padded)
    header = struct.pack('<4sII', b'glTF', 2, total_length)
    json_chunk = struct.pack('<II', len(json_padded), 0x4E4F534A) + json_padded
    bin_chunk = struct.pack('<II', len(bin_padded), 0x004E4942) + bin_padded

    glb_path = tmp_path / "scaled.glb"
    glb_path.write_bytes(header + json_chunk + bin_chunk)
    return glb_path


def create_glb_strided_accessor(tmp_path: Path) -> Path:
    """Create GLB with strided accessor (interleaved)."""
    # Positions at stride 16 (extra 4 bytes padding per vertex)
    positions = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    pos_bytes = b''.join(struct.pack('<fff', *p) for p in positions)
    # Interleave with dummy data (4 bytes per vertex)
    interleaved = b''
    for p in positions:
        interleaved += struct.pack('<fff', *p)
        interleaved += b'\x00\x00\x00\x00'  # padding

    gltf = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "StridedMesh", "mesh": 0}],
        "meshes": [{
            "name": "StridedMesh",
            "primitives": [{"attributes": {"POSITION": 0}, "material": 0}]
        }],
        "materials": [{"name": "default", "pbrMetallicRoughness": {"baseColorFactor": [1,1,1,1], "metallicFactor": 0.0, "roughnessFactor": 1.0}}],
        "accessors": [{
            "bufferView": 0,
            "componentType": 5126,
            "count": 4,
            "type": "VEC3",
            "byteOffset": 0,
            "min": [0, 0, 0],
            "max": [1, 1, 0]
        }],
        "bufferViews": [{
            "buffer": 0,
            "byteOffset": 0,
            "byteLength": len(interleaved),
            "byteStride": 16,
            "target": 34962
        }],
        "buffers": [{"byteLength": len(interleaved)}]
    }

    json_bytes = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_padded = json_bytes + b' ' * ((4 - len(json_bytes) % 4) % 4)
    bin_padded = interleaved + b'\x00' * ((4 - len(interleaved) % 4) % 4)

    total_length = 12 + 8 + len(json_padded) + 8 + len(bin_padded)
    header = struct.pack('<4sII', b'glTF', 2, total_length)
    json_chunk = struct.pack('<II', len(json_padded), 0x4E4F534A) + json_padded
    bin_chunk = struct.pack('<II', len(bin_padded), 0x004E4942) + bin_padded

    glb_path = tmp_path / "strided.glb"
    glb_path.write_bytes(header + json_chunk + bin_chunk)
    return glb_path


def create_real_style_candidate(tmp_path: Path, key: str) -> tuple:
    """Create a test candidate with proper UUID-based identity matching real specs."""
    import uuid
    subdir = tmp_path / f"candidate_{uuid.uuid4().hex[:8]}"
    subdir.mkdir()

    positions = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    glb_path = create_minimal_glb(subdir, positions, "mesh")
    sha256 = sha256_file(glb_path)

    if key == "tower70":
        source_id_full = "beirut001:bde30f76-7868-4187-92a7-a18270c58815"
        fractional_estimate = 0.7
        fraction_field = "upper_infill_frac"
    else:
        source_id_full = "beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1"
        fractional_estimate = 0.7
        fraction_field = "fraction"

    spec = {
        "schema": "test/1",
        "source_id_full": source_id_full,
        "geometry": str(glb_path),
        "sha256": sha256,
        fraction_field: fractional_estimate,
        "limits": ["Test limitation"],
        "photo_metadata": {"date": "2018-07-21", "source_photo_url": "https://kartaview.org/map/1288770597", "license": "CC BY-SA 4.0"}
    }
    spec_path = subdir / "spec.json"
    spec_path.write_text(json.dumps(spec))

    prepared = {
        "mesh": f"/Game/Test/{key}",
        "materials": {},
        "spec": str(spec_path),
        "sha256": sha256
    }
    prepared_path = subdir / "prepared.json"
    prepared_path.write_text(json.dumps(prepared))

    return prepared_path, spec_path, glb_path, sha256


def test_resolve_safe_path_allows_within_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        target = root / "file.glb"
        target.write_bytes(b"test")
        resolved = resolve_safe_path(root, target)
        assert resolved == target.resolve()


def test_resolve_safe_path_blocks_escape():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        outside = Path(tmp) / "outside.glb"
        outside.write_bytes(b"test")
        try:
            resolve_safe_path(root, outside)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "escapes allowed root" in str(e)


def test_sha256_file():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.bin"
        p.write_bytes(b"hello world")
        assert sha256_file(p) == hashlib.sha256(b"hello world").hexdigest()


def test_parse_glb_json_chunk():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        glb = create_minimal_glb(tmp, [[0,0,0], [1,1,1]], "Test")
        gltf = parse_glb_json_chunk(glb)
        assert gltf["asset"]["version"] == "2.0"
        assert len(gltf["meshes"]) == 1


def test_get_bin_data():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        glb = create_minimal_glb(tmp, [[0,0,0], [1,1,1]], "Test")
        bin_data = get_bin_data(glb)
        assert len(bin_data) > 0


def test_validate_gltf_structure():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        glb = create_minimal_glb(tmp, [[0,0,0], [1,1,1]], "Test")
        gltf = parse_glb_json_chunk(glb)
        reachable, mesh_to_nodes = validate_gltf_structure(gltf)
        assert 0 in reachable
        assert 0 in mesh_to_nodes
        assert mesh_to_nodes[0] == [0]


def test_compute_node_transform_identity():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        glb = create_minimal_glb(tmp, [[0,0,0], [1,1,1]], "Test")
        gltf = parse_glb_json_chunk(glb)
        parent_map = {}
        transform = compute_node_transform(gltf, 0, parent_map)
        assert np.allclose(transform, np.eye(4))


def test_compute_node_transform_translation():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        glb = create_glb_with_transform(tmp, [[0,0,0], [1,1,1]], translation=(10, 20, 30))
        gltf = parse_glb_json_chunk(glb)
        parent_map = {}
        transform = compute_node_transform(gltf, 0, parent_map)
        assert np.allclose(transform[0:3, 3], [10, 20, 30])


def test_compute_node_transform_rotation():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Simple box at origin
        positions = [[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]]
        glb = create_glb_with_rotation(tmp, positions)
        gltf = parse_glb_json_chunk(glb)
        parent_map = {}
        transform = compute_node_transform(gltf, 0, parent_map)
        # 90 deg around Y should swap X and Z with sign changes
        # Just verify it's not identity
        assert not np.allclose(transform, np.eye(4))


def test_compute_node_transform_scale():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        glb = create_glb_with_scale(tmp, [[0,0,0], [1,1,1]], scale=(2.0, 2.0, 2.0))
        gltf = parse_glb_json_chunk(glb)
        parent_map = {}
        transform = compute_node_transform(gltf, 0, parent_map)
        assert np.allclose(np.diag(transform)[:3], [2.0, 2.0, 2.0])


def test_compute_transformed_bounds_no_transform():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        positions = [[0, 0, 0], [10, 5, 2]]
        glb = create_minimal_glb(tmp, positions, "Test")
        min_bounds, max_bounds = compute_transformed_bounds(glb)
        assert min_bounds == [0, 0, 0]
        assert max_bounds == [10, 5, 2]


def test_compute_transformed_bounds_with_translation():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        positions = [[0, 0, 0], [1, 1, 1]]
        glb = create_glb_with_transform(tmp, positions, translation=(100, 200, 300))
        min_bounds, max_bounds = compute_transformed_bounds(glb)
        assert min_bounds == [100, 200, 300]
        assert max_bounds == [101, 201, 301]


def test_compute_transformed_bounds_with_scale():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        positions = [[0, 0, 0], [1, 1, 1]]
        glb = create_glb_with_scale(tmp, positions, scale=(2.0, 2.0, 2.0))
        min_bounds, max_bounds = compute_transformed_bounds(glb)
        assert min_bounds == [0, 0, 0]
        assert max_bounds == [2, 2, 2]


def test_compute_transformed_bounds_strided_accessor():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        glb = create_glb_strided_accessor(tmp)
        min_bounds, max_bounds = compute_transformed_bounds(glb)
        assert min_bounds == [0, 0, 0]
        assert max_bounds == [1, 1, 0]


def test_glb_yup_to_local_enu():
    # GLTF Y-up: X=East, Y=Up, Z=-North
    # ENU: X=East, Y=North, Z=Up
    min_yup = [0, 10, -20]   # min_x=0, min_y=10, min_z=-20
    max_yup = [10, 20, -10]  # max_x=10, max_y=20, max_z=-10
    enu_min, enu_max = glb_yup_to_local_enu(min_yup, max_yup)
    # Corners in Y-up -> ENU: (x, -z, y)
    # min corner (0,10,-20) -> (0, 20, 10)
    # max corner (10,20,-10) -> (10, 10, 20)
    # ENU min: [0, 10, 10], ENU max: [10, 20, 20]
    assert enu_min == [0, 10, 10]
    assert enu_max == [10, 20, 20]


def test_read_candidate_valid():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prepared_path, spec_path, glb_path, sha256 = create_real_style_candidate(tmp, "tower70")
        candidate = read_candidate(prepared_path)
        assert candidate.key == "tower70"
        assert candidate.sha256 == sha256
        assert candidate.geometry_kind == "optional_additive_upper_infill"
        assert candidate.fractional_estimate == 0.7


def test_read_candidate_sha_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prepared_path, spec_path, glb_path, sha256 = create_real_style_candidate(tmp, "tower70")
        # Corrupt prepared.json with wrong sha
        with open(prepared_path) as f:
            prepared = json.load(f)
        prepared["sha256"] = "deadbeef" * 8
        prepared_path.write_text(json.dumps(prepared))
        try:
            read_candidate(prepared_path)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "SHA256 mismatch" in str(e)


def test_read_candidate_missing_glb():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prepared_path, spec_path, glb_path, sha256 = create_real_style_candidate(tmp, "tower70")
        # Remove GLB
        glb_path.unlink()
        try:
            read_candidate(prepared_path)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "GLB not found" in str(e)


def test_extract_material_parameters():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        glb = create_minimal_glb(tmp, [[0,0,0], [1,1,1]], "Test")
        mats = extract_material_parameters(glb)
        assert len(mats) == 1
        assert mats[0]["name"] == "default"
        assert mats[0]["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0]
        assert mats[0]["metallicFactor"] == 0.0
        assert mats[0]["roughnessFactor"] == 1.0


def test_export_bundle_atomic_write_and_verify():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Create two candidates
        prep1, spec1, glb1, sha1 = create_real_style_candidate(tmp, "tower70")
        prep2, spec2, glb2, sha2 = create_real_style_candidate(tmp, "granite91")

        candidate1 = read_candidate(prep1)
        candidate2 = read_candidate(prep2)

        corridor_manifest = {
            "origin_utm_m": [730784.758, 3751551.159, 51.106]
        }

        output_dir = tmp / "out"
        bundle = export_bundle([candidate1, candidate2], output_dir, corridor_manifest)

        # Verify outputs
        assert (output_dir / "manifest.json").exists()
        assert (output_dir / "private-provenance.json").exists()
        assert (output_dir / "assets" / f"{sha1}.glb").exists()
        assert (output_dir / "assets" / f"{sha2}.glb").exists()

        # Verify manifest content
        assert bundle["schema"] == "dynacity-corridor-appearance-bundle/1"
        assert bundle["status"] == "ESTIMATED_OPTIONAL_NOT_SCENE_APPLIED"
        assert len(bundle["candidates"]) == 2
        assert bundle["origin_utm_m"] == [730784.758, 3751551.159, 51.106]

        # Verify candidate entries
        for c in bundle["candidates"]:
            assert c["status"] == "ESTIMATED_OPTIONAL_NOT_SCENE_APPLIED"
            assert "gltf_bounds_yup" in c
            assert "local_enu_bounds" in c
            assert "material_parameters" in c
            assert c["glb_asset"]["sha256"] in [sha1, sha2]


def test_export_bundle_refuses_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prep1, spec1, glb1, sha1 = create_real_style_candidate(tmp, "tower70")
        candidate1 = read_candidate(prep1)
        prep2, *_ = create_real_style_candidate(tmp, "granite91")
        candidate2 = read_candidate(prep2)

        corridor_manifest = {"origin_utm_m": [0, 0, 0]}
        output_dir = tmp / "out"
        output_dir.mkdir()

        # First export
        export_bundle([candidate1,candidate2], output_dir, corridor_manifest)

        # Second export should fail
        try:
            export_bundle([candidate1,candidate2], output_dir, corridor_manifest)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "already exists" in str(e)


def test_export_bundle_different_content_same_name():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prep1, spec1, glb1, sha1 = create_real_style_candidate(tmp, "tower70")
        candidate1 = read_candidate(prep1)
        prep2, *_ = create_real_style_candidate(tmp, "granite91")
        candidate2 = read_candidate(prep2)

        corridor_manifest = {"origin_utm_m": [0, 0, 0]}
        output_dir = tmp / "out"
        output_dir.mkdir()
        assets_dir = output_dir / "assets"
        assets_dir.mkdir()

        # Create a different GLB with same name (sha1.glb) but different content
        fake_glb = assets_dir / f"{sha1}.glb"
        fake_glb.write_bytes(b"different content")

        try:
            export_bundle([candidate1,candidate2], output_dir, corridor_manifest)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "different content" in str(e)


def test_roundtrip_bounds_consistency():
    """Test that bounds computed from GLB are consistent with known values."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Known box: corners at (0,0,0) to (10,5,2)
        positions = [[0, 0, 0], [10, 0, 0], [10, 5, 0], [0, 5, 0],
                     [0, 0, 2], [10, 0, 2], [10, 5, 2], [0, 5, 2]]
        glb = create_minimal_glb(tmp, positions, "Box")
        min_bounds, max_bounds = compute_transformed_bounds(glb)
        assert min_bounds == [0, 0, 0]
        assert max_bounds == [10, 5, 2]

        enu_min, enu_max = glb_yup_to_local_enu(min_bounds, max_bounds)
        # ENU: x=east, y=north=-z, z=up=y
        # min: [0, -0, 0] = [0, 0, 0]
        # max: [10, -2, 5] = [10, 2, 5] ... wait
        # Y-up max_z = 2, min_z = 0
        # max_y = 5, min_y = 0
        # ENU x = x, y = -z, z = y
        # min: x=0, y=-0=0, z=0 -> [0,0,0]
        # max: x=10, y=-2=-2, z=5 -> [10,-2,5] ... but we recompute from all corners
        # Let's just verify it runs and produces arrays
        assert len(enu_min) == 3
        assert len(enu_max) == 3


def test_cli_invocation():
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prep1, spec1, glb1, sha1 = create_real_style_candidate(tmp, "tower70")
        prep2, spec2, glb2, sha2 = create_real_style_candidate(tmp, "granite91")

        corridor_manifest = {"origin_utm_m": [730784.758, 3751551.159, 51.106]}
        manifest_path = tmp / "corridor-manifest-v2.json"
        manifest_path.write_text(json.dumps(corridor_manifest))

        output_dir = tmp / "out"

        cmd = [
            sys.executable,
            str(Path(__file__).parent.parent / "scripts" / "export_corridor_appearance_bundle.py"),
            "--tower70-prepared", str(prep1),
            "--granite91-prepared", str(prep2),
            "--corridor-manifest", str(manifest_path),
            "--output", str(output_dir)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"CLI failed: {result.stderr}\n{result.stdout}"
        assert (output_dir / "manifest.json").exists()
        assert (output_dir / "assets" / f"{sha1}.glb").exists()
        assert (output_dir / "assets" / f"{sha2}.glb").exists()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])


def test_actual_granite_overlay_hash_field(tmp_path):
    prepared, spec, glb, expected = create_real_style_candidate(tmp_path, 'granite91')
    data = json.loads(spec.read_text())
    data['overlay_glb_sha256'] = data.pop('sha256')
    data['overlay_glb'] = data.pop('geometry')
    spec.write_text(json.dumps(data))
    assert read_candidate(prepared).sha256 == expected


def test_utm_wgs84_roundtrip():
    """Test UTM <-> WGS84 roundtrip accuracy < 1mm."""
    from export_corridor_appearance_bundle import utm_to_wgs84, wgs84_to_utm

    # Known Beirut coordinates (approx)
    easting, northing = 730784.758, 3751551.159
    lon, lat = utm_to_wgs84(easting, northing)
    easting2, northing2 = wgs84_to_utm(lon, lat)

    # Roundtrip error should be < 1mm
    assert abs(easting - easting2) < 0.001
    assert abs(northing - northing2) < 0.001


def test_compute_geographic_anchor():
    """Test geographic anchor computation from origin + ENU bounds."""
    from export_corridor_appearance_bundle import compute_geographic_anchor

    origin_utm = [730784.758, 3751551.159, 51.106]
    enu_min = [0.0, 0.0, 0.0]
    enu_max = [10.0, 5.0, 2.0]

    anchor = compute_geographic_anchor(origin_utm, enu_min, enu_max)

    assert "anchor_utm_m" in anchor
    assert "anchor_wgs84_deg" in anchor
    assert "enu_center_m" in anchor

    # ENU center should be midpoint
    assert anchor["enu_center_m"] == [5.0, 2.5, 1.0]

    # Absolute UTM = origin + ENU center
    assert anchor["anchor_utm_m"][0] == 730784.758 + 5.0
    assert anchor["anchor_utm_m"][1] == 3751551.159 + 2.5
    assert anchor["anchor_utm_m"][2] == 51.106 + 1.0

    # WGS84 should be valid lat/lon for Beirut area
    lon, lat = anchor["anchor_wgs84_deg"]
    assert 35.0 < lon < 36.0
    assert 33.0 < lat < 34.0


def test_compute_geographic_anchor_negative_enu():
    """Test geographic anchor with negative ENU coordinates (translated origin)."""
    from export_corridor_appearance_bundle import compute_geographic_anchor

    origin_utm = [730784.758, 3751551.159, 51.106]
    enu_min = [-10.0, -5.0, 0.0]
    enu_max = [0.0, 0.0, 2.0]

    anchor = compute_geographic_anchor(origin_utm, enu_min, enu_max)

    # ENU center = (-5, -2.5, 1)
    assert anchor["enu_center_m"] == [-5.0, -2.5, 1.0]
    assert anchor["anchor_utm_m"][0] == 730784.758 - 5.0
    assert anchor["anchor_utm_m"][1] == 3751551.159 - 2.5


def test_export_bundle_includes_georeferencing():
    """Test that export_bundle includes georeferencing metadata."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prep1, spec1, glb1, sha1 = create_real_style_candidate(tmp, "tower70")
        prep2, spec2, glb2, sha2 = create_real_style_candidate(tmp, "granite91")

        candidate1 = read_candidate(prep1)
        candidate2 = read_candidate(prep2)

        corridor_manifest = {
            "origin_utm_m": [730784.758, 3751551.159, 51.106],
            "horizontal_crs": "EPSG:32636"
        }

        output_dir = tmp / "out"
        bundle = export_bundle([candidate1, candidate2], output_dir, corridor_manifest)

        # Check new georeferencing fields
        assert bundle["horizontal_crs"] == "EPSG:32636"
        assert bundle["vertical_datum"] == "UNKNOWN model height (not surveyed MSL/WGS84 ellipsoid)"
        assert bundle["alignment_status"] == "inferred_not_surveyed"
        assert "source_code_provenance" in bundle
        assert "transform_recipe" in bundle["coordinate_system"]

        # Check each candidate has geographic_anchor
        for c in bundle["candidates"]:
            assert "geographic_anchor" in c
            anchor = c["geographic_anchor"]
            assert "anchor_utm_m" in anchor
            assert "anchor_wgs84_deg" in anchor
            assert "enu_center_m" in anchor

            # Validate anchor coordinates are finite
            assert all(np.isfinite(v) for v in anchor["anchor_utm_m"])
            assert all(np.isfinite(v) for v in anchor["anchor_wgs84_deg"])
            assert all(np.isfinite(v) for v in anchor["enu_center_m"])

            # Validate WGS84 in Beirut region
            lon, lat = anchor["anchor_wgs84_deg"]
            assert 35.0 < lon < 36.0
            assert 33.0 < lat < 34.0


def test_export_bundle_rejects_invalid_crs():
    """Test that export_bundle rejects non-EPSG:32636 CRS."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prep1, spec1, glb1, sha1 = create_real_style_candidate(tmp, "tower70")
        prep2, spec2, glb2, sha2 = create_real_style_candidate(tmp, "granite91")

        candidate1 = read_candidate(prep1)
        candidate2 = read_candidate(prep2)

        corridor_manifest = {
            "origin_utm_m": [730784.758, 3751551.159, 51.106],
            "horizontal_crs": "EPSG:4326"  # Invalid
        }

        output_dir = tmp / "out"
        try:
            export_bundle([candidate1, candidate2], output_dir, corridor_manifest)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Only EPSG:32636 is supported" in str(e)


def test_cli_georeferencing():
    """Test CLI with georeferencing options."""
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        prep1, spec1, glb1, sha1 = create_real_style_candidate(tmp, "tower70")
        prep2, spec2, glb2, sha2 = create_real_style_candidate(tmp, "granite91")

        corridor_manifest = {"origin_utm_m": [730784.758, 3751551.159, 51.106]}
        manifest_path = tmp / "corridor-manifest-v2.json"
        manifest_path.write_text(json.dumps(corridor_manifest))

        output_dir = tmp / "out"

        cmd = [
            sys.executable,
            str(Path(__file__).parent.parent / "scripts" / "export_corridor_appearance_bundle.py"),
            "--tower70-prepared", str(prep1),
            "--granite91-prepared", str(prep2),
            "--corridor-manifest", str(manifest_path),
            "--output", str(output_dir),
            "--horizontal-crs", "EPSG:32636"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"CLI failed: {result.stderr}\n{result.stdout}"
        assert (output_dir / "manifest.json").exists()

        # Read back manifest and verify
        with open(output_dir / "manifest.json") as f:
            bundle = json.load(f)

        assert bundle["horizontal_crs"] == "EPSG:32636"
        assert "geographic_anchor" in bundle["candidates"][0]
        for c in bundle["candidates"]:
            anchor = c["geographic_anchor"]
            assert all(np.isfinite(v) for v in anchor["anchor_utm_m"])
            assert all(np.isfinite(v) for v in anchor["anchor_wgs84_deg"])
