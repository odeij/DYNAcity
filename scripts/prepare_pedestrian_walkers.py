"""Turn modelled pedestrian flows into pavement walker densities for the Unreal city (no Unreal import).

Input: `pedestrian-flows-pm.geojson` from `scripts/run_pedestrian_una.py` (EPSG:32636, `ped_per_hour_pm`
per segment; modelled, not observed). Output: `pedestrian-walkers.json` in the city's local metres.

People on a segment at any moment = flow (people/h) / 3600 * length / walking speed. Each flow segment
is snapped to the parallel modeled road it runs along, and walkers use the middle of that road's pavement
as the 3D model builds it (road width / 2 + pavement width / 2 from the centreline, 15 cm kerb above the
road's own height at every vertex). The formula's constant (about 71/h on every street) is removed as a
baseline, so quiet streets get few walkers rather than a uniform crowd.
"""
import argparse
import json
import math
from pathlib import Path

WALK_SPEED_MS = 1.3
BASELINE = 71.3925      # run_pedestrian_una.INTERCEPT: the regression constant, not trips on the street
MIN_PEOPLE_PER_100M = 0.15
# engine_city_surface: pavements are raised KERB_M above the asphalt; fast roads get none.
KERB_M = 0.15
FAST_ROADS = {'motorway', 'motorway_link', 'trunk', 'trunk_link'}


def pavement_width(highway):
    """Same rule as engine_city_surface.pavement_width: the pavement the 3D model actually builds."""
    if highway in FAST_ROADS:
        return 0.0
    return 1.6 if highway in ('living_street', 'residential') else 2.4


def road_index(roads, highways, cell=10.0):
    """Model ground roads sampled along their paths: (x, y, road id, unit direction)."""
    grid, usable = {}, {}
    for road in roads:
        if road['level'] != 0 or len(road['path']) < 2:
            continue
        walk = pavement_width(highways.get(road.get('source_way'), 'residential'))
        if not walk:
            continue
        usable[road['id']] = (road, walk)
        path = road['path']
        for a, b in zip(path, path[1:]):
            n = math.dist(a[:2], b[:2]) or 1
            d = ((b[0] - a[0]) / n, (b[1] - a[1]) / n)
            grid.setdefault((int(a[0] // cell), int(a[1] // cell)), []).append((a[0], a[1], road['id'], d))
    return grid, usable


def match_road(grid, x, y, direction, cell=10.0, reach=1, max_m=10.0):
    """Nearest model road sample near (x, y) running roughly parallel to the walking segment."""
    best = None
    gx, gy = int(x // cell), int(y // cell)
    for i in range(-reach, reach + 1):
        for j in range(-reach, reach + 1):
            for px, py, rid, d in grid.get((gx + i, gy + j), ()):
                if abs(d[0] * direction[0] + d[1] * direction[1]) < 0.8:
                    continue
                dist = math.hypot(px - x, py - y)
                if dist <= max_m and (best is None or dist < best[0]):
                    best = (dist, rid)
    return best


def project(path, x, y):
    """Distance along a polyline of the point closest to (x, y)."""
    best, along = None, 0.0
    for a, b in zip(path, path[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg = math.hypot(dx, dy)
        t = 0.0 if seg < 1e-9 else max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / seg ** 2))
        d = math.hypot(a[0] + dx * t - x, a[1] + dy * t - y)
        if best is None or d < best[0]:
            best = (d, along + seg * t)
        along += seg
    return best[1]


def point_at(path, s):
    """3D point at distance s along a polyline."""
    along = 0.0
    for a, b in zip(path, path[1:]):
        seg = math.dist(a[:2], b[:2])
        if along + seg >= s and seg > 0:
            t = (s - along) / seg
            return [a[k] + (b[k] - a[k]) * t for k in range(3)]
        along += seg
    return list(path[-1])


def sub_path(path, s0, s1):
    """Part of a 3D polyline between two distances along it (interpolated ends, original vertices between)."""
    out, along = [point_at(path, s0)], 0.0
    for a, b in zip(path, path[1:]):
        along += math.dist(a[:2], b[:2])
        if s0 < along < s1:
            out.append(list(b))
    out.append(point_at(path, s1))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--flows', required=True)
    p.add_argument('--roads', required=True, help='shared-city-surface roads JSON (local metres, widths, heights)')
    p.add_argument('--osm', required=True, help='osm-pedestrian-source.json (highway class of each road way)')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    roads_doc = json.loads(Path(a.roads).read_text(encoding='utf-8'))
    ox, oy = roads_doc['coordinates']['origin_utm_m'][:2]
    highways = {e['id']: e['tags']['highway'] for e in json.loads(Path(a.osm).read_text(encoding='utf-8'))['elements']
                if e['type'] == 'way' and 'highway' in e.get('tags', {})}
    grid, usable = road_index(roads_doc['roads'], highways)
    segments, skipped = [], 0
    for f in json.loads(Path(a.flows).read_text(encoding='utf-8'))['features']:
        flow = f['properties']['ped_per_hour_pm']
        coords = [[x - ox, y - oy] for x, y in f['geometry']['coordinates']]
        (x0, y0), (x1, y1) = coords[0], coords[-1]
        n = math.hypot(x1 - x0, y1 - y0)
        if n < 3:
            continue
        hit = match_road(grid, (x0 + x1) / 2, (y0 + y1) / 2, ((x1 - x0) / n, (y1 - y0) / n))
        if hit is None:
            skipped += 1   # no parallel modeled ground street with a pavement: footpaths, highways, off-model
            continue
        road, walk = usable[hit[1]]
        s0, s1 = sorted((project(road['path'], x0, y0), project(road['path'], x1, y1)))
        length = s1 - s0
        if length < 3:
            continue
        people = max(0.0, flow - BASELINE) / 3600 * length / WALK_SPEED_MS
        if people / length * 100 < MIN_PEOPLE_PER_100M:
            continue
        path = sub_path(road['path'], s0, s1)
        segments.append({'path': [[round(q[0], 2), round(q[1], 2), round(q[2] + KERB_M, 3)] for q in path],
                         'road_id': road['id'], 'offset_m': round(road['width_m'] / 2 + walk / 2, 2),
                         'pavement_m': walk, 'people': round(people, 3), 'ped_per_hour_pm': round(flow, 1)})
    out = {'schema': 'dynacity-pedestrian-walkers/2', 'origin_utm_m': roads_doc['coordinates']['origin_utm_m'],
           'walk_speed_ms': WALK_SPEED_MS, 'baseline_removed_per_hour': BASELINE, 'segments': segments,
           'status': 'modelled (UNA with Sevtsuk et al. 2024 coefficients, proxy inputs); not observed counts',
           'source': 'run_pedestrian_una.py output on model road paths; OSM network (ODbL)'}
    Path(a.output).write_text(json.dumps(out), encoding='utf-8')
    total = sum(s['people'] for s in segments)
    print(json.dumps({'segments': len(segments), 'skipped_off_model': skipped, 'people_citywide': round(total)}))


if __name__ == '__main__':
    main()
