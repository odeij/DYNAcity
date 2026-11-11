#!/usr/bin/env python3
"""
Focused tests for audit_stone_material_inputs.py
Tests: path guarding, hash validation, role validation, tile units, duplicate detection, missing roles.
Uses temp fixtures; no external dependencies.
"""
import json
import hashlib
import tempfile
import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from audit_stone_material_inputs import (
    audit,
    validate_image_role,
    resolve_safe_path,
    parse_glb_uv_presence,
    REQUIRED_ROLES,
)


def create_test_image(path: Path, size=(256, 256), color=(128, 128, 128), mode="RGB"):
    """Create a simple test image."""
    from PIL import Image
    img = Image.new(mode, size, color)
    img.save(path)
    return path


def test_resolve_safe_path_allows_within_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        target = root / "texture.jpg"
        target.write_bytes(b"test")
        resolved = resolve_safe_path(root, target)
        assert resolved == target.resolve()


def test_resolve_safe_path_blocks_escape():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "allowed"
        root.mkdir()
        outside = Path(tmp) / "outside.jpg"
        outside.write_bytes(b"test")
        try:
            resolve_safe_path(root, outside)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "escapes allowed root" in str(e)


def test_validate_image_role_computes_hashes_and_dims():
    with tempfile.TemporaryDirectory() as tmp:
        img_path = Path(tmp) / "diffuse.jpg"
        create_test_image(img_path, size=(512, 512), color=(200, 150, 100))
        ok, meta = validate_image_role(img_path, "Diffuse")
        assert ok
        assert meta["role"] == "Diffuse"
        assert meta["width"] == 512
        assert meta["height"] == 512
        assert meta["srgb"] is True
        assert len(meta["md5"]) == 32
        assert len(meta["sha256"]) == 64
        assert meta["bytes"] > 0


def test_validate_image_role_rough_not_srgb():
    with tempfile.TemporaryDirectory() as tmp:
        img_path = Path(tmp) / "rough.jpg"
        create_test_image(img_path, size=(512, 512), color=(100, 100, 100))
        ok, meta = validate_image_role(img_path, "Rough")
        assert ok
        assert meta["srgb"] is False


def test_validate_image_role_nor_dx_not_srgb():
    with tempfile.TemporaryDirectory() as tmp:
        img_path = Path(tmp) / "nor_dx.jpg"
        create_test_image(img_path, size=(512, 512), color=(128, 128, 255))
        ok, meta = validate_image_role(img_path, "nor_dx")
        assert ok
        assert meta["srgb"] is False


def test_audit_valid_manifest_passes():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()

        # Create three test images
        diff = allowed / "diff.jpg"
        rough = allowed / "rough.jpg"
        nor = allowed / "nor.jpg"
        create_test_image(diff, (512, 512))
        create_test_image(rough, (512, 512))
        create_test_image(nor, (512, 512))

        # Compute actual hashes for manifest
        diff_md5 = hashlib.md5(diff.read_bytes()).hexdigest()
        diff_bytes = diff.stat().st_size
        rough_md5 = hashlib.md5(rough.read_bytes()).hexdigest()
        rough_bytes = rough.stat().st_size
        nor_md5 = hashlib.md5(nor.read_bytes()).hexdigest()
        nor_bytes = nor.stat().st_size

        manifest = {
            "id": "test_stone",
            "source": "https://example.com/test",
            "license": "CC0 1.0",
            "license_url": "https://example.com/license",
            "files": [
                {"role": "Diffuse", "path": str(diff), "md5": diff_md5, "bytes": diff_bytes},
                {"role": "Rough", "path": str(rough), "md5": rough_md5, "bytes": rough_bytes},
                {"role": "nor_dx", "path": str(nor), "md5": nor_md5, "bytes": nor_bytes},
            ]
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        output_dir = tmp / "out"
        result = audit(manifest_path, allowed, tile_size_m=2.0, glb_path=None)

        assert result["status"] == "VALIDATED_INPUTS_ONLY_SOURCE_MATCH_REJECTED"
        assert result["asset_id"] == "test_stone"
        assert result["tile_size_m"] == 2.0
        assert "ASSUMPTION" in result["tile_size_note"]
        assert len(result["files"]) == 3
        roles = {f["role"] for f in result["files"]}
        assert roles == {"Diffuse", "Rough", "nor_dx"}
        for f in result["files"]:
            assert "estimated_tiling_scale_m" in f
            assert f["estimated_tiling_scale_m"] == 2.0
            assert "ASSUMPTION" in f["tiling_note"]
            assert "sha256" in f and len(f["sha256"]) == 64


def test_audit_rejects_md5_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()
        diff = allowed / "diff.jpg"
        create_test_image(diff, (256, 256))
        manifest = {
            "id": "test",
            "source": "https://example.com",
            "license": "CC0",
            "license_url": "https://example.com/license",
            "files": [{"role": "Diffuse", "path": str(diff), "md5": "deadbeef" * 4, "bytes": 100}]
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        try:
            audit(manifest_path, allowed)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "MD5 mismatch" in str(e)


def test_audit_rejects_size_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()
        diff = allowed / "diff.jpg"
        create_test_image(diff, (256, 256))
        actual_md5 = hashlib.md5(diff.read_bytes()).hexdigest()
        manifest = {
            "id": "test",
            "source": "https://example.com",
            "license": "CC0",
            "license_url": "https://example.com/license",
            "files": [{"role": "Diffuse", "path": str(diff), "md5": actual_md5, "bytes": 999999}]
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        try:
            audit(manifest_path, allowed)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Size mismatch" in str(e)


def test_audit_rejects_missing_role():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()
        diff = allowed / "diff.jpg"
        rough = allowed / "rough.jpg"
        create_test_image(diff, (256, 256))
        create_test_image(rough, (256, 256))
        diff_md5 = hashlib.md5(diff.read_bytes()).hexdigest()
        diff_bytes = diff.stat().st_size
        rough_md5 = hashlib.md5(rough.read_bytes()).hexdigest()
        rough_bytes = rough.stat().st_size
        manifest = {
            "id": "test",
            "source": "https://example.com",
            "license": "CC0",
            "license_url": "https://example.com/license",
            "files": [
                {"role": "Diffuse", "path": str(diff), "md5": diff_md5, "bytes": diff_bytes},
                {"role": "Rough", "path": str(rough), "md5": rough_md5, "bytes": rough_bytes},
            ]
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        try:
            audit(manifest_path, allowed)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Missing required roles" in str(e)
            assert "nor_dx" in str(e)


def test_audit_rejects_duplicate_role():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()
        diff = allowed / "diff.jpg"
        create_test_image(diff, (256, 256))
        actual_md5 = hashlib.md5(diff.read_bytes()).hexdigest()
        actual_bytes = diff.stat().st_size
        manifest = {
            "id": "test",
            "source": "https://example.com",
            "license": "CC0",
            "license_url": "https://example.com/license",
            "files": [
                {"role": "Diffuse", "path": str(diff), "md5": actual_md5, "bytes": actual_bytes},
                {"role": "Diffuse", "path": str(diff), "md5": actual_md5, "bytes": actual_bytes},
                {"role": "Rough", "path": str(diff), "md5": actual_md5, "bytes": actual_bytes},
                {"role": "nor_dx", "path": str(diff), "md5": actual_md5, "bytes": actual_bytes},
            ]
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        try:
            audit(manifest_path, allowed)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Duplicate role" in str(e)


def test_audit_rejects_duplicate_path():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()
        diff = allowed / "diff.jpg"
        rough = allowed / "rough.jpg"
        create_test_image(diff, (256, 256))
        create_test_image(rough, (256, 256))
        diff_md5 = hashlib.md5(diff.read_bytes()).hexdigest()
        diff_bytes = diff.stat().st_size
        rough_md5 = hashlib.md5(rough.read_bytes()).hexdigest()
        rough_bytes = rough.stat().st_size
        manifest = {
            "id": "test",
            "source": "https://example.com",
            "license": "CC0",
            "license_url": "https://example.com/license",
            "files": [
                {"role": "Diffuse", "path": str(diff), "md5": diff_md5, "bytes": diff_bytes},
                {"role": "Rough", "path": str(diff), "md5": rough_md5, "bytes": rough_bytes},
                {"role": "nor_dx", "path": str(rough), "md5": rough_md5, "bytes": rough_bytes},
            ]
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        try:
            audit(manifest_path, allowed)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Duplicate file path" in str(e)


def test_audit_rejects_invalid_role():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()
        diff = allowed / "diff.jpg"
        create_test_image(diff, (256, 256))
        actual_md5 = hashlib.md5(diff.read_bytes()).hexdigest()
        actual_bytes = diff.stat().st_size
        manifest = {
            "id": "test",
            "source": "https://example.com",
            "license": "CC0",
            "license_url": "https://example.com/license",
            "files": [
                {"role": "InvalidRole", "path": str(diff), "md5": actual_md5, "bytes": actual_bytes},
                {"role": "Rough", "path": str(diff), "md5": actual_md5, "bytes": actual_bytes},
                {"role": "nor_dx", "path": str(diff), "md5": actual_md5, "bytes": actual_bytes},
            ]
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        try:
            audit(manifest_path, allowed)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "Unexpected role" in str(e)


def test_audit_rejects_path_escape():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()
        outside = tmp / "outside.jpg"
        create_test_image(outside, (256, 256))
        actual_md5 = hashlib.md5(outside.read_bytes()).hexdigest()
        actual_bytes = outside.stat().st_size
        manifest = {
            "id": "test",
            "source": "https://example.com",
            "license": "CC0",
            "license_url": "https://example.com/license",
            "files": [
                {"role": "Diffuse", "path": str(outside), "md5": actual_md5, "bytes": actual_bytes},
                {"role": "Rough", "path": str(outside), "md5": actual_md5, "bytes": actual_bytes},
                {"role": "nor_dx", "path": str(outside), "md5": actual_md5, "bytes": actual_bytes},
            ]
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        try:
            audit(manifest_path, allowed)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "escapes allowed root" in str(e)


def test_audit_tile_size_m_is_finite_positive():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()
        diff = allowed / "diff.jpg"
        rough = allowed / "rough.jpg"
        nor = allowed / "nor.jpg"
        create_test_image(diff, (256, 256))
        create_test_image(rough, (256, 256))
        create_test_image(nor, (256, 256))
        for p in [diff, rough, nor]:
            m = hashlib.md5(p.read_bytes()).hexdigest()
            b = p.stat().st_size
        # Test with various tile sizes
        for tile in [0.5, 1.0, 2.0, 10.0]:
            manifest = {
                "id": "test",
                "source": "https://example.com",
                "license": "CC0",
                "license_url": "https://example.com/license",
                "files": [
                    {"role": "Diffuse", "path": str(diff), "md5": hashlib.md5(diff.read_bytes()).hexdigest(), "bytes": diff.stat().st_size},
                    {"role": "Rough", "path": str(rough), "md5": hashlib.md5(rough.read_bytes()).hexdigest(), "bytes": rough.stat().st_size},
                    {"role": "nor_dx", "path": str(nor), "md5": hashlib.md5(nor.read_bytes()).hexdigest(), "bytes": nor.stat().st_size},
                ]
            }
            manifest_path = tmp / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            result = audit(manifest_path, allowed, tile_size_m=tile)
            assert result["tile_size_m"] == tile
            assert result["files"][0]["estimated_tiling_scale_m"] == tile


def test_parse_glb_uv_presence_no_uv():
    """Test GLB parsing on the actual iteration27 GLB which has no UVs."""
    glb_path = Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration27-granite-parapet/granite_parapet_overlay.glb")
    if glb_path.exists():
        result = parse_glb_uv_presence(glb_path)
        assert "has_uv" in result
        assert "attributes" in result
        # The iteration27 GLB has no TEXCOORD_0
        assert result["has_uv"] is False
        assert "TEXCOORD_0" not in str(result["attributes"])


def test_parse_glb_uv_presence_corrected_glb():
    """Test GLB parsing on the corrected iteration27 GLB."""
    glb_path = Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration27-granite-parapet/corrected/fraction-0.7/granite_parapet_overlay.glb")
    if glb_path.exists():
        result = parse_glb_uv_presence(glb_path)
        assert "has_uv" in result
        assert "attributes" in result


def test_cli_invocation():
    """Test CLI runs and produces expected outputs."""
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        allowed = tmp / "assets"
        allowed.mkdir()
        diff = allowed / "diff.jpg"
        rough = allowed / "rough.jpg"
        nor = allowed / "nor.jpg"
        create_test_image(diff, (256, 256))
        create_test_image(rough, (256, 256))
        create_test_image(nor, (256, 256))

        manifest = {
            "id": "cli_test",
            "source": "https://example.com",
            "license": "CC0",
            "license_url": "https://example.com/license",
            "files": [
                {"role": "Diffuse", "path": str(diff), "md5": hashlib.md5(diff.read_bytes()).hexdigest(), "bytes": diff.stat().st_size},
                {"role": "Rough", "path": str(rough), "md5": hashlib.md5(rough.read_bytes()).hexdigest(), "bytes": rough.stat().st_size},
                {"role": "nor_dx", "path": str(nor), "md5": hashlib.md5(nor.read_bytes()).hexdigest(), "bytes": nor.stat().st_size},
            ]
        }
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))

        output_dir = tmp / "out"
        contact_sheet = tmp / "contact.jpg"

        cmd = [
            sys.executable,
            str(Path(__file__).parent.parent / "scripts" / "audit_stone_material_inputs.py"),
            "--manifest", str(manifest_path),
            "--allowed-root", str(allowed),
            "--output", str(output_dir),
            "--tile-size-m", "2.0",
            "--contact-sheet", str(contact_sheet)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        assert (output_dir / "texture-inputs.json").exists()
        assert (output_dir / "report.json").exists()
        assert (output_dir / "handoff.md").exists()
        assert contact_sheet.exists()

        # Read back and verify
        with open(output_dir / "texture-inputs.json") as f:
            data = json.load(f)
        assert data["status"] == "VALIDATED_INPUTS_ONLY_SOURCE_MATCH_REJECTED"
        assert data["tile_size_m"] == 2.0
        assert len(data["files"]) == 3


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
