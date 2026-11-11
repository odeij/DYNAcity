"""Stage only mapped dome cameras explicitly described as pole mounted."""
import math


def camera_placements(context):
    if context.get('schema') != 'dynacity-engine-street-context/1':
        raise ValueError('Expected mapped street context')
    rows, seen = [], set()
    for p in context.get('points', []):
        tags = p.get('tags', {})
        if p.get('kind') != 'man_made_surveillance' or tags.get('camera:type') != 'dome' or tags.get('camera:mount') != 'pole':
            continue
        identity, position = p.get('id', ''), p.get('position_local_m')
        if not identity.startswith('node/') or not isinstance(position, list) or len(position) != 3:
            continue
        if identity in seen or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in position):
            raise ValueError('Duplicate camera or invalid position')
        seen.add(identity)
        try:
            bearing = float(tags['camera:direction'])
            if not math.isfinite(bearing): raise ValueError('Nonfinite bearing')
            bearing %= 360
        except (KeyError, TypeError, ValueError):
            bearing = None
        rows.append({'source_id': identity, 'position_local_m': list(position),
                     'source_position_lonlat': p.get('source_position_lonlat'),
                     'production_area': p.get('production_area'), 'elevation_source': p.get('elevation_source'),
                     'as_of': p.get('as_of'), 'retrieved_on': p.get('retrieved_on'),
                     'camera_bearing_degrees_clockwise_from_north': bearing,
                     'bearing_source': 'OSM camera:direction' if bearing is not None else 'unknown',
                     'appearance_source': 'Generic camera and 2.84 m pole; appearance and height illustrative'})
    return {'schema': 'dynacity-mapped-cameras/1', 'origin_utm_m': context.get('origin_utm_m'), 'placements': rows}
