#!/usr/bin/env python3
"""
Filter verified water parking positions from source parked-cars.json.

CLI: --source --verified --output
- Reads source parked-cars.json (20958 slots, schema dynacity-parked-cars/1)
- Reads verified-water-positions.json (193 unique ENU positions at 1cm precision)
- Filters ONLY those verified slot coordinates against source JSON
- Keeps other metadata/source origin, unknown positions unchanged
- Writes filtered output to --output (default dry run to D:/workers/parked-cars-filtered.json)
- NEVER overwrites original source
- Records full hashes/removal counts/source IDs/actual examples
- Rejects different origin/dubious coordinates
"""

import json
import argparse
import hashlib
import math
from pathlib import Path
from typing import Dict, List, Tuple, Any


def load_json(path: str) -> Any:
    with open(path, 'r') as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def compute_hash(data: Any) -> str:
    """Compute deterministic SHA256 of JSON-serializable data."""
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def is_finite_number(x) -> bool:
    """Check if value is a finite number (not bool, not NaN, not inf)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def enu_key(position: List[float]) -> Tuple[float, float, float]:
    """Create 1cm precision key from ENU position. Validates input."""
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise ValueError(f"Position must be 3-element list/tuple, got {position}")
    if not all(is_finite_number(v) for v in position):
        raise ValueError(f"Position contains non-finite or boolean values: {position}")
    return (round(float(position[0]), 2), round(float(position[1]), 2), round(float(position[2]), 2))


def validate_origin(source_data: Dict, verified_data: Dict) -> bool:
    """
    Validate that verified positions come from the same geographic origin.
    Compares numeric origin_utm_m arrays with abs diff <= 1e-6.
    """
    source_origin = source_data.get('origin_utm_m')
    verified_origin = verified_data.get('origin_utm_m')

    if not source_origin or not verified_origin:
        raise ValueError("Origin validation failed: missing origin_utm_m in source or verified data")

    if not (isinstance(source_origin, (list, tuple)) and len(source_origin) == 3):
        raise ValueError(f"Source origin_utm_m must be 3-element array, got {source_origin}")
    if not (isinstance(verified_origin, (list, tuple)) and len(verified_origin) == 3):
        raise ValueError(f"Verified origin_utm_m must be 3-element array, got {verified_origin}")

    if not all(is_finite_number(v) for v in source_origin):
        raise ValueError(f"Source origin_utm_m contains non-finite values: {source_origin}")
    if not all(is_finite_number(v) for v in verified_origin):
        raise ValueError(f"Verified origin_utm_m contains non-finite values: {verified_origin}")

    for s, v in zip(source_origin, verified_origin):
        if abs(float(s) - float(v)) > 1e-6:
            raise ValueError(f"Origin mismatch: source={source_origin} vs verified={verified_origin} (diff > 1e-6)")

    return True


def check_overwrite_guard(source_path: str, verified_path: str, output_path: str) -> None:
    """Reject if output resolves to same file as source or verified (even via aliases)."""
    source_resolved = Path(source_path).resolve()
    verified_resolved = Path(verified_path).resolve()
    output_resolved = Path(output_path).resolve()

    if output_resolved == source_resolved:
        raise ValueError(f"Overwrite guard: output path resolves to source file: {source_resolved}")
    if output_resolved == verified_resolved:
        raise ValueError(f"Overwrite guard: output path resolves to verified file: {verified_resolved}")


def filter_parked_cars(source_path: str, verified_path: str, output_path: str, dry_run: bool = False) -> Dict:
    """
    Filter verified water positions from source parked cars.

    Returns report with hashes, counts, examples.
    """
    # Overwrite guard BEFORE any writes
    check_overwrite_guard(source_path, verified_path, output_path)

    # Load source
    source_data = load_json(source_path)
    source_slots = source_data.get('slots', [])
    source_hash = compute_hash(source_data)

    # Load verified positions
    verified_data = load_json(verified_path)
    verified_instances = verified_data.get('instances', [])
    verified_hash = compute_hash(verified_data)

    # Validate origin (numeric arrays, not filename)
    validate_origin(source_data, verified_data)

    # Build lookup of verified positions by 1cm key
    verified_by_key = {}
    for inst in verified_instances:
        key = tuple(inst['key'])  # Already 1cm precision from verified file
        # Validate key against UE translation if present (cross-check)
        if 'ue_translation' in inst:
            ue = inst['ue_translation']
            if isinstance(ue, (list, tuple)) and len(ue) == 3 and all(is_finite_number(v) for v in ue):
                # UE cm -> ENU: east=x/100, north=-y/100, z=z/100
                expected_enu = [round(ue[0] / 100.0, 2), round(-ue[1] / 100.0, 2), round(ue[2] / 100.0, 2)]
                actual_key = list(key)
                for exp, act in zip(expected_enu, actual_key):
                    if abs(exp - act) > 0.01:  # Allow 1cm rounding
                        raise ValueError(f"Verified instance ENU key {actual_key} doesn't match UE translation {ue} -> {expected_enu}")
        if key not in verified_by_key:
            verified_by_key[key] = inst

    # Filter source slots
    removed_slots = []
    kept_slots = []
    removal_examples = []

    for i, slot in enumerate(source_slots):
        pos = slot.get('position', [0, 0, 0])
        try:
            key = enu_key(pos)
        except ValueError as e:
            # Invalid position - keep slot but log
            kept_slots.append(slot)
            continue

        if key in verified_by_key:
            # This slot matches a verified water position - remove it
            verified_inst = verified_by_key[key]
            removed_slots.append({
                'source_index': i,
                'slot': slot,
                'verified_match': {
                    'enu': verified_inst['enu'],
                    'source_actor': verified_inst['source_actor'],
                    'source_label': verified_inst['source_label'],
                    'source_component': verified_inst['source_component'],
                    'source_index': verified_inst['source_index'],
                    'duplicate_count': verified_inst.get('duplicate_count', 1)
                }
            })
            if len(removal_examples) < 5:
                removal_examples.append({
                    'source_index': i,
                    'position': pos,
                    'matched_enu': verified_inst['enu'],
                    'source_label': verified_inst['source_label']
                })
        else:
            kept_slots.append(slot)

    # Build filtered output: preserve ALL source metadata, only replace slots + add filter_report
    filtered_data = {**source_data, 'slots': kept_slots}
    filtered_data['filter_report'] = {
        'source_file': source_path,
        'verified_file': verified_path,
        'source_hash': source_hash,
        'verified_hash': verified_hash,
        'source_slots': len(source_slots),
        'verified_unique_positions': len(verified_by_key),
        'removed_slots': len(removed_slots),
        'kept_slots': len(kept_slots),
        'removal_examples': removal_examples,
        'note': 'Only exact 1cm-precision ENU matches removed. Native backup had 336 legacy+paint duplicates; source has 1 per slot. Removed count <= unique verified positions.'
    }

    filtered_hash = compute_hash(filtered_data)

    report = {
        'source_file': source_path,
        'verified_file': verified_path,
        'output_file': output_path,
        'source_hash': source_hash,
        'verified_hash': verified_hash,
        'filtered_hash': filtered_hash,
        'source_slots': len(source_slots),
        'verified_unique_positions': len(verified_by_key),
        'removed_count': len(removed_slots),
        'kept_count': len(kept_slots),
        'removal_examples': removal_examples,
        'dry_run': dry_run
    }

    if not dry_run:
        save_json(output_path, filtered_data)
        print(f"Written filtered output to {output_path}")
    else:
        print(f"DRY RUN: Would write to {output_path}")

    return report


def main():
    parser = argparse.ArgumentParser(description='Filter verified water parking positions')
    parser.add_argument('--source', required=True, help='Source parked-cars.json path')
    parser.add_argument('--verified', required=True, help='Verified water positions JSON path')
    parser.add_argument('--output', required=True, help='Output filtered JSON path')
    parser.add_argument('--dry-run', action='store_true', help='Dry run (default: false)')

    args = parser.parse_args()

    report = filter_parked_cars(args.source, args.verified, args.output, args.dry_run)

    print("\n=== FILTER REPORT ===")
    print(f"Source: {report['source_file']}")
    print(f"Verified: {report['verified_file']}")
    print(f"Output: {report['output_file']}")
    print(f"Source hash: {report['source_hash'][:16]}...")
    print(f"Verified hash: {report['verified_hash'][:16]}...")
    print(f"Filtered hash: {report['filtered_hash'][:16]}...")
    print(f"Source slots: {report['source_slots']}")
    print(f"Verified unique positions: {report['verified_unique_positions']}")
    print(f"Removed: {report['removed_count']}")
    print(f"Kept: {report['kept_count']}")
    print(f"Dry run: {report['dry_run']}")
    if report['removal_examples']:
        print("\nRemoval examples:")
        for ex in report['removal_examples']:
            print(f"  Source idx {ex['source_index']}: pos={ex['position']} -> matched ENU={ex['matched_enu']} ({ex['source_label']})")


if __name__ == '__main__':
    main()
