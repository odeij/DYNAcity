#!/usr/bin/env python3
"""
DYNAcity Native Surface Coverage Index & Query Tool

Reusable OFFLINE index across ALL actual saved native cover packages (street-covers.json).
Uses dynacity.engine_city_batches.Glb decoder; GLB East/Up/South -> local ENU (x, -z, y).
Outputs index with sourceID/asset/hash/material/classification/local bounds.
Queries AABB with dry-run; meaningful local-vs-UTM conversion guard + finite bounds.

Input: C:/private/dynacity-data/engine/sea-highway/UnrealPrototype/Saved/DynaCity/street-covers.json
Output: D:/private/dynacity-data/engine/resemblance-20261101-2355/reference-assets/iteration20-surface-index/
CLI: --output <dir> --box x_min x_max y_min y_max z_min z_max (local ENU metres)

No editor/MCP/Git/nested/otherfiles. Root uses actual coverage before controlled canopy pilot.
"""

import sys, json, hashlib, struct, os
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import numpy as np

# Add dynacity source to path
sys.path.insert(0, str(Path("C:/Users/PC/DYNAcity/src")))
from dynacity.engine_city_batches import Glb

# ============================================================
# CONFIGURATION
# ============================================================
STREET_COVERS_MANIFEST = Path("C:/private/dynacity-data/engine/sea-highway/UnrealPrototype/Saved/DynaCity/street-covers.json")
DEFAULT_OUTPUT_DIR = Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reference-assets/iteration20-surface-index")

# UTM Origin (from street-covers.json)
UTM_ORIGIN_M = np.array([730784.7582802251, 3751551.1589951646, 51.10578950470706])

# Target query box (local ENU metres): x 2400..2600, north 1450..1530, z 0..30
DEFAULT_QUERY_BOX = {
    "x_min": 2400.0, "x_max": 2600.0,
    "y_min": 1450.0, "y_max": 1530.0,  # north = y
    "z_min": 0.0, "z_max": 30.0
}

# ============================================================
# UTILITIES
# ============================================================
def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def load_json(path: Path) -> Dict:
    with open(path, "r") as f:
        return json.load(f)

def to_native(val):
    """Convert numpy types to native Python for JSON."""
    if hasattr(val, "item"):
        return val.item()
    if isinstance(val, np.ndarray):
        return val.tolist()
    if isinstance(val, (list, tuple)):
        return [to_native(v) for v in val]
    if isinstance(val, dict):
        return {k: to_native(v) for k, v in val.items()}
    return val

def atomic_write_json(path: Path, data: Dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, suffix='.tmp') as tf:
        json.dump(to_native(data), tf, indent=2, allow_nan=False)
        tmp_name = tf.name
    os.replace(tmp_name, path)

def glb_eus_to_local_enu(positions: np.ndarray) -> np.ndarray:
    """
    Convert GLB coordinates (East, Up, South) to local ENU (East, North, Up).
    GLB: X=East, Y=Up, Z=South (negative North)
    Local ENU: X=East, Y=North, Z=Up
    """
    # positions is (N, 3) in GLB frame
    local = np.empty_like(positions)
    local[:, 0] = positions[:, 0]      # East -> East
    local[:, 1] = -positions[:, 2]     # South -> North (negate)
    local[:, 2] = positions[:, 1]      # Up -> Up
    return local

def check_finite_bounds(points: np.ndarray, bounds: Dict) -> bool:
    """Check if any point falls within the query bounds."""
    if len(points) == 0:
        return False
    x = points[:, 0]
    y = points[:, 1]
    z = points[:, 2]
    x_in = np.any((x >= bounds["x_min"]) & (x <= bounds["x_max"]))
    y_in = np.any((y >= bounds["y_min"]) & (y <= bounds["y_max"]))
    z_in = np.any((z >= bounds["z_min"]) & (z <= bounds["z_max"]))
    return x_in and y_in and z_in

# ============================================================
# CORE: Surface Index Builder
# ============================================================
def build_surface_index(manifest_path: Path) -> Dict:
    """Build complete surface index from street-covers.json manifest."""
    print(f"Loading manifest: {manifest_path}")
    manifest = load_json(manifest_path)

    origin_utm = np.array(manifest.get("origin_utm_m", UTM_ORIGIN_M))
    source_manifest = manifest.get("source_manifest", "")
    source_sha256 = manifest.get("source_sha256", "")
    packages = manifest.get("packages", [])

    print(f"Origin UTM: {origin_utm}")
    print(f"Source manifest: {source_manifest}")
    print(f"Source SHA256: {source_sha256}")
    print(f"Packages: {len(packages)}")

    index = {
        "schema": "dynacity-surface-index/1",
        "timestamp": "2026-11-05",
        "provenance": {
            "manifest_path": str(manifest_path),
            "manifest_sha256": compute_sha256(manifest_path),
            "source_manifest": source_manifest,
            "source_sha256": source_sha256,
            "origin_utm_m": origin_utm.tolist(),
            "coordinate_frame": "local ENU metres (X east, Y north, Z up) from UTM origin",
            "glb_to_local_conversion": "X_local = X_glb; Y_local = -Z_glb; Z_local = Y_glb"
        },
        "packages": [],
        "objects": [],
        "invalid_source_ranges": [],
        "summary": {
            "total_packages": len(packages),
            "total_objects": 0,
            "total_triangles": 0,
            "kinds": {}
        }
    }

    object_id = 0
    for pkg in packages:
        pkg_key = pkg.get("key", "")
        pkg_name = pkg.get("name", "")
        pkg_kind = pkg.get("kind", "")
        pkg_area = pkg.get("production_area", "")
        pkg_asset_path = Path(pkg.get("asset", ""))
        pkg_objects = pkg.get("objects", [])
        pkg_triangles = pkg.get("triangles", 0)

        # Skip if no triangles field (terrain_extension)
        if pkg_triangles == 0 and len(pkg_objects) == 1 and pkg_objects[0].get("source_id") == "copernicus-visual-extension":
            print(f"  Skipping {pkg_key} (terrain extension, no triangles)")
            continue

        if not pkg_asset_path.exists():
            print(f"  WARNING: Asset not found: {pkg_asset_path}")
            continue

        # Load GLB and extract mesh data
        try:
            glb = Glb(pkg_asset_path)
        except Exception as e:
            print(f"  ERROR loading GLB {pkg_asset_path}: {e}")
            continue

        # Get all objects from GLB
        glb_objects = list(glb.objects())
        # Saved cover GLBs are package batches: source object IDs live in the
        # manifest triangle ranges, not in a separate node per source object.
        positions_all=[]; faces_all=[]; offset=0
        for node, primitive, positions, normals, faces in glb_objects:
            positions_all.append(glb_eus_to_local_enu(positions))
            faces_all.append(faces + offset); offset += len(positions)
        package_geometry={'positions':np.concatenate(positions_all),
                          'faces':np.concatenate(faces_all), 'material':None}

        # Map source_id from manifest to GLB node data
        source_id_to_glb = {}
        for node, primitive, positions, normals, faces in glb_objects:
            extras = node.get("extras", {})
            source_id = extras.get("source_id", node["name"])
            # Convert positions to local ENU
            local_positions = glb_eus_to_local_enu(positions)
            source_id_to_glb[source_id] = {
                "node_name": node["name"],
                "positions": local_positions,
                "normals": normals,
                "faces": faces,
                "material": primitive.get("material", 0),
                "bounds_local_m": {
                    "min": local_positions.min(axis=0).tolist(),
                    "max": local_positions.max(axis=0).tolist()
                }
            }

        pkg_summary = {
            "key": pkg_key,
            "name": pkg_name,
            "kind": pkg_kind,
            "production_area": pkg_area,
            "asset_path": str(pkg_asset_path),
            "asset_sha256": compute_sha256(pkg_asset_path),
            "triangle_count": pkg_triangles,
            "object_count": len(pkg_objects),
            "objects": []
        }

        kind_tri_count = index["summary"]["kinds"].get(pkg_kind, {}).get("triangles", 0)
        kind_obj_count = index["summary"]["kinds"].get(pkg_kind, {}).get("objects", 0)

        for obj in pkg_objects:
            source_id = obj.get("source_id", "")
            tri_start = obj.get("triangle_start", 0)
            tri_count = obj.get("triangle_count", 0)

            # Find matching GLB object
            glb_data = package_geometry
            if glb_data is None:
                print(f"    WARNING: source_id {source_id} not found in GLB {pkg_key}")
                continue

            # Extract triangles for this object
            faces = glb_data["faces"]
            obj_faces = faces[tri_start:tri_start + tri_count]
            if len(obj_faces) != tri_count:
                index['invalid_source_ranges'].append({'package':pkg_key,'source_id':source_id,
                    'first_triangle':tri_start,'declared_count':tri_count,'available_count':len(obj_faces)})
                continue
            positions = glb_data["positions"]
            obj_positions = positions  # all positions, faces reference subset

            # Compute local bounds for this specific object's triangles
            if len(obj_faces) > 0:
                tri_verts = obj_positions[obj_faces.flatten()]
                bounds_min = tri_verts.min(axis=0).tolist()
                bounds_max = tri_verts.max(axis=0).tolist()
            else:
                bounds_min = [0, 0, 0]
                bounds_max = [0, 0, 0]

            obj_record = {
                "global_object_id": object_id,
                "package_key": pkg_key,
                "package_kind": pkg_kind,
                "asset_path": str(pkg_asset_path),
                "production_area": pkg_area,
                "source_id": source_id,
                "material_index": glb_data["material"],
                "triangle_start": tri_start,
                "triangle_count": tri_count,
                "bounds_local_m": {"min": bounds_min, "max": bounds_max},
                "vertex_count": len(obj_positions)
            }

            pkg_summary["objects"].append(obj_record)
            index["objects"].append(obj_record)
            object_id += 1
            index["summary"]["total_triangles"] += tri_count

        index["summary"]["total_objects"] += len(pkg_summary["objects"])
        index["summary"]["kinds"][pkg_kind] = {
            "triangles": kind_tri_count + sum(o['triangle_count'] for o in pkg_summary['objects']),
            "objects": kind_obj_count + len(pkg_summary["objects"])
        }

        index["packages"].append(pkg_summary)
        print(f"  {pkg_key} ({pkg_kind}): {len(pkg_summary['objects'])} objects, {pkg_summary['triangle_count']} triangles")

    index["summary"]["total_packages"] = len(index["packages"])
    print(f"Index built: {index['summary']['total_packages']} packages, {index['summary']['total_objects']} objects, {index['summary']['total_triangles']} triangles")
    return index

def query_aabb(index: Dict, box: Dict) -> Dict:
    """Query surface index for objects intersecting AABB in local ENU metres."""
    x_min, x_max = box["x_min"], box["x_max"]
    y_min, y_max = box["y_min"], box["y_max"]
    z_min, z_max = box["z_min"], box["z_max"]

    print(f"\nQuerying AABB: x[{x_min}, {x_max}], y[{y_min}, {y_max}], z[{z_min}, {z_max}]")

    # One truncated source range makes the package's other offsets uncertain.
    # Derive this from retained errors too, so older saved indices are guarded.
    quarantined = sorted({item['package'] for item in index.get('invalid_source_ranges', [])})
    results = {
        "query_box": box,
        "quarantined_packages": quarantined,
        "quarantine_reason": "Source triangle ranges require reconciliation; whole affected packages excluded",
        "matches": [],
        "summary": {
            "total_objects_checked": len(index["objects"]),
            "objects_intersecting": 0,
            "triangles_intersecting": 0,
            "kinds": {}
        }
    }

    geometry_cache={}
    for obj in index["objects"]:
        if obj['package_key'] in quarantined:
            continue
        bounds = obj["bounds_local_m"]
        obj_min = bounds["min"]
        obj_max = bounds["max"]

        # AABB intersection test
        if (obj_max[0] >= x_min and obj_min[0] <= x_max and
            obj_max[1] >= y_min and obj_min[1] <= y_max and
            obj_max[2] >= z_min and obj_min[2] <= z_max):

            path=obj['asset_path']
            if path not in geometry_cache:
                triangles=[]
                for node,primitive,pos,norm,faces in Glb(Path(path)).objects():
                    triangles.append(glb_eus_to_local_enu(pos)[faces])
                geometry_cache[path]=np.concatenate(triangles)
            all_triangles=geometry_cache[path]
            tri=all_triangles[obj['triangle_start']:obj['triangle_start']+obj['triangle_count']]
            lo=tri.min(axis=1); hi=tri.max(axis=1)
            mask=(hi[:,0]>=x_min)&(lo[:,0]<=x_max)&(hi[:,1]>=y_min)&(lo[:,1]<=y_max)&(hi[:,2]>=z_min)&(lo[:,2]<=z_max)
            matched=int(mask.sum())
            if not matched:continue
            match = {
                "global_object_id": obj["global_object_id"],
                "package_key": obj["package_key"],
                "package_kind": obj["package_kind"],
                "production_area": obj["production_area"],
                "source_id": obj["source_id"],
                "material_index": obj["material_index"],
                "triangle_start": obj["triangle_start"],
                "triangle_count": matched,
                "source_object_triangle_count":obj['triangle_count'],
                "triangles_local_enu":tri[mask].tolist(),
                "query_method":"Triangle AABB broadphase, not polygon containment or clearance proof",
                "bounds_local_m": bounds
            }
            results["matches"].append(match)
            results["summary"]["objects_intersecting"] += 1
            results["summary"]["triangles_intersecting"] += matched

            kind = obj["package_kind"]
            if kind not in results["summary"]["kinds"]:
                results["summary"]["kinds"][kind] = {"objects": 0, "triangles": 0}
            results["summary"]["kinds"][kind]["objects"] += 1
            results["summary"]["kinds"][kind]["triangles"] += matched

    print(f"Query results: {results['summary']['objects_intersecting']} objects, {results['summary']['triangles_intersecting']} triangles")
    for kind, counts in results["summary"]["kinds"].items():
        print(f"  {kind}: {counts['objects']} objects, {counts['triangles']} triangles")

    return results

# ============================================================
# CLI
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="DYNAcity Native Surface Coverage Index & Query")
    parser.add_argument("--output", type=str, default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--box", nargs=6, type=float, metavar=("X_MIN", "X_MAX", "Y_MIN", "Y_MAX", "Z_MIN", "Z_MAX"),
                        help="Query AABB in local ENU metres (east, north, up)")
    parser.add_argument("--build-only", action="store_true", help="Build index only, skip query")
    parser.add_argument("--query-only", action="store_true", help="Query existing index (requires --output)")
    parser.add_argument("--dryrun", action="store_true", help="Dry run - no writes")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    query_box = DEFAULT_QUERY_BOX.copy()
    if args.box:
        query_box = {
            "x_min": args.box[0], "x_max": args.box[1],
            "y_min": args.box[2], "y_max": args.box[3],
            "z_min": args.box[4], "z_max": args.box[5]
        }

    index_path = output_dir / "surface-index.json"
    query_path = output_dir / "surface-query.json"
    handoff_path = output_dir / "handoff.md"

    if not args.query_only:
        # Build index
        print("=" * 60)
        print("BUILDING SURFACE INDEX")
        print("=" * 60)
        index = build_surface_index(STREET_COVERS_MANIFEST)

        if not args.dryrun:
            atomic_write_json(index_path, index)
            print(f"Index written: {index_path}")
        else:
            print("DRY RUN - index not written")
    else:
        # Load existing index
        print(f"Loading index: {index_path}")
        index = load_json(index_path)

    if not args.build_only:
        # Run query
        print("\n" + "=" * 60)
        print("QUERYING AABB")
        print("=" * 60)
        query_results = query_aabb(index, query_box)

        if not args.dryrun:
            atomic_write_json(query_path, query_results)
            print(f"Query results written: {query_path}")
        else:
            print("DRY RUN - query results not written")

    # Generate handoff report
    if not args.dryrun:
        handoff = f"""# DYNAcity Surface Coverage Index & Query - Iteration 20

## Summary
- **Index built**: {index['summary']['total_packages']} packages, {index['summary']['total_objects']} objects, {index['summary']['total_triangles']} triangles
- **Query AABB**: x[{query_box['x_min']}, {query_box['x_max']}], y[{query_box['y_min']}, {query_box['y_max']}], z[{query_box['z_min']}, {query_box['z_max']}] (local ENU metres)
- **Objects intersecting**: {query_results['summary']['objects_intersecting'] if 'query_results' in locals() else 'N/A'}
- **Triangles intersecting**: {query_results['summary']['triangles_intersecting'] if 'query_results' in locals() else 'N/A'}

## Provenance
- **Manifest**: {STREET_COVERS_MANIFEST}
- **Origin UTM**: {UTM_ORIGIN_M}
- **Coordinate frame**: Local ENU metres (X east, Y north, Z up) from UTM origin
- **GLB -> Local conversion**: X_local = X_glb; Y_local = -Z_glb; Z_local = Y_glb

## Kinds in Index
"""
        for kind, counts in index["summary"]["kinds"].items():
            handoff += f"- {kind}: {counts['objects']} objects, {counts['triangles']} triangles\n"

        if 'query_results' in locals():
            handoff += "\n## Query Results by Kind\n"
            for kind, counts in query_results["summary"]["kinds"].items():
                handoff += f"- {kind}: {counts['objects']} objects, {counts['triangles']} triangles\n"

            handoff += "\n## Known Root Pedestrian Crosscheck\n"
            handoff += "- Previous root query (pedestrian only, all 12 districts): 0 target triangles\n"
            handoff += "- This index includes ALL kinds (not just pedestrian)\n"
            handoff += "- Crosscheck: pedestrian kind objects in query box should match root 0-triangle result\n"

        handoff += f"""
## Files
- `surface-index.json` - Full surface index
- `surface-query.json` - Query results for target AABB
- `handoff.md` - This report

## CLI Usage
```
python scripts/query_native_surface_coverage.py --output <dir> --box 2400 2600 1450 1530 0 30 [--build-only|--query-only] [--dryrun]
```

## Limitations
- Object-level bounds intersection only (triangle-level containment requires GLB reload)
- No semantic road->safe-footway assumption
- No invented tree coords/assumed sidewalk offsets
- Root must verify actual clearance using XY triangles + height sampling
"""
        with open(handoff_path, "w") as f:
            f.write(handoff)
        print(f"\nHandoff written: {handoff_path}")

    # Compact console output
    print("\n=== HANDOFF ===")
    print(f"Index: {index['summary']['total_packages']} packages, {index['summary']['total_objects']} objects, {index['summary']['total_triangles']} triangles")
    if 'query_results' in locals():
        print(f"Query: {query_results['summary']['objects_intersecting']} objects, {query_results['summary']['triangles_intersecting']} triangles intersect AABB")
        for kind, counts in query_results["summary"]["kinds"].items():
            print(f"  {kind}: {counts['objects']} obj, {counts['triangles']} tri")
    print(f"Output: {output_dir}")

if __name__ == "__main__":
    main()
