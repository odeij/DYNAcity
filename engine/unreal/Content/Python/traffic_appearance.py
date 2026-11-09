"""Stable presentation sampling; these weights do not alter SUMO demand."""
import hashlib
import math


def choose(identifier, entries, salt):
    """Use independent ID hashes so colours survive frame and session ordering."""
    if not entries:
        return None
    total = 0.0
    for entry in entries:
        weight = entry.get('weight')
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
            raise ValueError('Appearance weights must be finite and positive.')
        total += weight
    if not math.isfinite(total):
        raise ValueError('Appearance weights overflow.')
    value = int.from_bytes(hashlib.sha256((salt + '\0' + identifier).encode()).digest()[:8], 'big') / 2**64 * total
    cumulative = 0.0
    for entry in entries:
        cumulative += entry['weight']
        if value < cumulative:
            return entry
    return entries[-1]


def validate(presentation):
    for type_id, choices in presentation.get('traffic_model_choices', {}).items():
        choose('validation', choices, 'model')
        if any(choice.get('type_id') not in presentation.get('traffic_type_groups', {}) for choice in choices):
            raise ValueError('Appearance model has no native traffic group: ' + type_id)
    palette = presentation.get('traffic_paint_palette', [])
    choose('validation', palette, 'paint')
    for entry in palette:
        rgb = entry.get('rgb_linear')
        if not isinstance(rgb, list) or len(rgb) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in rgb):
            raise ValueError('Traffic paint requires three finite linear colour channels in [0,1].')
    return presentation


def apply(vehicle, presentation):
    original_type = vehicle.get('type_id')
    model = choose(vehicle['id'], presentation.get('traffic_model_choices', {}).get(original_type, []), 'model-v1')
    if model:
        vehicle['type_id'] = model['type_id']
    if original_type in presentation.get('traffic_paint_types', []):
        colour = choose(vehicle['id'], presentation.get('traffic_paint_palette', []), 'paint-v1')
        if colour:
            vehicle['paint_rgb'] = colour['rgb_linear']
    return vehicle
