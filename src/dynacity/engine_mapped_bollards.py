"""Prepare generic finished bollard instances from mapped point records only."""
from __future__ import annotations

import math


def bollard_placements(context):
    if context.get('schema') != 'dynacity-engine-street-context/1':
        raise ValueError('Expected a mapped street context')
    placements = []
    seen = set()
    for point in context.get('points', []):
        if point.get('kind') != 'bollard':
            continue
        source_id = point.get('id', '')
        position = point.get('position_local_m')
        # OSM ways do not establish a single fixture position.
        if not source_id.startswith('node/') or not isinstance(position, list) or len(position) != 3:
            continue
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in position):
            raise ValueError('Invalid bollard position')
        if source_id in seen:
            raise ValueError('Duplicate bollard source node')
        seen.add(source_id)
        placements.append({
            'source_id': source_id, 'position_local_m': list(position),
            'source_position_lonlat': point.get('source_position_lonlat'),
            'production_area': point.get('production_area'),
            'as_of': point.get('as_of'), 'retrieved_on': point.get('retrieved_on'),
            'elevation_source': point.get('elevation_source'),
            'asset_key': 'steel_bollard_innerscene',
            'appearance_source': 'Illustrative generic model; material and shape are not observed',
        })
    return {'schema': 'dynacity-mapped-bollards/1',
            'origin_utm_m': context.get('origin_utm_m'), 'placements': placements}
