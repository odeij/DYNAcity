#!/usr/bin/env python3
"""
DYNAcity Corridor Canopy Pilot Preparation - CORRECTED
Additive-only script reusing EXISTING native tree assets and mapped sidewalk geometry.
No editor operations, no asset imports, no citywide application.
Dry-run only - Root handles native preview/collision/visibility/save-reload QA.

CRITICAL FIX: Selected corridor coordinates are LOCAL metres (east, north, up) from UTM origin,
NOT absolute UTM. Do NOT subtract UTM origin again.
NO explicit sidewalks in source data -> ZERO placements, explicit blockers.

Assets from: C:/private/dynacity-data/engine/street-twin-20261029/native-inventory.json
Sidewalk geometry from: C:/private/dynacity-data/engine/street-twin-20261029/charles-malek/finish/selected-street-twin.json
Library mesh paths from: native-inventory.json -> reports.library-assets-active.json.street.{tree_broadleaf,tree_conifer}.mesh
Output: D:/private/dynacity-data/engine/resemblance-20261101-2355/reference-assets/iteration18-canopy/
"""

import sys, json, hashlib, os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# ============================================================
# CONFIGURATION
# ============================================================
NATIVE_INVENTORY = Path("C:/private/dynacity-data/engine/street-twin-20261029/native-inventory.json")
SELECTED_CORRIDOR = Path("C:/private/dynacity-data/engine/street-twin-20261029/charles-malek/finish/selected-street-twin.json")
OUTPUT_DIR = Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reference-assets/iteration18-canopy")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Pilot limits - ZERO placements if no genuine sidewalk geometry
MAX_CANDIDATES = 4
MIN_CANDIDATES = 2

# Coordinate frame: Selected corridor paths are LOCAL ENU metres from UTM origin
# axes: X east, Y north, Z up (metres) relative to origin_utm_m
# Conversion to UE cm: UE_X = local_east * 100; UE_Y = -local_north * 100; UE_Z = local_up * 100
# (UE: X right, Y forward, Z up)

# Existing tree asset kinds in native inventory
TREE_ASSET_KINDS = ["tree_broadleaf", "tree_conifer"]

# Bounds check for local corridor (observed: x ~2300..2600, y ~1450..1650)
CORRIDOR_LOCAL_BOUNDS = {
    "x_min": 2000, "x_max": 2800,
    "y_min": 1300, "y_max": 1800,
    "z_min": -50, "z_max": 100  # elevation range, some null
}

# ============================================================
# UTILITIES
# ============================================================
def compute_sha256(filepath: Path) -> str:
    """Compute SHA256 of file for provenance."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def local_enu_to_ue_cm(x_local: float, y_local: float, z_local: Optional[float]) -> Tuple[float, float, float]:
    """
    Convert LOCAL ENU metres (east, north, up) to Unreal cm (X right, Y forward, Z up).
    Source coords already relative to UTM origin - do NOT subtract origin again.
    UE: X = east * 100, Y = -north * 100, Z = up * 100
    """
    z = z_local if z_local is not None else 0.0
    return (x_local * 100.0, -y_local * 100.0, z * 100.0)

def check_finite_bounds(x: float, y: float, z: Optional[float]) -> bool:
    """Check coordinates are finite and within expected corridor local bounds."""
    if x is None or y is None:
        return False
    if not (CORRIDOR_LOCAL_BOUNDS["x_min"] <= x <= CORRIDOR_LOCAL_BOUNDS["x_max"]):
        return False
    if not (CORRIDOR_LOCAL_BOUNDS["y_min"] <= y <= CORRIDOR_LOCAL_BOUNDS["y_max"]):
        return False
    if z is not None and not (CORRIDOR_LOCAL_BOUNDS["z_min"] <= z <= CORRIDOR_LOCAL_BOUNDS["z_max"]):
        return False
    return True

def load_json(path: Path) -> Dict:
    with open(path, "r") as f:
        return json.load(f)

def to_native(val):
    """Convert numpy/types to native Python for JSON."""
    if hasattr(val, "item"):
        return val.item()
    if isinstance(val, (list, tuple)):
        return [to_native(v) for v in val]
    if isinstance(val, dict):
        return {k: to_native(v) for k, v in val.items()}
    return val

def atomic_write_json(path: Path, data: Dict):
    """Write JSON atomically via tempfile+replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, suffix='.tmp') as tf:
        json.dump(to_native(data), tf, indent=2, allow_nan=False)
        tmp_name = tf.name
    os.replace(tmp_name, path)

# ============================================================
# ASSET INVENTORY
# ============================================================
def extract_tree_assets(inventory: Dict) -> List[Dict]:
    """Extract tree asset entries from native inventory AND library mesh paths."""
    assets = []

    # 1. Actor instances in native inventory (placement actors)
    actor_entries = []
    for actor in inventory.get("actors", []):
        tags = actor.get("tags", [])
        is_tree = any("tree_" in tag for tag in tags)
        if not is_tree:
            continue

        kind = "unknown"
        for tag in tags:
            if tag.startswith("street_kind:"):
                kind = tag.split(":")[1]
                break

        actor_entries.append({
            "label": actor.get("label"),
            "actor_path": actor.get("path"),
            "tags": tags,
            "kind": kind,
            "instances": actor.get("instances", 0)
        })

    # 2. Library asset mesh paths (reusable StaticMesh assets)
    reports = inventory.get("reports", {})
    lib = reports.get("library-assets-active.json", {})
    street = lib.get("street", {})

    library_meshes = {}
    for kind in TREE_ASSET_KINDS:
        if kind in street:
            library_meshes[kind] = {
                "mesh_path": street[kind].get("mesh"),
                "prepared_json": street[kind].get("prepared"),
                "provenance": street[kind].get("provenance", {}),
                "dimensions_cm": street[kind].get("dimensions_cm"),
                "source_triangles": street[kind].get("source_triangles"),
                "materials": street[kind].get("materials", {})
            }

    # Combine: each actor kind maps to library mesh
    for ae in actor_entries:
        kind = ae["kind"]
        lib_mesh = library_meshes.get(kind)
        assets.append({
            "label": ae["label"],
            "actor_path": ae["actor_path"],
            "kind": kind,
            "instances_in_native": ae["instances"],
            "library_mesh_path": lib_mesh.get("mesh_path") if lib_mesh else None,
            "library_prepared_json": lib_mesh.get("prepared_json") if lib_mesh else None,
            "library_provenance": lib_mesh.get("provenance") if lib_mesh else None,
            "library_dimensions_cm": lib_mesh.get("dimensions_cm") if lib_mesh else None,
            "library_materials": lib_mesh.get("materials") if lib_mesh else None,
            "native_load_verified": False,
            "notes": "Actor path is placement instance; library_mesh_path is reusable StaticMesh asset. Root must verify native load of mesh asset."
        })

    return assets

# ============================================================
# SIDEWALK GEOMETRY EXTRACTION
# ============================================================
def extract_sidewalk_geometry(corridor: Dict) -> List[Dict]:
    """Extract explicit sidewalk edges/geometry from selected corridor."""
    sidewalks = []

    # Check for explicit sidewalk entries (currently EMPTY)
    for sw in corridor.get("sidewalks", []):
        sidewalks.append({
            "source": "explicit_sidewalk",
            "geometry": sw,
            "path": sw.get("path", []),
            "road_id": sw.get("road_id"),
            "side": sw.get("side")
        })

    return sidewalks

def extract_road_geometry(corridor: Dict) -> List[Dict]:
    """Extract road centerline geometry from selected corridor (local ENU metres)."""
    roads = []
    for road in corridor.get("roads", []):
        path = road.get("path", [])
        if len(path) >= 2:
            # Validate coordinate frame: paths should be LOCAL metres
            valid_vertices = [p for p in path if check_finite_bounds(p[0], p[1], p[2])]
            roads.append({
                "id": road.get("id"),
                "name": road.get("name"),
                "highway": road.get("highway"),
                "osm_way_id": road.get("osm_way_id"),
                "path_local_enu_m": valid_vertices,  # [east, north, up] metres from UTM origin
                "lane_count_per_direction": road.get("lane_count_per_direction", 1),
                "lane_width_m": road.get("lane_width_m", 3.2),
                "oneway": road.get("oneway", 1),
                "vertex_count": len(valid_vertices),
                "coordinate_frame": "local_enu_metres_from_utm_origin"
            })
    return roads

# ============================================================
# MAIN
# ============================================================
def main():
    print("DYNAcity Corridor Canopy Pilot Preparation (CORRECTED)")
    print(f"Loading native inventory: {NATIVE_INVENTORY}")
    print(f"Loading selected corridor: {SELECTED_CORRIDOR}")
    print(f"Output directory: {OUTPUT_DIR}")

    # Load inputs
    inventory = load_json(NATIVE_INVENTORY)
    corridor = load_json(SELECTED_CORRIDOR)

    # Verify input hashes for provenance
    inv_sha = compute_sha256(NATIVE_INVENTORY)
    cor_sha = compute_sha256(SELECTED_CORRIDOR)
    print(f"Native inventory SHA256: {inv_sha}")
    print(f"Selected corridor SHA256: {cor_sha}")

    # Extract tree assets with library mesh paths
    tree_assets = extract_tree_assets(inventory)
    print(f"\nFound {len(tree_assets)} tree asset entries in native inventory:")
    for a in tree_assets:
        mesh = a["library_mesh_path"] or "UNKNOWN"
        print(f"  {a['label']} [{a['kind']}] x{a['instances_in_native']} -> mesh: {mesh}")

    # Extract geometry
    roads = extract_road_geometry(corridor)
    sidewalks = extract_sidewalk_geometry(corridor)
    print(f"\nRoads in corridor: {len(roads)}")
    for r in roads:
        print(f"  {r['name']} ({r['highway']}) - {r['vertex_count']} valid vertices, lanes={r['lane_count_per_direction']}")
    print(f"Explicit sidewalks: {len(sidewalks)} (EMPTY in current data)")

    # CORRECTED: ZERO placements - no genuine mapped sidewalk geometry exists
    pilot_candidates = []
    print(f"\nPilot candidates: {len(pilot_candidates)} (ZERO - no explicit sidewalk geometry)")

    # Build manifest with BLOCKED status
    manifest = {
        "schema": "dynacity-canopy-pilot/1",
        "timestamp": "2026-11-05",
        "status": "BLOCKED_NO_SIDEWALK_GEOMETRY",
        "provenance": {
            "native_inventory_path": str(NATIVE_INVENTORY),
            "native_inventory_sha256": inv_sha,
            "selected_corridor_path": str(SELECTED_CORRIDOR),
            "selected_corridor_sha256": cor_sha,
            "coordinate_system": {
                "source_crs": "EPSG:32636",
                "source_axes": "X east, Y north, Z up (metres)",
                "source_origin_utm_m": [730784.7582802251, 3751551.1589951646, 51.10578950470706],
                "selected_corridor_frame": "LOCAL ENU metres from UTM origin (do NOT subtract origin again)",
                "target_engine": "Unreal Engine",
                "target_axes": "X right, Y forward, Z up (centimetres)",
                "conversion": "UE_X = local_east * 100; UE_Y = -local_north * 100; UE_Z = local_up * 100",
                "corridor_local_bounds_m": CORRIDOR_LOCAL_BOUNDS
            }
        },
        "tree_assets_available": [
            {
                "label": a["label"],
                "actor_path": a["actor_path"],
                "kind": a["kind"],
                "instances_in_native": a["instances_in_native"],
                "library_mesh_path": a["library_mesh_path"],
                "library_prepared_json": a["library_prepared_json"],
                "library_provenance": a["library_provenance"],
                "library_dimensions_cm": a["library_dimensions_cm"],
                "library_materials": a["library_materials"],
                "native_load_verified": False,
                "notes": a["notes"]
            }
            for a in tree_assets
        ],
        "mapped_geometry": {
            "roads_count": len(roads),
            "roads": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "highway": r["highway"],
                    "osm_way_id": r["osm_way_id"],
                    "vertex_count": r["vertex_count"],
                    "lane_count_per_direction": r["lane_count_per_direction"],
                    "lane_width_m": r["lane_width_m"],
                    "coordinate_frame": r["coordinate_frame"]
                }
                for r in roads
            ],
            "explicit_sidewalks_count": len(sidewalks),
            "sidewalks": sidewalks,
            "limitation": "No explicit sidewalk/footway geometry in selected-street-twin.json. Cannot establish tree placements without mapped sidewalk edges."
        },
        "pilot_candidates": pilot_candidates,
        "blockers": [
            "No explicit sidewalk/footway geometry in selected-street-twin.json - zero mapped footway edges",
            "Road centerline paths exist but sidewalk offsets/edges are not present in source data",
            "Building facade/doorway clearance unknown - no facade geometry in corridor selection",
            "Tree asset native-load not verified - Root must confirm library mesh assets load in editor",
            "Exact species/height unmeasured from source photographs"
        ],
        "root_query": {
            "question": "Do any mapped sidewalk/footway geometries exist for this corridor in other data sources (citywide street-twin, OSM footways, imported survey)? If yes, provide path to geometry with coordinate frame and provenance.",
            "required_for_placement": [
                "Explicit sidewalk/footway edge geometry (polylines or polygons) in LOCAL ENU metres",
                "Coordinate frame confirmation (same UTM origin as selected corridor)",
                "Clearance verification: road edge, building facade, doorway/entry offsets"
            ],
            "assets_ready_for_use_if_geometry_provided": [
                a["library_mesh_path"] for a in tree_assets if a["library_mesh_path"]
            ]
        },
        "limits": {
            "max_candidates": MAX_CANDIDATES,
            "min_candidates": MIN_CANDIDATES,
            "actual_candidates": 0,
            "citywide_apply": False,
            "dry_run_only": True,
            "no_existing_assets_modified": True,
            "no_editor_operations": True,
            "no_asset_imports": True,
            "no_ungrounded_placement": True
        }
    }

    # Write manifest
    manifest_path = OUTPUT_DIR / "canopy-pilot-manifest.json"
    atomic_write_json(manifest_path, manifest)
    print(f"\nManifest written: {manifest_path}")

    # Write blocked candidates list
    candidates_path = OUTPUT_DIR / "candidate-placements.json"
    atomic_write_json(candidates_path, {
        "schema": "dynacity-canopy-candidates/1",
        "timestamp": "2026-11-05",
        "status": "BLOCKED_NO_SIDEWALK_GEOMETRY",
        "candidates": [],
        "blockers": manifest["blockers"],
        "root_query": manifest["root_query"]
    })
    print(f"Candidates (blocked) written: {candidates_path}")

    # Compact handoff
    print("\n=== CANOPY PILOT HANDOFF (CORRECTED) ===")
    print(f"Status: BLOCKED - No explicit sidewalk geometry")
    print(f"Tree assets in native inventory: {len(tree_assets)}")
    for a in tree_assets:
        mesh = a["library_mesh_path"] or "NO LIBRARY MESH"
        print(f"  {a['label']} [{a['kind']}] x{a['instances_in_native']} -> mesh: {mesh}")
    print(f"Corridor roads: {len(roads)} (local ENU metres, bounds ~x 2300-2600, y 1450-1650)")
    print(f"Explicit sidewalks: {len(sidewalks)} (EMPTY)")
    print(f"Pilot candidates: {len(pilot_candidates)} (ZERO - correctly blocked)")
    print(f"Blockers: {len(manifest['blockers'])}")
    for b in manifest["blockers"]:
        print(f"  - {b}")
    print(f"Root query: Provide mapped sidewalk geometry if available elsewhere")
    print(f"Output: {OUTPUT_DIR}")
    print("DRY RUN ONLY - No placements invented. Root must provide genuine sidewalk geometry for any placement.")

if __name__ == "__main__":
    main()
