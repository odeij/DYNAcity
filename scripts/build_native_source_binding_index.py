#!/usr/bin/env python3
"""
Worker-owned scalable source binding index.
Builds reusable OFFLINE index for ALL source building records across city-batches,
matched to native inventory actor batch labels/mesh paths, preserving many-to-one ambiguity.

Usage:
    python build_native_source_binding_index.py --output binding_index.json
    python build_native_source_binding_index.py --output binding_index.json --query-box 2514 1428 0 2614 1530 80
"""

import argparse
import hashlib
import json
import os
import math
import sys
from pathlib import Path
from typing import Any


# ----------------------------------------------------------------------
# Configuration - paths relative to this script
# ----------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

CITY_BATCHES_JSON = Path("C:/private/dynacity-data/engine/city-batches/city-batches.json")
NATIVE_INVENTORY_JSON = Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration18-residual/native-inventory.json")

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def compute_file_hash(path: Path) -> str:
    """SHA256 hash of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_bounds(bounds: list) -> tuple:
    """
    Validate and normalize bounds [[min_x, min_y, min_z], [max_x, max_y, max_z]].
    Returns (min_vec, max_vec) as tuples of floats.
    Raises ValueError if invalid.
    """
    if not bounds or len(bounds) != 2:
        raise ValueError(f"Bounds must be [[min_x,min_y,min_z],[max_x,max_y,max_z]], got {bounds}")
    min_v = bounds[0]
    max_v = bounds[1]
    if len(min_v) != 3 or len(max_v) != 3:
        raise ValueError(f"Bounds vectors must have 3 components, got {min_v}, {max_v}")
    min_v = [float(v) for v in min_v]
    max_v = [float(v) for v in max_v]
    if not all(math.isfinite(v) for v in min_v + max_v):
        raise ValueError('Bounds must contain finite coordinates')
    for i in range(3):
        if min_v[i] > max_v[i]:
            raise ValueError(f"Invalid bounds: min[{i}]={min_v[i]} > max[{i}]={max_v[i]}")
    return (tuple(min_v), tuple(max_v))


def bounds_intersect(b1_min, b1_max, b2_min, b2_max) -> bool:
    """Check if two AABBs intersect (inclusive)."""
    for i in range(3):
        if b1_max[i] < b2_min[i] or b2_max[i] < b1_min[i]:
            return False
    return True


def height_above_base(local_bounds_m: list) -> float:
    """
    Compute height above base Z from local_bounds_m.
    Distinct from local elevation (which is absolute Z).
    """
    min_v, max_v = validate_bounds(local_bounds_m)
    return max_v[2] - min_v[2]


def base_z(local_bounds_m: list) -> float:
    """Return base Z (minimum Z) from local_bounds_m."""
    min_v, _ = validate_bounds(local_bounds_m)
    return min_v[2]


def top_z(local_bounds_m: list) -> float:
    """Return top Z (maximum Z) from local_bounds_m."""
    _, max_v = validate_bounds(local_bounds_m)
    return max_v[2]


def parse_query_box(query_str: str) -> tuple:
    """Parse --query-box as 6 floats: min_x min_y min_z max_x max_y max_z (local metres)."""
    parts = query_str.split()
    if len(parts) != 6:
        raise ValueError("--query-box requires exactly 6 floats: min_x min_y min_z max_x max_y max_z")
    vals = [float(p) for p in parts]
    return ((vals[0], vals[1], vals[2]), (vals[3], vals[4], vals[5]))


# ----------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------


def load_city_batches() -> dict:
    """Load city-batches.json manifest."""
    with open(CITY_BATCHES_JSON) as f:
        return json.load(f)


def load_objects_index(job: dict) -> dict:
    """Load a single .objects.json file for a job."""
    meta_path = job["metadata"]
    if not os.path.exists(meta_path):
        raise FileNotFoundError(f"Objects index not found: {meta_path}")
    with open(meta_path) as f:
        return json.load(f)


def load_native_inventory() -> dict:
    """Load native-inventory.json from Unreal."""
    with open(NATIVE_INVENTORY_JSON) as f:
        return json.load(f)


# ----------------------------------------------------------------------
# Source record extraction
# ----------------------------------------------------------------------


def extract_source_records(city_batches: dict) -> list:
    """
    Extract ALL source building records from all city-batch object indexes.
    Returns list of source records with batch/material/triangle_range/bounds/height/basez.
    """
    all_sources = []
    seen_source_ids = {}  # track duplicates: source_id -> list of record indices

    for job in city_batches["jobs"]:
        kind = job["kind"]  # "buildings" or "roads"
        if kind != 'buildings':
            continue
        name = job["name"]
        production_group = job["production_group"]

        try:
            objects_data = load_objects_index(job)
        except FileNotFoundError as e:
            print(f"WARNING: {e}", file=sys.stderr)
            continue

        # batch name from objects file (e.g., "buildings_achrafieh_material_5")
        batch_prefix = objects_data.get("asset", "").split("\\")[-1].replace(".glb", "")

        for obj in objects_data.get("objects", []):
            source_id = obj.get("source_id", "")
            if not source_id:
                continue

            # Track duplicates
            if source_id in seen_source_ids:
                seen_source_ids[source_id].append(len(all_sources))
            else:
                seen_source_ids[source_id] = [len(all_sources)]

            local_bounds = obj.get("local_bounds_m", [[0,0,0],[0,0,0]])
            min_v, max_v = validate_bounds(local_bounds)

            meta = obj.get("metadata", {})

            record = {
                "source_id": source_id,
                "batch": obj.get("batch", ""),
                "material": obj.get("material", 0),
                "triangle_range": {
                    "first_triangle": obj.get("first_triangle", 0),
                    "triangle_count": obj.get("triangle_count", 0)
                },
                "vertex_range": {
                    "first_vertex": obj.get("first_vertex", 0),
                    "vertex_count": obj.get("vertex_count", 0)
                },
                "local_bounds_m": {
                    "min": list(min_v),
                    "max": list(max_v)
                },
                "height_above_base": height_above_base(local_bounds),
                "base_z": base_z(local_bounds),
                "top_z": top_z(local_bounds),
                "production_group": production_group,
                "kind": kind,
                "batch_job_name": name,
                "metadata": meta,
                "native_actor_candidates": [],  # to be filled later
                "duplicate_flag": False  # will be set after all records collected
            }
            all_sources.append(record)

    # Mark duplicates
    for source_id, indices in seen_source_ids.items():
        if len(indices) > 1:
            for idx in indices:
                all_sources[idx]["duplicate_flag"] = True
                all_sources[idx]["duplicate_count"] = len(indices)
                all_sources[idx]["duplicate_indices"] = indices

    print(f"Loaded {len(all_sources)} source records from {len(city_batches['jobs'])} jobs")
    dup_count = sum(1 for r in all_sources if r["duplicate_flag"])
    if dup_count:
        print(f"WARNING: {dup_count} records have duplicate source_ids (preserved, flagged)")

    return all_sources


# ----------------------------------------------------------------------
# Native actor candidate matching
# ----------------------------------------------------------------------


def extract_batch_label_from_mesh_path(mesh_path: str) -> str:
    """
    Extract batch label from native actor mesh_path.
    e.g., /Game/DynaCity/CorridorReconstruction/Geometry/.../buildings_remeil_material_5/...
    """
    # Look for pattern like buildings_*_material_* or roads_*_material_*
    parts = mesh_path.split("/")
    for part in parts:
        if part.startswith("buildings_") and "_material_" in part:
            return part
        if part.startswith("roads_") and "_material_" in part:
            return part
    return ""


def match_native_candidates(source_records: list, native_inventory: dict) -> None:
    """
    Match native actors to source records by batch label.
    Preserves many-to-one ambiguity (multiple sources -> same batch -> same actors).
    Updates source_records in-place with native_actor_candidates.
    """
    # Build actor index by batch label
    actors_by_batch = {}
    for actor in native_inventory.get("actors", []):
        mesh_path = actor.get("mesh_path", "")
        batch_label = extract_batch_label_from_mesh_path(mesh_path)
        label = actor.get('label', '').removeprefix('Corridor visual - ')
        if label.startswith('buildings_') and '_material_' in label:
            batch_label = label
        if batch_label:
            actors_by_batch.setdefault(batch_label, []).append(actor)

    # Match each source record
    for src in source_records:
        batch = src["batch"]
        candidates = actors_by_batch.get(batch, [])

        src["native_actor_candidates"] = [
            {
                "actor_path": a.get("actor_path", ""),
                "label": a.get("label", ""),
                "mesh_path": a.get("mesh_path", ""),
                "world_bounds_local_m": a.get("world_bounds_local_m", {}),
                "material_slots": [
                    {
                        "slot_index": ms.get("slot_index", 0),
                        "slot_name": ms.get("slot_name", ""),
                        "material_path": ms.get("material_path", "")
                    }
                    for ms in a.get("material_slots", [])
                ],
                "tags": a.get("tags", []),
                "intersects_search_volume": a.get("intersects_search_volume", False)
            }
            for a in candidates
        ]


# ----------------------------------------------------------------------
# Query filtering
# ----------------------------------------------------------------------


def filter_by_query_box(source_records: list, query_min: tuple, query_max: tuple) -> list:
    """Filter source records whose local_bounds intersect query box."""
    filtered = []
    for src in source_records:
        b = src["local_bounds_m"]
        if bounds_intersect(b["min"], b["max"], query_min, query_max):
            filtered.append(src)
    return filtered


# ----------------------------------------------------------------------
# Index building
# ----------------------------------------------------------------------


def build_index(city_batches: dict, native_inventory: dict, query_box: tuple = None) -> dict:
    """Build the complete binding index in one pass."""

    # Input hashes
    city_batches_hash = compute_file_hash(CITY_BATCHES_JSON)
    native_inventory_hash = compute_file_hash(NATIVE_INVENTORY_JSON)

    # Extract all source records
    source_records = extract_source_records(city_batches)

    # Match native candidates
    match_native_candidates(source_records, native_inventory)

    # Apply query box filter if provided
    if query_box:
        query_min, query_max = query_box
        source_records = filter_by_query_box(source_records, query_min, query_max)
        print(f"Query box filter: {len(source_records)} records match")

    # Build index
    index = {
        "schema": "dynacity-native-source-binding-index/1",
        "input_hashes": {
            "city_batches_json": city_batches_hash,
            "native_inventory_json": native_inventory_hash
        },
        "index_counts": {
            "total_source_records": len(source_records),
            "unique_source_ids": len(set(r["source_id"] for r in source_records)),
            "duplicate_source_ids": sum(1 for r in source_records if r.get("duplicate_flag", False)),
            "records_with_native_candidates": sum(1 for r in source_records if r["native_actor_candidates"]),
            "total_native_actors_in_inventory": len(native_inventory.get("actors", []))
        },
        "sources": source_records
    }

    return index


# ----------------------------------------------------------------------
# Atomic write + readback verification
# ----------------------------------------------------------------------


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + rename, then read back and verify."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp_path, path)

    # Read back and verify
    with open(path) as f:
        verify = json.load(f)
    if verify != data:
        raise RuntimeError("Atomic write verification failed: readback differs from written data")


# ----------------------------------------------------------------------
# Dry-run assertion for LGB query
# ----------------------------------------------------------------------


def dry_run_assertion(index: dict) -> dict:
    """
    Run assertion for known LGB query:
    source height 62.6775, base 14.8013, top 77.4789
    source_id: beirut001:d31eb718-f947-49d5-b82d-14838045a9c7
    """
    lgb_source_id = "beirut001:d31eb718-f947-49d5-b82d-14838045a9c7"

    # Find in index
    found = None
    for src in index["sources"]:
        if src["source_id"] == lgb_source_id:
            found = src
            break

    if not found:
        return {'assertion': 'NOT_IN_QUERY', 'lgb_source_id': lgb_source_id,
                'note': 'Query excludes LGB; no LGB geometry assertion made for this output'}

    # Assertions
    expected_height = 62.6775
    expected_base = 14.8013
    expected_top = 77.4789
    tolerance = 0.001

    actual_height = found["height_above_base"]
    actual_base = found["base_z"]
    actual_top = found["top_z"]
    candidate_count = len(found["native_actor_candidates"])

    assert abs(actual_height - expected_height) < tolerance, \
        f"Height mismatch: expected {expected_height}, got {actual_height}"
    assert abs(actual_base - expected_base) < tolerance, \
        f"Base Z mismatch: expected {expected_base}, got {actual_base}"
    assert abs(actual_top - expected_top) < tolerance, \
        f"Top Z mismatch: expected {expected_top}, got {actual_top}"

    print(f"DRY-RUN ASSERTION PASSED:")
    print(f"  source_id: {lgb_source_id}")
    print(f"  height_above_base: {actual_height:.4f} (expected {expected_height})")
    print(f"  base_z: {actual_base:.4f} (expected {expected_base})")
    print(f"  top_z: {actual_top:.4f} (expected {expected_top})")
    print(f"  native_actor_candidates: {candidate_count}")

    return {
        "lgb_source_id": lgb_source_id,
        "height_above_base": actual_height,
        "base_z": actual_base,
        "top_z": actual_top,
        "candidate_count": candidate_count,
        "assertion": "PASSED"
    }


# ----------------------------------------------------------------------
# Main CLI
# ----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Build scalable native source binding index (offline, single pass)"
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output JSON file path for binding index"
    )
    parser.add_argument(
        "--query-box", "-q",
        help="Optional query box: 6 floats min_x min_y min_z max_x max_y max_z (local metres)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run LGB assertion only, do not write output"
    )

    args = parser.parse_args()

    # Load inputs
    print("Loading city-batches.json...")
    city_batches = load_city_batches()

    print("Loading native-inventory.json...")
    native_inventory = load_native_inventory()

    # Parse query box
    query_box = None
    if args.query_box:
        query_box = parse_query_box(args.query_box)
        print(f"Query box: {query_box}")

    # Build index
    print("Building binding index...")
    index = build_index(city_batches, native_inventory, query_box)

    # Dry-run assertion
    print("Running LGB dry-run assertion...")
    dry_run_result = dry_run_assertion(index)
    index["dry_run_assertion"] = dry_run_result

    if args.dry_run:
        print("Dry-run complete. Output not written.")
        return 0

    # Write output atomically
    output_path = Path(args.output)
    print(f"Writing index to {output_path}...")
    atomic_write_json(output_path, index)

    # Print summary
    counts = index["index_counts"]
    print(f"\nIndex written successfully:")
    print(f"  Total source records: {counts['total_source_records']}")
    print(f"  Unique source IDs: {counts['unique_source_ids']}")
    print(f"  Duplicate source IDs: {counts['duplicate_source_ids']}")
    print(f"  Records with native candidates: {counts['records_with_native_candidates']}")
    print(f"  LGB assertion: {dry_run_result['assertion']}")
    print(f"  LGB candidate count: {dry_run_result.get('candidate_count', 'outside query')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
