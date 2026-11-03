"""Bounded evidence from returned trajectories, independent of Unreal rendering."""
from collections import Counter
import math


def summarize_frames(frames):
    previous = {}
    identifiers = set()
    types = Counter()
    pairs = moved = 0
    maximum = 0.0
    for frame in frames:
        current = {}
        for vehicle in frame['vehicles']:
            identifier = vehicle['id']
            position = vehicle['position']
            identifiers.add(identifier)
            types[vehicle.get('type_id', 'unknown')] += 1
            prior = previous.get(identifier)
            if prior is not None and frame['time_s'] > prior[0]:
                distance = math.dist(position, prior[1])
                pairs += 1
                moved += distance > .01
                maximum = max(maximum, distance)
            current[identifier] = (frame['time_s'], position)
        previous = current
    return {'frames': len(frames), 'vehicle_samples': sum(types.values()),
            'unique_vehicles': len(identifiers), 'type_samples': dict(types),
            'time_first_s': frames[0]['time_s'] if frames else None,
            'time_last_s': frames[-1]['time_s'] if frames else None,
            'matched_trajectory_pairs': pairs, 'moving_trajectory_pairs': moved,
            'max_step_distance_m': maximum}
