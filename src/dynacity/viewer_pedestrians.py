"""Browser representation of the same private pavement paths used by Unreal."""
from __future__ import annotations

import math

from .viewer import utm_to_lonlat


def pedestrian_payload(document: dict, traffic: dict | None = None) -> dict:
    if document.get('schema') != 'dynacity-pedestrian-walkers/2':
        raise ValueError('Expected dynacity-pedestrian-walkers/2')
    origin = document['origin_utm_m']
    if len(origin) < 3 or not all(math.isfinite(v) for v in origin):
        raise ValueError('Pedestrian origin must contain finite east, north and elevation')
    # Match geometry, never assume the engine's road indexes equal the web model's.
    from shapely.geometry import LineString, Point
    from shapely.strtree import STRtree
    roads = (traffic or {}).get('roads', [])
    indexes = [i for i, r in enumerate(roads) if not r.get('lvl') and len(r['path']) >= 2]
    lines = [LineString([p[:2] for p in roads[i]['path']]) for i in indexes]
    tree = STRtree(lines) if lines else None
    segments = []
    for segment in document['segments']:
        path = segment['path']
        values = [segment[k] for k in ('people', 'offset_m', 'pavement_m', 'ped_per_hour_pm')]
        if len(path) < 2 or any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in path):
            raise ValueError('Pedestrian paths need at least two finite 3D points')
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError('Pedestrian flow and pavement dimensions must be finite and nonnegative')
        converted = [[*utm_to_lonlat(p[0]+origin[0], p[1]+origin[1], 36), p[2]+origin[2]] for p in path]
        road_index = None
        if tree is not None:
            midpoint = LineString(converted).interpolate(.5, normalized=True)
            nearest = int(tree.nearest(midpoint))
            # Matching is an inferred display association only (about 12 m at Beirut).
            if lines[nearest].distance(Point(midpoint.x, midpoint.y)) < .0001:
                road_index = indexes[nearest]
        segments.append({**{k: segment[k] for k in ('people', 'offset_m', 'pavement_m', 'ped_per_hour_pm')},
                         'road_index': road_index,
                         # Absolute elevations are retained here; the browser drapes to its road plane.
                         'path': converted})
    return {'segments': segments, 'walk_speed_ms': document['walk_speed_ms'],
            'status': document['status'], 'source': document['source'],
            'baseline_removed_per_hour': document['baseline_removed_per_hour'],
            'period': 'Weekday PM peak, 16:30–17:30',
            'limitations': 'Proxy demand; not observed counts. Pavement playback has no crossing, car avoidance or pedestrian rerouting solver.'}
