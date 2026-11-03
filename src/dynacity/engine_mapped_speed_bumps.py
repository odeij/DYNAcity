"""Prepare illustrative speed-bump placements from mapped bump nodes."""
import math


def speed_bump_placements(context, model_length_m):
    if context.get('schema') != 'dynacity-engine-street-context/1':
        raise ValueError('Expected a mapped street context')
    if not isinstance(model_length_m, (float, int)) or isinstance(model_length_m, bool) or not math.isfinite(model_length_m) or model_length_m <= 0:
        raise ValueError('A positive model length is required')
    placements, skipped, seen = [], [], set()
    for point in context.get('points', []):
        if point.get('kind') != 'traffic_calming' or point.get('tags', {}).get('traffic_calming') != 'bump':
            continue
        ident = point.get('id', '')
        position = point.get('position_local_m')
        width, heading = point.get('road_width_m'), point.get('road_heading_degrees')
        if not ident.startswith('node/') or not isinstance(position, list) or len(position) != 3 or point.get('road_level') != 0:
            skipped.append({'source_id': ident, 'reason': 'missing point or ground-level road'}); continue
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in position):
            raise ValueError('Invalid source position')
        if ident in seen: raise ValueError('Duplicate source node')
        seen.add(ident)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (width, heading)) or not 1 <= width <= 30:
            skipped.append({'source_id': ident, 'reason': 'missing valid road width or heading'}); continue
        placements.append({'source_id': ident, 'position_local_m': list(position),
            'source_position_lonlat': point.get('source_position_lonlat'), 'production_area': point.get('production_area'),
            'as_of': point.get('as_of'), 'retrieved_on': point.get('retrieved_on'),
            'road_id': point.get('road_id'), 'road_width_m': width,
            'heading_degrees': (heading + 90) % 360, 'scale_x': width / model_length_m,
            'elevation_source': point.get('elevation_source'),
            'placement_source': 'Mapped node; cross-road orientation and width inferred from nearest road',
            'appearance_source': 'Illustrative rubber bump; actual dimensions and materials unknown'})
    return {'schema': 'dynacity-mapped-speed-bumps/1', 'origin_utm_m': context.get('origin_utm_m'),
            'model_length_m': model_length_m, 'placements': placements, 'skipped': skipped}
