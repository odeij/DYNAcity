#!/usr/bin/env python3
"""
Deterministic audit of stone material texture inputs.
Validates manifest against actual files, computes hashes/dimensions,
reports tiling estimate (ASSUMPTION), checks GLB for UV presence.
Outputs texture-inputs.json, report.json, handoff.md, and contact sheet.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("ERROR: Pillow required. Install with: pip install Pillow", file=sys.stderr)
    sys.exit(1)

REQUIRED_ROLES = ("Diffuse", "Rough", "nor_dx")


def md5_file(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_safe_path(base: Path, target: Path) -> Path:
    """Resolve target path and ensure it's within base (no symlink escape)."""
    base_resolved = base.resolve()
    target_resolved = target.resolve()
    try:
        target_resolved.relative_to(base_resolved)
    except ValueError:
        raise ValueError(f"Path escapes allowed root: {target}")
    return target_resolved


def validate_image_role(filepath: Path, role: str) -> Tuple[bool, Dict]:
    """Validate image file and return metadata dict."""
    try:
        with Image.open(filepath) as img:
            img.load()
            width, height = img.size
            mode = img.mode
    except Exception as e:
        return False, {"error": f"Failed to open image: {e}"}

    content = filepath.read_bytes()
    actual_md5 = hashlib.md5(content).hexdigest()
    actual_sha256 = hashlib.sha256(content).hexdigest()
    size_bytes = len(content)

    # sRGB assumption: Diffuse=true, Rough/nor_dx=false (linear data)
    srgb = (role == "Diffuse")

    return True, {
        "role": role,
        "path": str(filepath),
        "filename": filepath.name,
        "width": width,
        "height": height,
        "mode": mode,
        "md5": actual_md5,
        "sha256": actual_sha256,
        "bytes": size_bytes,
        "srgb": srgb
    }


def parse_glb_uv_presence(glb_path: Path) -> Dict:
    """Parse GLB JSON chunk and check for TEXCOORD_0 (UV) attribute."""
    import struct
    result = {"has_uv": False, "attributes": [], "materials": [], "error": None}

    try:
        data = glb_path.read_bytes()
        if len(data) < 12 or data[0:4] != b'glTF':
            result["error"] = "Not a valid GLB file"
            return result

        version = struct.unpack('<I', data[4:8])[0]
        total_length = struct.unpack('<I', data[8:12])[0]

        offset = 12
        while offset + 8 <= len(data):
            chunk_length = struct.unpack('<I', data[offset:offset+4])[0]
            chunk_type = data[offset+4:offset+8]
            if chunk_type == b'JSON':
                json_data = data[offset+8:offset+8+chunk_length]
                gltf = json.loads(json_data.decode('utf-8'))

                # Check mesh primitives for TEXCOORD_0
                for mesh in gltf.get("meshes", []):
                    for prim in mesh.get("primitives", []):
                        attrs = prim.get("attributes", {})
                        result["attributes"].append(list(attrs.keys()))
                        if "TEXCOORD_0" in attrs:
                            result["has_uv"] = True

                # Materials
                for mat in gltf.get("materials", []):
                    result["materials"].append(mat.get("name", "unnamed"))
                break
            offset += 8 + chunk_length
            if offset % 4 != 0:
                offset += 4 - (offset % 4)

    except Exception as e:
        result["error"] = f"GLB parse error: {e}"

    return result


def build_contact_sheet(image_paths: List[Path], output_path: Path, tile_size: int = 512) -> bool:
    """Build a horizontal contact sheet from texture images."""
    try:
        images = []
        for p in image_paths:
            with Image.open(p) as img:
                img.load()
                # Resize to fit tile_size height, maintain aspect
                ratio = tile_size / img.height
                new_w = int(img.width * ratio)
                images.append(img.resize((new_w, tile_size), Image.LANCZOS))

        total_width = sum(img.width for img in images)
        sheet = Image.new('RGB', (total_width, tile_size), (30, 30, 30))
        draw = ImageDraw.Draw(sheet)

        x = 0
        for img in images:
            sheet.paste(img, (x, 0))
            # Label
            try:
                font = ImageFont.load_default()
            except:
                font = None
            draw.text((x + 5, 5), img.filename.name if hasattr(img, 'filename') else Path(img.filename).name if hasattr(img, 'filename') else "", fill=(255, 255, 255), font=font)
            x += img.width

        sheet.save(output_path, quality=90)
        return True
    except Exception as e:
        print(f"Contact sheet generation failed: {e}", file=sys.stderr)
        return False


def audit(manifest_path: Path, allowed_root: Path, tile_size_m: float = 2.0, glb_path: Optional[Path] = None) -> Dict:
    """Main audit function. Returns output manifest dict."""
    manifest_path = Path(manifest_path).resolve()
    allowed_root = Path(allowed_root).resolve()

    # Load manifest
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)

    asset_id = manifest.get("id")
    source_url = manifest.get("source")
    license_str = manifest.get("license")
    license_url = manifest.get("license_url")

    if not asset_id:
        raise ValueError("Manifest missing 'id'")

    # Validate each file entry
    files_output = []
    seen_roles = set()
    seen_paths = set()

    for item in manifest.get("files", []):
        role = item.get("role")
        rel_path_str = item.get("path")
        expected_md5 = item.get("md5")
        expected_bytes = item.get("bytes")

        if not role or not rel_path_str:
            raise ValueError(f"Manifest file entry missing role or path: {item}")

        if role in seen_roles:
            raise ValueError(f"Duplicate role in manifest: {role}")
        seen_roles.add(role)

        if role not in REQUIRED_ROLES:
            raise ValueError(f"Unexpected role: {role}. Required: {REQUIRED_ROLES}")

        # Resolve path safely within allowed_root
        # The manifest contains absolute paths; we must verify they're under allowed_root
        file_path = Path(rel_path_str)
        try:
            safe_path = resolve_safe_path(allowed_root, file_path)
        except ValueError as e:
            raise ValueError(f"File escapes allowed root: {rel_path_str}") from e

        safe_path_str = str(safe_path)
        if safe_path_str in seen_paths:
            raise ValueError(f"Duplicate file path: {safe_path_str}")
        seen_paths.add(safe_path_str)

        if not safe_path.is_file():
            raise ValueError(f"File not found: {safe_path}")

        # Validate image and compute actual hashes
        ok, meta = validate_image_role(safe_path, role)
        if not ok:
            raise ValueError(f"Image validation failed for {role}: {meta.get('error')}")

        # Check MD5 match
        if meta["md5"] != expected_md5:
            raise ValueError(f"MD5 mismatch for {role}: expected {expected_md5}, got {meta['md5']}")

        # Check size match
        if meta["bytes"] != expected_bytes:
            raise ValueError(f"Size mismatch for {role}: expected {expected_bytes}, got {meta['bytes']}")

        # Add tiling estimate (ASSUMPTION, not measured)
        meta["estimated_tiling_scale_m"] = tile_size_m
        meta["tiling_note"] = f"ASSUMPTION: {tile_size_m}m estimated tiling scale; not measured from source imagery"

        files_output.append(meta)

    # Check all required roles present
    missing = set(REQUIRED_ROLES) - seen_roles
    if missing:
        raise ValueError(f"Missing required roles: {missing}")

    # GLB UV check
    glb_result = {"checked": False, "has_uv": False, "projection_required": False, "note": ""}
    if glb_path and glb_path.is_file():
        glb_result = parse_glb_uv_presence(glb_path)
        glb_result["checked"] = True
        glb_result["projection_required"] = not glb_result["has_uv"]
        if glb_result["has_uv"]:
            glb_result["note"] = "GLB has TEXCOORD_0; standard UV mapping available"
        else:
            glb_result["note"] = "GLB missing TEXCOORD_0; triplanar or projection mapping required"

    # Build output manifest
    output = {
        "schema": "dynacity-stone-material-input-audit/1",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "asset_id": asset_id,
        "source_url": source_url,
        "license": license_str,
        "license_url": license_url,
        "manifest_source": str(manifest_path),
        "allowed_root": str(allowed_root),
        "tile_size_m": tile_size_m,
        "tile_size_note": f"ASSUMPTION: {tile_size_m}m estimated tiling; not measured from source imagery",
        "files": files_output,
        "glb_uv_check": glb_result,
        "status": "VALIDATED_INPUTS_ONLY_SOURCE_MATCH_REJECTED",
        "notes": [
            "All three texture files (Diffuse, Rough, nor_dx) validated against manifest MD5 and size.",
            "Actual SHA256, dimensions, and sRGB flags recorded.",
            f"Tiling scale {tile_size_m}m is an ASSUMPTION, not measured from photography.",
            "Source material fit UNACCEPTED: root inspection of actual Diffuse texture vs source photo shows dark mottled plaster NOT matching pale source cladding.",
            "Direct color application REJECTED; no native material or scene changes made.",
            "GLB UV presence checked; projection required if TEXCOORD_0 absent.",
            "Worker reconstruction consumes this manifest; no pending state."
        ]
    }

    return output


def main():
    parser = argparse.ArgumentParser(description="Audit stone material texture inputs")
    parser.add_argument("--manifest", required=True, help="Path to manifest.json")
    parser.add_argument("--allowed-root", required=True, help="Allowed root directory for texture files")
    parser.add_argument("--output", required=True, help="Output directory for reports")
    parser.add_argument("--tile-size-m", type=float, default=2.0, help="Estimated tiling scale in meters (ASSUMPTION)")
    parser.add_argument("--glb", help="Optional GLB path to check for UV presence")
    parser.add_argument("--contact-sheet", help="Optional output path for contact sheet image")

    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    allowed_root = Path(args.allowed_root)
    output_dir = Path(args.output)
    glb_path = Path(args.glb) if args.glb else None
    contact_sheet_path = Path(args.contact_sheet) if args.contact_sheet else None

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = audit(manifest_path, allowed_root, args.tile_size_m, glb_path)
    except Exception as e:
        print(f"AUDIT FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    # Write texture-inputs.json
    texture_inputs_path = output_dir / "texture-inputs.json"
    with open(texture_inputs_path, 'w', encoding='utf-8') as f:
        json.dump({
            "schema": "dynacity-stone-material-input-audit/1",
            "asset_id": result["asset_id"],
            "source_url": result["source_url"],
            "license": result["license"],
            "license_url": result["license_url"],
            "manifest_source": result["manifest_source"],
            "tile_size_m": result["tile_size_m"],
            "tile_size_note": result["tile_size_note"],
            "files": result["files"],
            "glb_uv_check": result["glb_uv_check"],
            "status": result["status"]
        }, f, indent=2)

    # Write report.json
    report_path = output_dir / "report.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)

    # Write handoff.md
    handoff_path = output_dir / "handoff.md"
    with open(handoff_path, 'w', encoding='utf-8') as f:
        f.write(f"""# Stone Material Input Audit — iteration29-stone-material

**Timestamp:** {result['timestamp']}
**Asset:** {result['asset_id']}
**Source:** {result['source_url']}
**License:** {result['license']} ({result['license_url']})
**Manifest:** {result['manifest_source']}

## Validated Texture Files

""")
        for file_meta in result["files"]:
            f.write(f"### {file_meta['role']}\n")
            f.write(f"- **File:** {file_meta['filename']}\n")
            f.write(f"- **Dimensions:** {file_meta['width']}x{file_meta['height']} ({file_meta['mode']})\n")
            f.write(f"- **MD5:** {file_meta['md5']}\n")
            f.write(f"- **SHA256:** {file_meta['sha256']}\n")
            f.write(f"- **Size:** {file_meta['bytes']} bytes\n")
            f.write(f"- **sRGB:** {file_meta['srgb']}\n")
            f.write(f"- **Estimated Tiling:** {file_meta['estimated_tiling_scale_m']}m ({file_meta['tiling_note']})\n\n")

        f.write(f"""## GLB UV Check
- **Checked:** {result['glb_uv_check']['checked']}
- **Has TEXCOORD_0 (UV):** {result['glb_uv_check']['has_uv']}
- **Projection Required:** {result['glb_uv_check']['projection_required']}
- **Note:** {result['glb_uv_check']['note']}

## Status
**{result['status']}**

## Determination
{chr(10).join('- ' + note for note in result['notes'])}

## Boundaries
- Texture files validated against manifest; hashes and dimensions are actual computed values.
- Tiling scale is an explicit ASSUMPTION (2m default), not derived from measurement.
- Source material fit is REJECTED by root visual comparison; no native changes accepted.
- GLB UV status informs mapping strategy; no geometry editing performed.
- Output manifest carries validated files + source/license + SHA256 + tile units + status ready for worker reconstruction.
""")

    # Build contact sheet
    if contact_sheet_path:
        texture_files = [Path(m['path']) for m in result['files']]
        if build_contact_sheet(texture_files, contact_sheet_path):
            print(f"Contact sheet written to {contact_sheet_path}")
        else:
            print("WARNING: Contact sheet generation failed", file=sys.stderr)

    print(f"texture-inputs.json -> {texture_inputs_path}")
    print(f"report.json -> {report_path}")
    print(f"handoff.md -> {handoff_path}")
    print(f"Status: {result['status']}")


if __name__ == "__main__":
    main()
