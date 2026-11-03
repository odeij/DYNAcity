"""Prepare the sea, Corniche edge and parked-car placements for the Unreal city (no Unreal import).

Inputs: the shared road surface JSON (local metres, EPSG:32636 offset) and an Overpass export of
natural=coastline / breakwaters / piers. Outputs, all illustrative rather than surveyed:
  water-normal.png   tileable wave normal map generated here (sum of directional sine swell + fine ripples)
  corniche.json      seawall, parapet and railing-post placements along the Corniche coastline
  parked-cars.json   kerbside parked-vehicle slots on ordinary ground-level streets
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine' / 'unreal' / 'Content' / 'Python'))
import location_core  # noqa: E402

# Sea level in the model's local frame (local z = model z - 51.106 m origin height). The Beirut 001
# vertical datum is undeclared, but its ground matches Copernicus GLO-30 (heights above mean sea level,
# EGM2008) to a median of +0.34 m on the 0-3 m coastal strip (+0.4 m citywide, 207,653 cells compared
# on 1 Nov 2026), so mean sea level is about model z -0.3, i.e. local z -51.4.
SEA_Z = -51.4
# Ain el-Mreisseh to Raouche: the stretch with a continuous Corniche promenade and seawall.
CORNICHE_LONLAT = (35.466, 33.886, 35.506, 33.904)


def water_normal(path, size=1024, seed=7):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / size
    height = np.zeros((size, size))
    # Integer wave numbers keep every component tileable.
    for _ in range(48):
        kx, ky = rng.integers(-14, 15, 2)
        if kx == 0 and ky == 0:
            continue
        k = math.hypot(kx, ky)
        amp = 1 / (k ** 1.6)
        height += amp * np.sin(2 * math.pi * (kx * x + ky * y) + rng.uniform(0, 2 * math.pi))
    gy, gx = np.gradient(height)
    strength = 1.6 / (np.abs(gx).max() + np.abs(gy).max())
    n = np.dstack([-gx * strength, -gy * strength, np.ones_like(height)])
    n /= np.linalg.norm(n, axis=2, keepdims=True)
    Image.fromarray(((n * .5 + .5) * 255).astype(np.uint8)).save(path)


def _local(lon, lat, origin):
    x, y = location_core.lonlat_to_utm(lon, lat)
    return [x - origin[0], y - origin[1]]


def corniche(coast, roads, origin):
    west, south, east, north = CORNICHE_LONLAT
    pts = np.array([p[:3] for r in roads if r['level'] == 0 for p in r['path']])
    walls, posts = [], []
    for way in coast['elements']:
        if way['tags'].get('natural') != 'coastline':
            continue
        line = [_local(g['lon'], g['lat'], origin) for g in way['geometry']
                if west <= g['lon'] <= east and south <= g['lat'] <= north]
        for a, b in zip(line, line[1:]):
            length = math.dist(a, b)
            if not 1 <= length <= 200:
                continue
            mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
            d2 = (pts[:, 0] - mid[0]) ** 2 + (pts[:, 1] - mid[1]) ** 2
            near = int(d2.argmin())
            if d2[near] > 70 ** 2:
                continue  # no modeled road behind this stretch: leave natural rock shore alone
            top = float(pts[near, 2]) + .3
            heading = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            walls.append({'center': [mid[0], mid[1], (SEA_Z + top) / 2], 'heading_degrees': heading,
                          'length_m': length + .2, 'height_m': top - SEA_Z, 'top_z': top})
            for i in range(int(length // 2.5) + 1):
                t = min(1, i * 2.5 / length)
                posts.append({'position': [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, top],
                              'heading_degrees': heading})
    return {'schema': 'dynacity-corniche/1', 'sea_z': SEA_Z, 'walls': walls, 'railing_posts': posts,
            'note': 'Illustrative seawall/railing along OSM coastline where a modeled road lies within 70 m.'}


def _road_grid(roads, cell=8.0):
    """Densified centreline samples of every road, bucketed for nearest-carriageway checks."""
    grid = {}
    for road in roads:
        half = (road.get('width_m') or 6) / 2
        for a, b in zip(road['path'], road['path'][1:]):
            n = max(1, int(math.dist(a[:2], b[:2]) // 2))
            for i in range(n + 1):
                x, y = a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n
                grid.setdefault((int(x // cell), int(y // cell)), []).append((x, y, half, road['id'], road['level']))
    return grid


def _on_other_road(grid, x, y, road_id, cell=8.0):
    gx, gy = int(x // cell), int(y // cell)
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            for px, py, half, rid, level in grid.get((gx + i, gy + j), ()):
                if rid != road_id and level == 0 and math.hypot(x - px, y - py) < half + 2.5:
                    return True
    return False


def parked_cars(roads, seed='dynacity-parked-v1', fill=0.45):
    slots = []
    grid = _road_grid(roads)
    for road in roads:
        width = road.get('width_m') or 0
        if road['level'] != 0 or not 5.5 <= width <= 11 or 'Highway' in (road.get('name') or ''):
            continue
        path = road['path']
        offset = width / 2 - 1.1
        total_length = sum(math.dist(a[:2], b[:2]) for a, b in zip(road['path'], road['path'][1:]))
        along = 0.0
        for a, b in zip(path, path[1:]):
            seg = math.dist(a[:2], b[:2])
            if seg < .01:
                continue
            ux, uy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
            s = (6.0 - along % 6.0) % 6.0
            while s < seg:
                total = along + s
                for side in (-1, 1):
                    key = f"{seed}:{road['id']}:{total:.1f}:{side}"
                    h = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'little') / 2 ** 64
                    if h > fill or total < 12 or total > total_length - 12:
                        continue   # keep junction mouths clear at both ends
                    px, py = a[0] + ux * s - uy * offset * side, a[1] + uy * s + ux * offset * side
                    if _on_other_road(grid, px, py, road['id']):
                        continue   # would sit in a crossing or adjoining carriageway
                    pz = a[2] + (b[2] - a[2]) * s / seg
                    heading = math.degrees(math.atan2(uy, ux)) + (0 if side < 0 else 180)
                    model = 'van' if h < fill * .08 else ('bmw' if h < fill * .54 else 'toyota')
                    slots.append({'position': [px, py, pz], 'heading_degrees': heading, 'model': model,
                                  'road_id': road['id']})
                s += 6.0
            along += seg
    return {'schema': 'dynacity-parked-cars/1', 'seed': seed, 'fill': fill, 'slots': slots,
            'note': 'Illustrative kerbside parking on 5.5-11 m ground-level streets; not observed occupancy.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--roads', required=True)
    p.add_argument('--coastline', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    out = Path(a.output); out.mkdir(parents=True, exist_ok=True)
    roads_doc = json.loads(Path(a.roads).read_text(encoding='utf-8'))
    origin = roads_doc['coordinates']['origin_utm_m']
    water_normal(out / 'water-normal.png')
    c = corniche(json.loads(Path(a.coastline).read_text(encoding='utf-8')), roads_doc['roads'], origin)
    c['origin_utm_m'] = origin
    (out / 'corniche.json').write_text(json.dumps(c), encoding='utf-8')
    pc = parked_cars(roads_doc['roads']); pc['origin_utm_m'] = origin
    (out / 'parked-cars.json').write_text(json.dumps(pc), encoding='utf-8')
    print(json.dumps({'walls': len(c['walls']), 'posts': len(c['railing_posts']), 'parked': len(pc['slots'])}))


if __name__ == '__main__':
    main()
