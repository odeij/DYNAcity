#!/usr/bin/env python3
"""
Regression tests for verified water parking filter.

Tests:
- Axis/unit conversion (UE cm -> ENU meters)
- Duplicate deduplication at 1cm precision
- Zero wrong removals (only exact matches removed)
- Origin validation (rejects different origin/dubious coordinates)
- Non-matching positions preserved
- Counts/hashes verification
"""

import json
import tempfile
import os
import sys
import subprocess
from pathlib import Path

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from filter_verified_water_parking import (
    enu_key, compute_hash, validate_origin, filter_parked_cars, check_overwrite_guard
)


def test_enu_key_conversion():
    """Test UE cm to ENU meters conversion with 1cm precision key."""
    # UE coordinates: x=105317.049, y=-182131.218, z=-2799.355
    # ENU: east=x/100=1053.17, north=-y/100=1821.31, up=z/100=-27.99
    position = [1053.1704911751281, 1821.3121858996095, -27.993554166193453]
    key = enu_key(position)
    assert key == (1053.17, 1821.31, -27.99), f"Expected (1053.17, 1821.31, -27.99), got {key}"

    # Test rounding at 1cm precision
    position2 = [1053.174, 1821.315, -27.994]
    key2 = enu_key(position2)
    assert key2 == (1053.17, 1821.32, -27.99), f"Rounding failed: {key2}"

    print("PASS: enu_key conversion test passed")


def test_duplicate_deduplication():
    """Test that 1cm precision keys deduplicate correctly."""
    positions = [
        [1000.00, 2000.00, -50.00],
        [1000.004, 2000.004, -50.004],  # Same at 1cm
        [1000.01, 2000.01, -50.01],  # Different at 1cm
    ]
    keys = [enu_key(p) for p in positions]
    assert keys[0] == keys[1], "Should deduplicate at 1cm precision"
    assert keys[0] != keys[2], "Should differ at 1cm precision"
    print("PASS: Duplicate deduplication test passed")


def test_compute_hash_deterministic():
    """Test hash computation is deterministic."""
    data = {"a": 1, "b": [2, 3], "c": {"d": 4}}
    h1 = compute_hash(data)
    h2 = compute_hash(data)
    assert h1 == h2, "Hash not deterministic"
    assert len(h1) == 64, "SHA256 should be 64 hex chars"
    print("PASS: Deterministic hash test passed")


def test_origin_validation():
    """Test origin validation rejects different origins (numeric arrays only)."""
    source_data = {
        'origin_utm_m': [730784.7582802251, 3751551.1589951646, 51.10578950470706],
        'slots': []
    }
    verified_data = {
        'origin_utm_m': [730784.7582802251, 3751551.1589951646, 51.10578950470706],
        'instances': []
    }
    assert validate_origin(source_data, verified_data) == True

    # Different origin (exceeds 1e-6)
    verified_data_diff = {
        'origin_utm_m': [730784.758, 3751551.158, 51.105],  # Truncated, diff > 1e-6
        'instances': []
    }
    try:
        validate_origin(source_data, verified_data_diff)
        assert False, "Should have raised ValueError for origin mismatch"
    except ValueError as e:
        assert "Origin mismatch" in str(e)

    # Missing origin in source
    source_no_origin = {'slots': []}
    try:
        validate_origin(source_no_origin, verified_data)
        assert False, "Should have raised ValueError for missing source origin"
    except ValueError as e:
        assert "missing origin_utm_m" in str(e)

    # Missing origin in verified
    verified_no_origin = {'instances': []}
    try:
        validate_origin(source_data, verified_no_origin)
        assert False, "Should have raised ValueError for missing verified origin"
    except ValueError as e:
        assert "missing origin_utm_m" in str(e)

    # Non-finite values
    source_bad = {'origin_utm_m': [float('nan'), 0, 0], 'slots': []}
    try:
        validate_origin(source_bad, verified_data)
        assert False, "Should have raised ValueError for non-finite origin"
    except ValueError as e:
        assert "non-finite" in str(e)

    # Wrong length
    source_bad_len = {'origin_utm_m': [1, 2], 'slots': []}
    try:
        validate_origin(source_bad_len, verified_data)
        assert False, "Should have raised ValueError for wrong origin length"
    except ValueError as e:
        assert "3-element array" in str(e)

    print("PASS: Origin validation test passed")


def test_filter_integration():
    """Integration test with real data files."""
    # Use the immutable pre-filter fixture: the live source is now filtered.
    source_path = 'D:/private/dynacity-data/engine/driving-water-speed-20261105/parked-cars-before-source-filter.json'
    verified_path = 'D:/private/dynacity-data/engine/driving-water-speed-20261105/workers/verified-water-positions.json'

    # Skip if files don't exist
    if not os.path.exists(source_path) or not os.path.exists(verified_path):
        print("⚠ Skipping integration test - data files not found")
        return

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        output_path = f.name

    try:
        report = filter_parked_cars(source_path, verified_path, output_path, dry_run=False)

        # Verify counts
        assert report['source_slots'] == 20958, f"Expected 20958 source slots, got {report['source_slots']}"
        assert report['verified_unique_positions'] == 193, f"Expected 193 verified, got {report['verified_unique_positions']}"
        assert report['removed_count'] == 143
        assert report['kept_count'] == 20815
        assert report['kept_count'] + report['removed_count'] == report['source_slots'], "Counts don't add up"

        # Read back output and verify
        with open(output_path, 'r') as f:
            filtered = json.load(f)

        assert filtered['schema'] == 'dynacity-parked-cars/1'
        assert len(filtered['slots']) == report['kept_count']
        assert 'filter_report' in filtered
        assert filtered['filter_report']['removed_slots'] == report['removed_count']

        # Verify hashes
        assert report['source_hash'] == compute_hash(json.load(open(source_path)))
        assert report['verified_hash'] == compute_hash(json.load(open(verified_path)))
        assert report['filtered_hash'] == compute_hash(filtered)

        print(f"PASS: Integration test passed: removed={report['removed_count']}, kept={report['kept_count']}")

    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)


def test_non_matching_preserved():
    """Test that non-matching positions are preserved unchanged."""
    # Create minimal test data
    source_data = {
        'schema': 'dynacity-parked-cars/1',
        'seed': 42,
        'fill': 'test',
        'slots': [
            {'position': [1000.0, 2000.0, -50.0], 'heading_degrees': 0, 'model': 'bmw', 'road_id': 1},
            {'position': [1001.0, 2001.0, -50.0], 'heading_degrees': 90, 'model': 'toyota', 'road_id': 2},
            {'position': [1002.0, 2002.0, -50.0], 'heading_degrees': 180, 'model': 'van', 'road_id': 3},
        ],
        'note': 'test',
        'origin_utm_m': [0, 0, 0]
    }

    verified_data = {
        'schema': 'dynacity-verified-water-positions/1',
        'source_backup': 'D:/private/dynacity-data/engine/driving-water-speed-20261105/parked-before-water-filter.json',
        'origin_utm_m': [0, 0, 0],
        'instances': [
            {
                'enu': [1000.0, 2000.0, -50.0],
                'key': [1000.0, 2000.0, -50.0],
                'source_actor': 'actor1',
                'source_label': 'Parked_bmw',
                'source_component': 'MappedInstances',
                'source_index': 0,
                'ue_translation': [100000, -200000, -5000],
                'rotation': 'quat',
                'scale': [1, 1, 1],
                'duplicate_count': 1
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        source_path = f.name
        json.dump(source_data, f)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        verified_path = f.name
        json.dump(verified_data, f)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        output_path = f.name

    try:
        report = filter_parked_cars(source_path, verified_path, output_path, dry_run=False)

        assert report['source_slots'] == 3
        assert report['removed_count'] == 1
        assert report['kept_count'] == 2

        with open(output_path, 'r') as f:
            filtered = json.load(f)

        # Check preserved slots unchanged
        kept_positions = [s['position'] for s in filtered['slots']]
        assert [1001.0, 2001.0, -50.0] in kept_positions
        assert [1002.0, 2002.0, -50.0] in kept_positions
        assert [1000.0, 2000.0, -50.0] not in kept_positions

        # Check metadata preserved
        for slot in filtered['slots']:
            assert 'heading_degrees' in slot
            assert 'model' in slot
            assert 'road_id' in slot

        print("PASS: Non-matching preservation test passed")

    finally:
        for p in [source_path, verified_path, output_path]:
            if os.path.exists(p):
                os.unlink(p)


def test_cli_invocation():
    """Test CLI invocation works."""
    # Use the immutable pre-filter fixture: the live source is now filtered.
    source_path = 'D:/private/dynacity-data/engine/driving-water-speed-20261105/parked-cars-before-source-filter.json'
    verified_path = 'D:/private/dynacity-data/engine/driving-water-speed-20261105/workers/verified-water-positions.json'

    if not os.path.exists(source_path) or not os.path.exists(verified_path):
        print("⚠ Skipping CLI test - data files not found")
        return

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        output_path = f.name

    try:
        result = subprocess.run([
            sys.executable, 'scripts/filter_verified_water_parking.py',
            '--source', source_path,
            '--verified', verified_path,
            '--output', output_path,
            '--dry-run'
        ], capture_output=True, text=True, cwd=Path(__file__).parent.parent)

        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        assert "DRY RUN" in result.stdout
        assert "Removed:" in result.stdout
        print("PASS: CLI invocation test passed")

    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)


def test_zero_wrong_removals():
    """Test that only exact coordinate matches are removed (zero false positives)."""
    source_data = {
        'schema': 'dynacity-parked-cars/1',
        'seed': 42,
        'fill': 'test',
        'slots': [
            {'position': [1000.00, 2000.00, -50.00], 'heading_degrees': 0, 'model': 'bmw', 'road_id': 1},
            {'position': [1000.01, 2000.00, -50.00], 'heading_degrees': 0, 'model': 'bmw', 'road_id': 2},  # 1cm east diff
            {'position': [1000.00, 2000.01, -50.00], 'heading_degrees': 0, 'model': 'bmw', 'road_id': 3},  # 1cm north diff
            {'position': [1000.00, 2000.00, -49.99], 'heading_degrees': 0, 'model': 'bmw', 'road_id': 4},  # 1cm up diff
            {'position': [1000.004, 2000.004, -50.004], 'heading_degrees': 0, 'model': 'bmw', 'road_id': 5},  # Same at 1cm
        ],
        'note': 'test',
        'origin_utm_m': [0, 0, 0]
    }

    verified_data = {
        'schema': 'dynacity-verified-water-positions/1',
        'source_backup': 'D:/private/dynacity-data/engine/driving-water-speed-20261105/parked-before-water-filter.json',
        'origin_utm_m': [0, 0, 0],
        'instances': [
            {
                'enu': [1000.00, 2000.00, -50.00],
                'key': [1000.00, 2000.00, -50.00],
                'source_actor': 'actor1',
                'source_label': 'Parked_bmw',
                'source_component': 'MappedInstances',
                'source_index': 0,
                'ue_translation': [100000, -200000, -5000],
                'rotation': 'quat',
                'scale': [1, 1, 1],
                'duplicate_count': 1
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        source_path = f.name
        json.dump(source_data, f)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        verified_path = f.name
        json.dump(verified_data, f)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        output_path = f.name

    try:
        report = filter_parked_cars(source_path, verified_path, output_path, dry_run=False)

        # Only exact 1cm matches removed: index 0 and index 4 (same at 1cm precision)
        # Index 1, 2, 3 differ by >= 1cm in at least one axis
        assert report['removed_count'] == 2, f"Expected 2 removed (exact + 1cm dup), got {report['removed_count']}"
        assert report['kept_count'] == 3, f"Expected 3 kept, got {report['kept_count']}"

        print("PASS: Zero wrong removals test passed")

    finally:
        for p in [source_path, verified_path, output_path]:
            if os.path.exists(p):
                os.unlink(p)


def test_overwrite_guard():
    """Test that output path cannot resolve to source or verified file."""
    source_data = {
        'schema': 'dynacity-parked-cars/1',
        'seed': 42,
        'fill': 'test',
        'slots': [],
        'note': 'test',
        'origin_utm_m': [0, 0, 0]
    }
    verified_data = {
        'schema': 'dynacity-verified-water-positions/1',
        'origin_utm_m': [0, 0, 0],
        'instances': []
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        source_path = f.name
        json.dump(source_data, f)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        verified_path = f.name
        json.dump(verified_data, f)

    try:
        # Test output == source (should fail)
        try:
            filter_parked_cars(source_path, verified_path, source_path, dry_run=False)
            assert False, "Should have raised ValueError for output=source"
        except ValueError as e:
            assert "Overwrite guard" in str(e)

        # Test output == verified (should fail)
        try:
            filter_parked_cars(source_path, verified_path, verified_path, dry_run=False)
            assert False, "Should have raised ValueError for output=verified"
        except ValueError as e:
            assert "Overwrite guard" in str(e)

        print("PASS: Overwrite guard test passed")

    finally:
        for p in [source_path, verified_path]:
            if os.path.exists(p):
                os.unlink(p)


def test_unknown_metadata_preserved():
    """Test that all unknown metadata from source is preserved in output."""
    source_data = {
        'schema': 'dynacity-parked-cars/1',
        'seed': 42,
        'fill': 'test',
        'custom_field': 'should_be_preserved',
        'another_field': {'nested': 'data'},
        'array_field': [1, 2, 3],
        'slots': [
            {'position': [1000.0, 2000.0, -50.0], 'heading_degrees': 0, 'model': 'bmw', 'road_id': 1},
            {'position': [1001.0, 2001.0, -50.0], 'heading_degrees': 90, 'model': 'toyota', 'road_id': 2},
        ],
        'note': 'test',
        'origin_utm_m': [0, 0, 0]
    }

    verified_data = {
        'schema': 'dynacity-verified-water-positions/1',
        'origin_utm_m': [0, 0, 0],
        'instances': [
            {
                'enu': [1000.0, 2000.0, -50.0],
                'key': [1000.0, 2000.0, -50.0],
                'source_actor': 'actor1',
                'source_label': 'Parked_bmw',
                'source_component': 'MappedInstances',
                'source_index': 0,
                'ue_translation': [100000, -200000, -5000],
                'rotation': 'quat',
                'scale': [1, 1, 1],
                'duplicate_count': 1
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        source_path = f.name
        json.dump(source_data, f)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        verified_path = f.name
        json.dump(verified_data, f)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        output_path = f.name

    try:
        report = filter_parked_cars(source_path, verified_path, output_path, dry_run=False)

        with open(output_path, 'r') as f:
            filtered = json.load(f)

        # Check all original metadata preserved
        assert filtered['schema'] == 'dynacity-parked-cars/1'
        assert filtered['seed'] == 42
        assert filtered['fill'] == 'test'
        assert filtered['custom_field'] == 'should_be_preserved'
        assert filtered['another_field'] == {'nested': 'data'}
        assert filtered['array_field'] == [1, 2, 3]
        assert filtered['note'] == 'test'
        assert filtered['origin_utm_m'] == [0, 0, 0]
        # filter_report added
        assert 'filter_report' in filtered
        assert filtered['filter_report']['removed_slots'] == 1

        # Check slots filtered correctly
        assert len(filtered['slots']) == 1
        assert filtered['slots'][0]['position'] == [1001.0, 2001.0, -50.0]

        print("PASS: Unknown metadata preservation test passed")

    finally:
        for p in [source_path, verified_path, output_path]:
            if os.path.exists(p):
                os.unlink(p)


def test_enu_key_validation():
    """Test enu_key rejects bad input."""
    # Bad length
    try:
        enu_key([1, 2])
        assert False, "Should reject 2-element list"
    except ValueError as e:
        assert "3-element" in str(e)

    try:
        enu_key([1, 2, 3, 4])
        assert False, "Should reject 4-element list"
    except ValueError as e:
        assert "3-element" in str(e)

    # Non-finite
    try:
        enu_key([float('nan'), 0, 0])
        assert False, "Should reject NaN"
    except ValueError as e:
        assert "non-finite" in str(e)

    try:
        enu_key([float('inf'), 0, 0])
        assert False, "Should reject inf"
    except ValueError as e:
        assert "non-finite" in str(e)

    # Boolean (isinstance bool is subclass of int in Python)
    try:
        enu_key([True, 0, 0])
        assert False, "Should reject boolean"
    except ValueError as e:
        assert "boolean" in str(e)

    # String
    try:
        enu_key(["1", 0, 0])
        assert False, "Should reject string"
    except ValueError as e:
        assert "non-finite" in str(e)

    print("PASS: enu_key validation test passed")


if __name__ == '__main__':
    print("Running regression tests...\n")

    test_enu_key_conversion()
    test_duplicate_deduplication()
    test_compute_hash_deterministic()
    test_origin_validation()
    test_non_matching_preserved()
    test_zero_wrong_removals()
    test_overwrite_guard()
    test_unknown_metadata_preserved()
    test_enu_key_validation()
    test_filter_integration()
    test_cli_invocation()

    print("\n=== ALL TESTS PASSED ===")
