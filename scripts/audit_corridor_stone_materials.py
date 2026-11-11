#!/usr/bin/env python3
"""Deterministic audit of existing stone/granite/limestone materials in native DYNAcity content.
Scans actual .uasset files, computes hashes, reads assignment from iteration27 candidate-persistence.json,
checks for texture assets with provenance/license. Outputs report.json and handoff.md to iteration28-stone-material.
"""
import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime

NATIVE_ROOT = Path("C:/private/dynacity-data/engine/sea-highway/UnrealPrototype/Content/DynaCity")
ITER28_DIR = Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reference-assets/iteration28-stone-material")
CANDIDATE_PERSISTENCE = Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration27-granite-parapet/candidate-persistence.json")
ASSETS_INBOX = Path("C:/private/dynacity-data/assets-inbox")

STONE_KEYWORDS = ("stone", "granite", "limestone")

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def find_stone_materials(root: Path) -> list:
    """Find all .uasset files with stone/granite/limestone in their path."""
    materials = []
    for uasset in root.rglob("*.uasset"):
        name = uasset.name.lower()
        if any(kw in name for kw in STONE_KEYWORDS):
            rel = uasset.relative_to(root)
            materials.append({
                "path": str(uasset),
                "relative_path": str(rel),
                "name": uasset.name,
                "size_bytes": uasset.stat().st_size,
                "sha256": sha256_file(uasset)
            })
    return materials

def read_candidate_persistence(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}

def scan_texture_assets() -> list:
    """Scan assets-inbox for stone-related texture sets with license/provenance."""
    textures = []
    for manifest_path in ASSETS_INBOX.rglob("manifest.json"):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            for asset in data.get("assets", [data] if data.get("id") else []):
                name = asset.get("id", "").lower()
                if any(kw in name for kw in STONE_KEYWORDS):
                    files = []
                    for item in asset.get("files", []):
                        path = Path(item.get("path", ""))
                        actual = {**item, "exists": path.is_file()}
                        if path.is_file():
                            content = path.read_bytes()
                            actual.update(actual_sha256=hashlib.sha256(content).hexdigest(),
                                          manifest_md5_match=hashlib.md5(content).hexdigest() == item.get("md5"),
                                          manifest_size_match=len(content) == item.get("bytes"))
                        files.append(actual)
                    verified = bool(files) and all(f.get("manifest_md5_match") and f.get("manifest_size_match") for f in files)
                    textures.append({
                        "source_manifest": str(manifest_path),
                        "asset_id": asset.get("id"),
                        "license": asset.get("license"),
                        "license_url": asset.get("license_url"),
                        "source_url": asset.get("source"),
                        "sha256": asset.get("sha256"),
                        "files": files,
                        "actual_files_verified": verified,
                        "use_note": asset.get("use")
                    })
        except Exception:
            pass
    return textures

def main():
    print(f"Scanning native root: {NATIVE_ROOT}")
    materials = find_stone_materials(NATIVE_ROOT)
    print(f"Found {len(materials)} stone/granite/limestone material .uasset files")

    candidate = read_candidate_persistence(CANDIDATE_PERSISTENCE)
    print(f"Candidate persistence: {candidate.get('materials', [])}")

    texture_assets = scan_texture_assets()
    print(f"Found {len(texture_assets)} stone-related texture asset sets in assets-inbox")

    # Check if current assignment uses actual texture (not constant-color)
    current_material = candidate.get("materials", [None])[0]
    has_verified_texture = any(t["actual_files_verified"] and t.get("license") == "CC0 1.0" for t in texture_assets)

    # Determine status
    if has_verified_texture:
        status = "VERIFIED_LICENSED_INPUTS_SOURCE_MATCH_UNACCEPTED"
    else:
        status = "BLOCKED_NO_VERIFIED_REUSE_TEXTURE"

    report = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "native_root": str(NATIVE_ROOT),
        "candidate_persistence_path": str(CANDIDATE_PERSISTENCE),
        "current_granite_assignment": current_material,
        "stone_materials_found": materials,
        "texture_assets_with_provenance": texture_assets,
        "has_verified_reuse_texture": has_verified_texture,
        "status": status,
        "notes": "Filename inventory does not prove native material graphs or texture assignments. Verified CC0 source files may support an illustrative candidate; they do not establish a match to photographed granite. No candidate material or scene change accepted."
    }

    ITER28_DIR.mkdir(parents=True, exist_ok=True)
    (ITER28_DIR / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    handoff = f"""# Stone Material Audit Handoff — iteration28-stone-material

**Timestamp:** {report['timestamp']}
**Native Root Scanned:** {NATIVE_ROOT}
**Candidate Persistence:** {CANDIDATE_PERSISTENCE}

## Current Granite Assignment
{current_material}

## Stone/Granite/Limestone Materials Found ({len(materials)} .uasset files)
"""
    for m in materials:
        handoff += f"- `{m['relative_path']}` — {m['size_bytes']} bytes — sha256:{m['sha256'][:16]}...\n"

    handoff += f"""
## Texture Assets with Provenance/License ({len(texture_assets)} sets)
"""
    for t in texture_assets:
        handoff += f"- **{t['asset_id']}** — {t['license']} — {t['source_url']}\n"
        handoff += f"  Use note: {t['use_note']}\n"
        for f in t['files']:
            handoff += f"  - {f.get('role', 'file')}: {Path(f.get('path', '')).name}\n"

    handoff += f"""
## Status: {status}

## Determination
{report['notes']}

## Boundaries
- Native asset filenames alone do not establish material class or texture wiring.
- The existing CC0 inputs are illustrative and are not a Beirut survey or measured granite match.
- Actual input file verification is recorded per file; native import and visual matching remain pending.

## Next Steps
No new download, material creation, assignment or scene save occurred. A future isolated candidate must preserve original materials, document estimated tiling and pass actual matched-view review before acceptance.
"""
    (ITER28_DIR / "handoff.md").write_text(handoff, encoding="utf-8")
    print(f"Report written to {ITER28_DIR / 'report.json'}")
    print(f"Handoff written to {ITER28_DIR / 'handoff.md'}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
