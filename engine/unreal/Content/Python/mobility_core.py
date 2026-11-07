"""Pure geometry helpers shared by the native map and bounded SUMO requests."""
import math
import location_core


def simplify(points, tolerance=2):
    if len(points) <= 2: return points
    a, b = points[0], points[-1]
    dx, dy = b[0]-a[0], b[1]-a[1]
    length2 = dx*dx+dy*dy
    best, split = 0, 0
    for i, p in enumerate(points[1:-1], 1):
        t = max(0, min(1, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/max(length2, 1e-12)))
        distance = math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)
        if distance > best: best, split = distance, i
    if best <= tolerance: return [a, b]
    return simplify(points[:split+1], tolerance)[:-1]+simplify(points[split:], tolerance)


def map_payload(index):
    # Local metres are offset UTM36N, not east/north offsets from a lat/lon pair.
    # Both renderers need the authoritative origin to retain meridian convergence.
    origin = index.get('origin_utm_m')
    if (not isinstance(origin, (list, tuple)) or len(origin) < 2 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in origin) or not 100000 <= origin[0] <= 900000 or
            not 0 <= origin[1] <= 9500000):
        raise ValueError('The map needs a valid EPSG:32636 origin_utm_m.')
    return {
        'origin_utm_m': list(origin),
        'map_reference': {'crs': 'EPSG:32636',
                          'origin_lonlat': list(location_core.utm_to_lonlat(*origin[:2]))},
        'roads': [dict({'id': r['id'], 'name': r.get('name') or '', 'path': simplify(r['path'])}, **_speeds(r))
                  for r in index['roads']],
    }


def _speeds(road):
    # Tari'ak hourly median speeds (km/h, hours 0-23) let the native GPS plan by travel time.
    speeds = road.get('speed_kmh')
    if (not isinstance(speeds, list) or len(speeds) != 24 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                for v in speeds)):
        return {}
    return {'speed_kmh': [round(v, 1) for v in speeds]}


def traffic_view(index, local, radius=900):
    # Invert the known Beirut origin using the index's geographic origin pair.
    # A small local rectangle bounds demand/response; changing area needs reconnect.
    from location_core import utm_to_lonlat
    x, y = index['origin_utm_m'][:2]
    west, south = utm_to_lonlat(x+local[0]-radius, y+local[1]-radius)
    east, north = utm_to_lonlat(x+local[0]+radius, y+local[1]+radius)
    return [west, south, east, north]
