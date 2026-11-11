"""Add observed road speeds and an over-water flag to the Unreal location index (no Unreal import).

The built-in ambient traffic (`traffic_local.LocalTrafficClient`) drives on the index roads. Index road ids
are positions in the citywide traffic model, so each road can carry that model's Tari'ak hourly median
speeds. Roads whose centreline mostly lies over sea or no-ground cells are flagged so no car is spawned
on them. Writes `speed_kmh` (24 hourly medians) and `over_water` per road; existing keys are untouched.

  python scripts/annotate_location_index_traffic.py --index <location-index.json> \
      --traffic <beirut-traffic.json> --ground <ground.npz> [--output <path>]
"""
import argparse
import json
from pathlib import Path

import numpy as np

WATER_GROUND_M = 0.3      # ground at or below this (metres above sea level), or no ground, counts as water
WATER_SHARE = 0.25        # a road with at least this share of its points over water is flagged


def over_water(xy, ground, x0, y0, cell_m):
    """True when at least WATER_SHARE of the UTM points lie on sea-level or no-ground cells (north-down grid)."""
    p = np.asarray(xy, dtype=float)
    row = np.floor((y0 - p[:, 1]) / cell_m).astype(int)
    col = np.floor((p[:, 0] - x0) / cell_m).astype(int)
    inside = (row >= 0) & (row < ground.shape[0]) & (col >= 0) & (col < ground.shape[1])
    value = np.full(len(p), np.nan)
    value[inside] = ground[row[inside], col[inside]]
    wet = inside & (np.isnan(value) | (value <= WATER_GROUND_M))
    return bool(wet.mean() >= WATER_SHARE)


def annotate(index, traffic, ground, x0, y0, cell_m):
    roads, summary = traffic['roads'], {'annotated': 0, 'over_water': 0, 'unmatched': 0}
    origin = np.asarray(index['origin_utm_m'][:2], dtype=float)
    for road in index['roads']:
        rid = road['id']
        model = roads[rid] if 0 <= rid < len(roads) else None
        # Ids are model positions; confirm by an end point before trusting the speeds.
        if model is None or min(np.linalg.norm(np.asarray(road['path'][0][:2]) + origin - np.asarray(model['xy'][k]))
                                for k in (0, -1)) > 2.0:
            summary['unmatched'] += 1
            continue
        road['speed_kmh'] = [round(float(v), 1) for v in model['speed']]
        road['over_water'] = over_water(model['xy'], ground, x0, y0, cell_m)
        summary['annotated'] += 1
        summary['over_water'] += road['over_water']
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--index', required=True)
    parser.add_argument('--traffic', required=True)
    parser.add_argument('--ground', required=True, help='npz with ground, x0, y0, cell_m (north-down, metres above sea level)')
    parser.add_argument('--output', help='default: rewrite --index in place, after saving <index>.before-traffic-speeds.json')
    args = parser.parse_args()
    index_path = Path(args.index)
    index = json.loads(index_path.read_text(encoding='utf-8'))
    traffic = json.loads(Path(args.traffic).read_text(encoding='utf-8'))
    with np.load(args.ground) as grid:
        summary = annotate(index, traffic, grid['ground'], float(grid['x0']), float(grid['y0']), float(grid['cell_m']))
    output = Path(args.output) if args.output else index_path
    if output == index_path:
        index_path.with_name(index_path.stem + '.before-traffic-speeds.json').write_bytes(index_path.read_bytes())
    output.write_text(json.dumps(index, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
