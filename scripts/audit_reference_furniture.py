"""Check saved reference prop points against active city building triangles.

The check is deliberately conservative about what it proves: a projected
point inside a building triangle with a nearby height range is an overlap
candidate, not a collision test of the full mesh or a surveyed location.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from shapely.geometry import Point, Polygon

from dynacity.engine_city_batches import Glb


def audit(positions_path: Path, checkpoint_path: Path):
    positions = json.loads(positions_path.read_text(encoding='utf-8'))
    if not positions.get('success'):
        raise ValueError('Position extraction did not succeed')
    rows = positions['instances']
    points = [Point(*row['position_local_m'][:2]) for row in rows]
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    candidates = []
    for package in checkpoint['packages']:
        if not package['key'].startswith('batch-buildings-'):
            continue
        asset = Path(package['asset'])
        index_path = asset.with_suffix('.objects.json')
        for item in json.loads(index_path.read_text(encoding='utf-8'))['objects']:
            low, high = item['local_bounds_m']
            selected = [i for i, point in enumerate(points)
                        if low[0] <= point.x <= high[0] and low[1] <= point.y <= high[1]
                        and low[2] - 1 <= rows[i]['position_local_m'][2] <= high[2] + 1]
            if selected:
                candidates.append((asset, item, selected))
    batches, hits = {}, [[] for _ in points]
    for asset, item, selected in candidates:
        if asset not in batches:
            batches[asset] = {
                node['name']: (np.column_stack((pos[:, 0], -pos[:, 2], pos[:, 1])), faces)
                for node, _, pos, _, faces in Glb(asset).objects()
            }
        vertices, faces = batches[asset][item['batch']]
        start, count = item['first_triangle'], item['triangle_count']
        triangles = vertices[faces[start:start + count]]
        for i in selected:
            point = points[i]
            nearby = triangles[(triangles[:, :, 0].min(axis=1) <= point.x)
                               & (triangles[:, :, 0].max(axis=1) >= point.x)
                               & (triangles[:, :, 1].min(axis=1) <= point.y)
                               & (triangles[:, :, 1].max(axis=1) >= point.y)]
            for triangle in nearby:
                polygon = Polygon(triangle[:, :2])
                if polygon.area > 1e-10 and polygon.covers(point):
                    hits[i].append(item['source_id'])
                    break
    return {'success': True, 'count': len(rows),
            'overlap_count': sum(bool(group) for group in hits),
            'rows': [dict(row, building_source_ids=hits[i]) for i, row in enumerate(rows)],
            'limits': 'Checks prop origin points against projected source triangles and building height ranges; does not test full prop geometry or rendered placement.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--positions', type=Path, required=True)
    parser.add_argument('--city-checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.positions, args.city_checkpoint)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('count', 'overlap_count')}))


if __name__ == '__main__':
    main()
