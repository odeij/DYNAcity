"""Moving traffic evidence must follow identities rather than array positions."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python'))
from traffic_evidence import summarize_frames


def vehicle(identifier, x):
    return {'id': identifier, 'position': [x, 0, 2], 'type_id': 'car'}


def test_reordering_and_arrivals_are_not_counted_as_motion():
    evidence = summarize_frames([
        {'time_s': 10, 'vehicles': [vehicle('a', 0), vehicle('b', 50)]},
        {'time_s': 11, 'vehicles': [vehicle('b', 50), vehicle('a', 3), vehicle('new', 200)]}])
    assert evidence['matched_trajectory_pairs'] == 2
    assert evidence['moving_trajectory_pairs'] == 1
    assert evidence['max_step_distance_m'] == 3
    assert evidence['unique_vehicles'] == 3


def test_same_time_and_disappearing_ids_do_not_prove_movement():
    evidence = summarize_frames([
        {'time_s': 10, 'vehicles': [vehicle('a', 0)]},
        {'time_s': 10, 'vehicles': [vehicle('a', 20)]},
        {'time_s': 11, 'vehicles': []},
        {'time_s': 12, 'vehicles': [vehicle('a', 100)]}])
    assert evidence['moving_trajectory_pairs'] == 0
    assert summarize_frames([])['time_first_s'] is None
