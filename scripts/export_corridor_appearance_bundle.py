#!/usr/bin/env python3
"""
Export renderer-neutral corridor appearance bundle with georeferencing.
Packages two approved-as-optional geometry candidates (tower70 upper infill, granite91 parapet overlay)
into a portable GLB bundle with computed bounds, full provenance, and atomic writes.
"""
import argparse
import hashlib
import json
import struct
import shutil
import sys
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass

try:
    import numpy as np
except ImportError:
    print("ERROR: numpy required. Install with: pip install numpy", file=sys.stderr)
    sys.exit(1)

try:
    from pyproj import Transformer
except ImportError:
    print("ERROR: pyproj required for georeferencing. Install with: pip install pyproj", file=sys.stderr)
    sys.exit(1)


# Known source UUIDs from iteration3 corridor-manifest-v2.json
KNOWN_SOURCE_UUIDS = {
    "tower70": "beirut001:bde30f76-7868-4187-92a7-a18270c58815",
    "granite91": "beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1"
}

# Reverse mapping for UUID -> key
UUID_TO_KEY = {v: k for k, v in KNOWN_SOURCE_UUIDS.items()}


@dataclass
class GeometryCandidate:
    """A single geometry candidate for export."""
    key: str  # "tower70" or "granite91"
    prepared_path: Path
    spec_path: Path
    glb_path: Path
    sha256: str
    source_id_full: str
    geometry_kind: str
    fractional_estimate: float


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_safe_path(base: Path, target: Path) -> Path:
    """Resolve target path and ensure it's within base (no symlink escape)."""
    base_resolved = base.resolve()
    target_resolved = target.resolve()
    try:
        target_resolved.relative_to(base_resolved)
    except ValueError:
        raise ValueError(f"Path escapes allowed root: {target}")
    return target_resolved


def parse_glb_json_chunk(glb_path: Path) -> dict:
    """Parse GLB and return the JSON chunk as dict. Validates total length and chunk structure."""
    data = glb_path.read_bytes()
    if len(data) < 12:
        raise ValueError(f"GLB too small for header: {glb_path}")
    if data[0:4] != b'glTF':
        raise ValueError(f"Not a valid GLB (missing magic): {glb_path}")

    version = struct.unpack('<I', data[4:8])[0]
    total_length = struct.unpack('<I', data[8:12])[0]

    if total_length != len(data):
        raise ValueError(f"GLB total length mismatch: header says {total_length}, actual {len(data)}")

    offset = 12
    json_chunk = None
    bin_chunk = None

    while offset + 8 <= len(data):
        if offset + 4 > len(data):
            raise ValueError("Truncated chunk header")
        chunk_length = struct.unpack('<I', data[offset:offset+4])[0]
        chunk_type = data[offset+4:offset+8]

        if offset + 8 + chunk_length > len(data):
            raise ValueError(f"Chunk extends past file end: type={chunk_type}, length={chunk_length}")

        if chunk_type == b'JSON':
            if json_chunk is not None:
                raise ValueError("Multiple JSON chunks not supported")
            json_data = data[offset+8:offset+8+chunk_length]
            json_chunk = json.loads(json_data.decode('utf-8'))
        elif chunk_type == b'BIN\x00':
            if bin_chunk is not None:
                raise ValueError("Multiple BIN chunks not supported")
            bin_chunk = data[offset+8:offset+8+chunk_length]

        offset += 8 + chunk_length
        if offset % 4 != 0:
            offset += 4 - (offset % 4)

    if json_chunk is None:
        raise ValueError("No JSON chunk found in GLB")
    if bin_chunk is None:
        raise ValueError("No BIN chunk found in GLB")

    return json_chunk


def get_bin_data(glb_path: Path) -> bytes:
    """Extract binary chunk data from GLB, validating structure."""
    data = glb_path.read_bytes()
    if len(data) < 12 or data[0:4] != b'glTF':
        raise ValueError(f"Not a valid GLB: {glb_path}")

    total_length = struct.unpack('<I', data[8:12])[0]
    if total_length != len(data):
        raise ValueError(f"GLB total length mismatch")

    offset = 12
    while offset + 8 <= len(data):
        chunk_length = struct.unpack('<I', data[offset:offset+4])[0]
        chunk_type = data[offset+4:offset+8]

        if offset + 8 + chunk_length > len(data):
            raise ValueError(f"Chunk extends past file end")

        if chunk_type == b'BIN\x00':
            return data[offset+8:offset+8+chunk_length]

        offset += 8 + chunk_length
        if offset % 4 != 0:
            offset += 4 - (offset % 4)

    raise ValueError("No BIN chunk found in GLB")


def validate_gltf_structure(gltf: dict) -> Tuple[List[int], Dict[int, List[int]]]:
    """
    Validate GLTF structure and return (scene_node_indices, mesh_to_nodes_map).
    Raises ValueError for cycles, multiple parents, invalid indices, unused meshes.
    """
    nodes = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    scenes = gltf.get("scenes", [])

    if not scenes:
        raise ValueError("No scenes in GLTF")

    scene_idx = gltf.get("scene", 0)
    if scene_idx >= len(scenes):
        raise ValueError(f"Invalid scene index: {scene_idx}")

    scene = scenes[scene_idx]
    scene_node_indices = scene.get("nodes", [])

    # Validate node indices
    for n_idx in scene_node_indices:
        if n_idx >= len(nodes):
            raise ValueError(f"Scene references invalid node index: {n_idx}")

    # Build parent map and detect cycles/multiple parents
    parent_of = {}
    visited = set()
    stack = set()

    def visit(node_idx: int):
        if node_idx in stack:
            raise ValueError(f"Node cycle detected at index {node_idx}")
        if node_idx in visited:
            return
        stack.add(node_idx)
        node = nodes[node_idx]
        for child in node.get("children", []):
            if child >= len(nodes):
                raise ValueError(f"Node {node_idx} references invalid child index: {child}")
            if child in parent_of:
                raise ValueError(f"Node {child} has multiple parents ({parent_of[child]} and {node_idx})")
            parent_of[child] = node_idx
            visit(child)
        stack.remove(node_idx)
        visited.add(node_idx)

    for n_idx in scene_node_indices:
        visit(n_idx)

    # Map meshes to nodes that reference them (only reachable from scene)
    mesh_to_nodes = {}
    for n_idx in visited:
        node = nodes[n_idx]
        mesh_idx = node.get("mesh")
        if mesh_idx is not None:
            if mesh_idx >= len(meshes):
                raise ValueError(f"Node {n_idx} references invalid mesh index: {mesh_idx}")
            mesh_to_nodes.setdefault(mesh_idx, []).append(n_idx)

    return list(visited), mesh_to_nodes


def compute_node_transform(gltf: dict, node_idx: int, parent_map: Dict[int, int]) -> np.ndarray:
    """Compute 4x4 transform matrix for a node (including parent hierarchy)."""
    node = gltf["nodes"][node_idx]
    matrix = np.eye(4, dtype=np.float32)

    if "matrix" in node:
        matrix = np.array(node["matrix"], dtype=np.float32).reshape(4, 4)
    else:
        if "translation" in node:
            t = node["translation"]
            matrix[0:3, 3] = t
        if "rotation" in node:
            x, y, z, w = node["rotation"]
            xx, yy, zz = x*x, y*y, z*z
            xy, xz, yz = x*y, x*z, y*z
            wx, wy, wz = w*x, w*y, w*z
            matrix[0,0] = 1 - 2*(yy + zz)
            matrix[0,1] = 2*(xy - wz)
            matrix[0,2] = 2*(xz + wy)
            matrix[1,0] = 2*(xy + wz)
            matrix[1,1] = 1 - 2*(xx + zz)
            matrix[1,2] = 2*(yz - wx)
            matrix[2,0] = 2*(xz - wy)
            matrix[2,1] = 2*(yz + wx)
            matrix[2,2] = 1 - 2*(xx + yy)
        if "scale" in node:
            s = node["scale"]
            matrix[0,0] *= s[0]; matrix[1,1] *= s[1]; matrix[2,2] *= s[2]

    # Apply parent transforms
    parent = parent_map.get(node_idx)
    while parent is not None:
        parent_node = gltf["nodes"][parent]
        parent_matrix = np.eye(4, dtype=np.float32)
        if "matrix" in parent_node:
            parent_matrix = np.array(parent_node["matrix"], dtype=np.float32).reshape(4, 4)
        else:
            if "translation" in parent_node:
                t = parent_node["translation"]
                parent_matrix[0:3, 3] = t
            if "rotation" in parent_node:
                x, y, z, w = parent_node["rotation"]
                xx, yy, zz = x*x, y*y, z*z
                xy, xz, yz = x*y, x*z, y*z
                wx, wy, wz = w*x, w*y, w*z
                parent_matrix[0,0] = 1 - 2*(yy + zz)
                parent_matrix[0,1] = 2*(xy - wz)
                parent_matrix[0,2] = 2*(xz + wy)
                parent_matrix[1,0] = 2*(xy + wz)
                parent_matrix[1,1] = 1 - 2*(xx + zz)
                parent_matrix[1,2] = 2*(yz - wx)
                parent_matrix[2,0] = 2*(xz - wy)
                parent_matrix[2,1] = 2*(yz + wx)
                parent_matrix[2,2] = 1 - 2*(xx + yy)
            if "scale" in parent_node:
                s = parent_node["scale"]
                parent_matrix[0,0] *= s[0]; parent_matrix[1,1] *= s[1]; parent_matrix[2,2] *= s[2]
        matrix = parent_matrix @ matrix
        parent = parent_map.get(parent)

    return matrix


def compute_transformed_bounds(glb_path: Path) -> Tuple[List[float], List[float]]:
    """
    Compute world-space bounds by applying node transforms to position accessors.
    Only processes meshes reachable from the selected scene.
    Returns (min_xyz, max_xyz) in GLTF scene coordinates (Y-up).
    """
    gltf = parse_glb_json_chunk(glb_path)
    bin_data = get_bin_data(glb_path)

    # Validate structure and get reachable nodes/meshes
    reachable_nodes, mesh_to_nodes = validate_gltf_structure(gltf)

    # Build parent map for reachable nodes
    parent_map = {}
    for n_idx in reachable_nodes:
        node = gltf["nodes"][n_idx]
        for child in node.get("children", []):
            if child in reachable_nodes:
                parent_map[child] = n_idx

    # Collect all mesh primitive positions with their node transforms
    all_world_positions = []

    for mesh_idx, node_indices in mesh_to_nodes.items():
        mesh = gltf["meshes"][mesh_idx]
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "POSITION" not in attrs:
                continue
            acc_idx = attrs["POSITION"]
            if acc_idx >= len(gltf.get("accessors", [])):
                raise ValueError(f"Primitive references invalid accessor index: {acc_idx}")
            accessor = gltf["accessors"][acc_idx]
            if accessor.get("type") != "VEC3" or accessor.get("componentType") != 5126:
                continue

            bv_idx = accessor.get("bufferView")
            if bv_idx is None or bv_idx >= len(gltf.get("bufferViews", [])):
                raise ValueError(f"Accessor {acc_idx} references invalid bufferView: {bv_idx}")
            bv = gltf["bufferViews"][bv_idx]
            byte_offset = bv.get("byteOffset", 0) + accessor.get("byteOffset", 0)
            count = accessor["count"]
            stride = bv.get("byteStride", 12)

            # Validate bounds
            if count <= 0:
                continue
            if stride < 12:
                raise ValueError(f"BufferView stride too small for VEC3: {stride}")
            max_offset = byte_offset + (count - 1) * stride + 12
            if max_offset > len(bin_data):
                raise ValueError(f"Accessor {acc_idx} reads past buffer end: need {max_offset}, have {len(bin_data)}")

            # Extract local positions
            local_positions = []
            for i in range(count):
                pos_offset = byte_offset + i * stride
                x, y, z = struct.unpack('<fff', bin_data[pos_offset:pos_offset+12])
                local_positions.append([x, y, z, 1.0])
            local_positions = np.array(local_positions, dtype=np.float32)

            # Transform by each referencing node
            for node_idx in node_indices:
                transform = compute_node_transform(gltf, node_idx, parent_map)
                world_positions = (transform @ local_positions.T).T
                all_world_positions.append(world_positions[:, :3])

    if not all_world_positions:
        raise ValueError("No valid POSITION accessors found in reachable meshes")

    all_pos = np.vstack(all_world_positions)
    min_vals = all_pos.min(axis=0).tolist()
    max_vals = all_pos.max(axis=0).tolist()

    return min_vals, max_vals


def glb_yup_to_local_enu(min_yup: List[float], max_yup: List[float]) -> Tuple[List[float], List[float]]:
    """
    Convert GLTF Y-up bounds to local ENU (East, North, Up) bounds.
    GLTF: X=East, Y=Up, Z=-North (right-handed, Y-up)
    ENU:  X=East, Y=North, Z=Up
    So: enu_x = gltf_x, enu_y = -gltf_z, enu_z = gltf_y
    """
    corners = [
        [min_yup[0], min_yup[1], min_yup[2]],
        [min_yup[0], min_yup[1], max_yup[2]],
        [min_yup[0], max_yup[1], min_yup[2]],
        [min_yup[0], max_yup[1], max_yup[2]],
        [max_yup[0], min_yup[1], min_yup[2]],
        [max_yup[0], min_yup[1], max_yup[2]],
        [max_yup[0], max_yup[1], min_yup[2]],
        [max_yup[0], max_yup[1], max_yup[2]],
    ]
    enu_corners = np.array([[c[0], -c[2], c[1]] for c in corners])
    enu_min = enu_corners.min(axis=0).tolist()
    enu_max = enu_corners.max(axis=0).tolist()
    return enu_min, enu_max


def source_file_hashes() -> dict:
    """Record actual source bytes without invoking Git or depending on checkout state."""
    root = Path(__file__).resolve().parents[1]
    names = ['src/dynacity/beirut001_pack.py', 'src/dynacity/engine_city_batches.py']
    return {name: sha256_file(root / name) for name in names}


def utm_to_wgs84(easting: float, northing: float, zone: int = 36, northern: bool = True) -> Tuple[float, float]:
    """Convert UTM coordinates to WGS84 (longitude, latitude)."""
    transformer = Transformer.from_crs(f"EPSG:326{zone}", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(easting, northing)
    return lon, lat


def wgs84_to_utm(lon: float, lat: float, zone: int = 36, northern: bool = True) -> Tuple[float, float]:
    """Convert WGS84 (longitude, latitude) to UTM coordinates."""
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:326{zone}", always_xy=True)
    easting, northing = transformer.transform(lon, lat)
    return easting, northing


def compute_geographic_anchor(origin_utm: List[float], enu_min: List[float], enu_max: List[float]) -> dict:
    """
    Compute geographic anchor from origin UTM + local ENU bounds center.
    Returns dict with anchor_utm, anchor_wgs84, enu_center.
    """
    # ENU bounds center in local coordinates
    enu_center = [
        (enu_min[0] + enu_max[0]) / 2.0,
        (enu_min[1] + enu_max[1]) / 2.0,
        (enu_min[2] + enu_max[2]) / 2.0
    ]

    # Absolute UTM = origin + local ENU (east, north)
    # origin_utm = [easting, northing, elevation]
    anchor_utm = [
        origin_utm[0] + enu_center[0],
        origin_utm[1] + enu_center[1],
        origin_utm[2] + enu_center[2]
    ]

    # Convert to WGS84
    anchor_lon, anchor_lat = utm_to_wgs84(anchor_utm[0], anchor_utm[1])

    return {
        "anchor_utm_m": anchor_utm,
        "anchor_wgs84_deg": [anchor_lon, anchor_lat],
        "enu_center_m": enu_center
    }


def read_candidate(prepared_path: Path) -> GeometryCandidate:
    """Read prepared.json and resolve all referenced files. Identity from spec.source_id_full only."""
    with open(prepared_path) as f:
        prepared = json.load(f)

    spec_path = Path(prepared["spec"])
    if not spec_path.is_file():
        raise ValueError(f"Spec not found: {spec_path}")

    with open(spec_path) as f:
        spec = json.load(f)

    # Identity: MUST come from spec.source_id_full (or source_id as fallback)
    source_id_full = spec.get("source_id_full") or spec.get("source_id")
    if not source_id_full:
        raise ValueError(f"Spec missing source_id_full and source_id: {spec_path}")

    # Map UUID to canonical key
    if source_id_full not in UUID_TO_KEY:
        raise ValueError(f"Unknown source UUID: {source_id_full} (expected one of {list(UUID_TO_KEY.keys())})")
    key = UUID_TO_KEY[source_id_full]

    # Validate explicit key if present in prepared
    if "key" in prepared and prepared["key"] != key:
        raise ValueError(f"Prepared key mismatch: {prepared['key']} vs UUID-mapped {key}")

    glb_path = Path(spec.get("geometry") or spec.get("overlay_glb", ""))
    if not glb_path.is_file():
        raise ValueError(f"GLB not found: {glb_path}")

    # Verify SHA256 matches - REQUIRED in both prepared and spec
    actual_sha256 = sha256_file(glb_path)
    prepared_sha = prepared.get("sha256")
    spec_sha = spec.get("sha256") or spec.get("overlay_glb_sha256")
    declared = [spec[k] for k in ("sha256", "overlay_glb_sha256") if spec.get(k)]
    if any(value != actual_sha256 for value in declared):
        raise ValueError("SHA256 mismatch in declared geometry hashes")

    if not prepared_sha:
        raise ValueError(f"Prepared missing sha256: {prepared_path}")
    if not spec_sha:
        raise ValueError(f"Spec missing sha256: {spec_path}")
    if actual_sha256 != prepared_sha:
        raise ValueError(f"SHA256 mismatch (prepared): expected {prepared_sha}, got {actual_sha256}")
    if actual_sha256 != spec_sha:
        raise ValueError(f"SHA256 mismatch (spec): expected {spec_sha}, got {actual_sha256}")

    # Geometry kind from key
    if key == "tower70":
        geometry_kind = "optional_additive_upper_infill"
        fractional_estimate = spec.get("upper_infill_frac")
    else:
        geometry_kind = "optional_per_balcony_panels"
        fractional_estimate = spec.get("fraction")

    # Fraction MUST be present and finite
    if fractional_estimate is None:
        raise ValueError(f"Spec missing required fractional field (upper_infill_frac or fraction): {spec_path}")
    if not isinstance(fractional_estimate, (int, float)) or not np.isfinite(fractional_estimate):
        raise ValueError(f"Fractional estimate not finite: {fractional_estimate}")

    return GeometryCandidate(
        key=key,
        prepared_path=prepared_path,
        spec_path=spec_path,
        glb_path=glb_path,
        sha256=actual_sha256,
        source_id_full=source_id_full,
        geometry_kind=geometry_kind,
        fractional_estimate=float(fractional_estimate)
    )


def export_bundle(candidates: List[GeometryCandidate], output_dir: Path, corridor_manifest: dict) -> dict:
    """Export the bundle to output_dir/assets/<sha>.glb and create portable manifest."""
    output_dir = Path(output_dir).resolve()

    # Validate all inputs BEFORE any writes
    if len(candidates) != 2:
        raise ValueError(f"Expected exactly 2 candidates, got {len(candidates)}")

    # Check for duplicate keys
    keys = [c.key for c in candidates]
    if len(set(keys)) != 2:
        raise ValueError(f"Duplicate candidate keys: {keys}")

    # Check for duplicate UUIDs
    uuids = [c.source_id_full for c in candidates]
    if len(set(uuids)) != 2:
        raise ValueError(f"Duplicate source UUIDs: {uuids}")

    # Validate origin_utm_m from corridor manifest
    origin_utm = corridor_manifest.get("origin_utm_m")
    if not origin_utm or not isinstance(origin_utm, list) or len(origin_utm) != 3:
        raise ValueError(f"corridor_manifest missing or invalid origin_utm_m (must be 3-element list): {origin_utm}")
    if not all(isinstance(v, (int, float)) and np.isfinite(v) for v in origin_utm):
        raise ValueError(f"origin_utm_m contains non-finite values: {origin_utm}")

    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Read horizontal CRS from corridor manifest or use default (EPSG:32636 - UTM zone 36N)
    horizontal_crs = corridor_manifest.get("horizontal_crs", "EPSG:32636")
    if horizontal_crs != "EPSG:32636":
        raise ValueError(f"Only EPSG:32636 is supported as horizontal CRS, got: {horizontal_crs}")

    # Extract UTM zone from CRS
    zone = int(horizontal_crs.replace("EPSG:326", ""))

    # Source code provenance
    source_provenance = {
        "repo_root": "src/dynacity",
        "crs_source": "src/dynacity/beirut001_pack.py (CRS=EPSG:32636)",
        "transform_source": "src/dynacity/engine_city_batches.py (Transformer 4326->32636)",
        "source_file_sha256": source_file_hashes()
    }

    bundle = {
        "schema": "dynacity-corridor-appearance-bundle/1",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "origin_utm_m": origin_utm,
        "horizontal_crs": horizontal_crs,
        "vertical_datum": "UNKNOWN model height (not surveyed MSL/WGS84 ellipsoid)",
        "alignment_status": "inferred_not_surveyed",
        "coordinate_system": {
            "units": "metres",
            "horizontal_crs": horizontal_crs,
            "origin_axis": "East, North, Up",
            "vertical_datum": "UNKNOWN model height (not surveyed MSL/WGS84 ellipsoid)",
            "alignment_status": "inferred_not_surveyed",
            "gltf_axis": "Y-up (X=East, Y=Up, Z=-North)",
            "local_enu_axis": "X=East, Y=North, Z=Up",
            "conversion_note": "Global origin UTM from source; axis/unit conversion should be applied exactly once at web/root import. Z = origin_utm_m[2] + local_enu_z is model height, NOT surveyed elevation.",
            "transform_recipe": "absoluteUTM = origin_utm_m + local_enu (east, north, up); webAnchorWGS84XY from pyproj always_xy; optional recenter GLB by center requires 1 transform only. Z_global = origin_utm_m[2] + local_enu_z is model Z, NOT surveyed MSL/WGS84 ellipsoid height. Never pretend map-native placement verified."
        },
        "source_code_provenance": source_provenance,
        "candidates": [],
        "source_references": [],
        "status": "ESTIMATED_OPTIONAL_NOT_SCENE_APPLIED",
        "notes": [
            "Both candidates are APPROVED_AS_OPTIONAL only; not accuracy-accepted.",
            "Geometry from iteration16 (tower70 upper infill) and iteration27 (granite91 parapet overlay).",
            "Source identity/height/placement from iteration3 corridor-manifest-v2.json; uncalibrated camera.",
            "Fractional estimates (0.7) are not measured from photography.",
            "GLB files exported as-is (no remesh, no UV generation, no material baking).",
            "Materials retained as GLB pbrMetallicRoughness factors; iteration29 procedural stone REJECTED.",
            "Full resemblance held/native checks scoped to iterations 16/27/28 only.",
            "No physical collision acceptance; native/web preview PENDING.",
            "Portable manifest uses relative paths; absolute paths only in private provenance handoff."
        ]
    }

    private_provenance = {
        "generated": datetime.utcnow().isoformat() + "Z",
        "candidates_detail": []
    }

    for cand in candidates:
        # Copy GLB to assets/<sha>.glb with atomic write
        dest_name = f"{cand.sha256}.glb"
        dest_path = assets_dir / dest_name

        if dest_path.exists():
            existing_sha = sha256_file(dest_path)
            if existing_sha != cand.sha256:
                raise ValueError(f"Output exists with different content: {dest_path}")
        else:
            tmp_path = assets_dir / f".tmp_{dest_name}"
            shutil.copy2(cand.glb_path, tmp_path)
            # Verify copy
            if sha256_file(tmp_path) != cand.sha256:
                tmp_path.unlink()
                raise ValueError(f"Atomic copy verification failed for {dest_name}")
            tmp_path.rename(dest_path)

        # Compute bounds from actual GLB binary data with transforms
        min_yup, max_yup = compute_transformed_bounds(cand.glb_path)
        enu_min, enu_max = glb_yup_to_local_enu(min_yup, max_yup)

        # Read spec for additional metadata
        with open(cand.spec_path) as f:
            spec = json.load(f)

        # Source photo date - ONLY from photo_metadata.date if present
        photo_date = spec.get("photo_metadata", {}).get("date") if "photo_metadata" in spec else None
        if photo_date is None:
            photo_date = "unknown"

        # License info - ONLY from photo_metadata.license if present
        license_info = spec.get("photo_metadata", {}).get("license") if "photo_metadata" in spec else None
        if license_info is None:
            license_info = "unknown"

        # Source URI - ONLY from photo_metadata.source_photo_url if present
        source_uri = spec.get("photo_metadata", {}).get("source_photo_url") if "photo_metadata" in spec else None
        if source_uri is None:
            source_uri = "unknown"

        # Portable geometry_manifest_reference: short opaque identifier + sha
        # Format: "<key>-<sha256[:16]>"
        geom_ref = f"{cand.key}-{cand.sha256[:16]}"

        # Compute geographic anchor for this candidate
        geographic_anchor = compute_geographic_anchor(origin_utm, enu_min, enu_max)

        candidate_entry = {
            "key": cand.key,
            "geometry_kind": cand.geometry_kind,
            "fractional_estimate": cand.fractional_estimate,
            "source_building_uuid": cand.source_id_full,
            "source_coordinate_units": "metres",
            "gltf_bounds_yup": {
                "min": min_yup,
                "max": max_yup
            },
            "local_enu_bounds": {
                "min": enu_min,
                "max": enu_max
            },
            "geographic_anchor": geographic_anchor,
            "glb_asset": {
                "path": f"assets/{dest_name}",
                "sha256": cand.sha256,
                "size_bytes": cand.glb_path.stat().st_size
            },
            "material_parameters": extract_material_parameters(cand.glb_path),
            "source_photo_date": photo_date,
            "source_license": license_info,
            "source_uri": source_uri,
            "geometry_manifest_reference": geom_ref,
            "limitations": spec.get("limits", spec.get("limitations", [])),
            "status": "ESTIMATED_OPTIONAL_NOT_SCENE_APPLIED"
        }
        bundle["candidates"].append(candidate_entry)

        private_provenance["candidates_detail"].append({
            "key": cand.key,
            "prepared_json": str(cand.prepared_path),
            "spec_json": str(cand.spec_path),
            "source_glb": str(cand.glb_path),
            "output_glb": str(dest_path),
            "sha256_verified": True
        })

    # Source references summary
    bundle["source_references"] = [
        {
            "source_id_full": c.source_id_full,
            "geometry_kind": c.geometry_kind,
            "fractional_estimate": c.fractional_estimate
        }
        for c in candidates
    ]

    # Write portable manifest
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        raise ValueError(f"Manifest already exists: {manifest_path} (refuse overwrite)")
    with open(manifest_path, 'w') as f:
        json.dump(bundle, f, indent=2)

    # Write private provenance handoff (absolute paths allowed here)
    provenance_path = output_dir / "private-provenance.json"
    if provenance_path.exists():
        raise ValueError(f"Provenance already exists: {provenance_path}")
    with open(provenance_path, 'w') as f:
        json.dump(private_provenance, f, indent=2)

    return bundle


def extract_material_parameters(glb_path: Path) -> List[dict]:
    """Extract pbrMetallicRoughness factors from GLB materials."""
    gltf = parse_glb_json_chunk(glb_path)
    materials = []
    for mat in gltf.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness", {})
        materials.append({
            "name": mat.get("name", "unnamed"),
            "doubleSided": mat.get("doubleSided", False),
            "baseColorFactor": pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0]),
            "metallicFactor": pbr.get("metallicFactor", 0.0),
            "roughnessFactor": pbr.get("roughnessFactor", 1.0)
        })
    return materials


def main():
    parser = argparse.ArgumentParser(description="Export corridor appearance bundle with georeferencing")
    parser.add_argument("--tower70-prepared", required=True, help="Path to tower70 prepared.json")
    parser.add_argument("--granite91-prepared", required=True, help="Path to granite91 prepared.json")
    parser.add_argument("--corridor-manifest", required=True, help="Path to iteration3 corridor-manifest-v2.json")
    parser.add_argument("--output", required=True, help="Output directory for bundle")
    parser.add_argument("--horizontal-crs", default="EPSG:32636", help="Horizontal CRS (only EPSG:32636 supported)")
    args = parser.parse_args()

    # Validate CRS
    if args.horizontal_crs != "EPSG:32636":
        print(f"ERROR: Only EPSG:32636 is supported as horizontal CRS, got: {args.horizontal_crs}", file=sys.stderr)
        sys.exit(1)

    # Read candidates
    tower70 = read_candidate(Path(args.tower70_prepared))
    granite91 = read_candidate(Path(args.granite91_prepared))

    # Read corridor manifest for origin_utm
    with open(args.corridor_manifest) as f:
        corridor_manifest = json.load(f)

    # Add horizontal_crs to manifest if not present
    if "horizontal_crs" not in corridor_manifest:
        corridor_manifest["horizontal_crs"] = args.horizontal_crs

    # Export
    output_dir = Path(args.output).resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        print(f"ERROR: Output directory not empty: {output_dir}", file=sys.stderr)
        sys.exit(1)

    bundle = export_bundle([tower70, granite91], output_dir, corridor_manifest)

    print(f"Bundle exported to: {output_dir}")
    print(f"Manifest: {output_dir / 'manifest.json'}")
    print(f"Assets: {list((output_dir / 'assets').glob('*.glb'))}")
    print(f"Status: {bundle['status']}")
    for c in bundle["candidates"]:
        anchor = c["geographic_anchor"]
        print(f"  {c['key']}: anchor_utm={anchor['anchor_utm_m']}, anchor_wgs84={anchor['anchor_wgs84_deg']}")


if __name__ == "__main__":
    main()
