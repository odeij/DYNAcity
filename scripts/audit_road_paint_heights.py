"""Compare pilot paint triangle heights with its saved source road batches.

This audits source GLB geometry, not Unreal material appearance, collisions or
renderer depth precision.  Paths and sampling limits are explicit so the tool
can be used without modifying the city or relying on a hard-coded private path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from dynacity.engine_city_batches import Glb


def local_xyz(positions):
    """Convert the glTF east/up/south axes to source east/north/up metres."""
    return np.column_stack((positions[:, 0], -positions[:, 2], positions[:, 1])).astype(float)


def triangle_height(triangle, x, y):
    a, b, c = triangle
    denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(denominator) < 1e-12:
        return None
    wa = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
    wb = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
    return float(wa * a[2] + wb * b[2] + (1 - wa - wb) * c[2])


def audit(paint_path: Path, city_checkpoint: Path, sample_limit: int):
    paint = Glb(paint_path)
    paint_groups = {}
    for node, _, positions, _, faces in paint.objects():
        road_id = node.get('extras', {}).get('road_id')
        if road_id is None or road_id in paint_groups:
            raise ValueError('Paint nodes must have unique road IDs')
        paint_groups[road_id] = (local_xyz(positions), faces)

    index = {}
    for package in json.loads(city_checkpoint.read_text(encoding='utf-8'))['packages']:
        if not package['key'].startswith('batch-roads-'):
            continue
        asset = Path(package['asset'])
        metadata = asset.with_suffix('.objects.json')
        if not metadata.is_file():
            raise FileNotFoundError(metadata)
        for item in json.loads(metadata.read_text(encoding='utf-8'))['objects']:
            info = item.get('metadata', {})
            road_id = info.get('road_id')
            if info.get('kind') == 'road' and road_id in paint_groups:
                if road_id in index:
                    raise ValueError('Road ID appears in multiple active batches')
                index[road_id] = (asset, item)

    glbs = {}
    results, all_offsets = [], []
    for road_id, (paint_positions, paint_faces) in sorted(paint_groups.items()):
        if road_id not in index:
            results.append({'road_id': road_id, 'status': 'missing_active_road'})
            continue
        asset, item = index[road_id]
        if asset not in glbs:
            glbs[asset] = {node['name']: (local_xyz(pos), faces)
                           for node, _, pos, _, faces in Glb(asset).objects()}
        road_positions, batch_faces = glbs[asset][item['batch']]
        start, count = item['first_triangle'], item['triangle_count']
        triangles = road_positions[batch_faces[start:start + count]]
        polygons, road_triangles = [], []
        for triangle in triangles:
            polygon = Polygon(triangle[:, :2])
            if polygon.area > 1e-10:
                polygons.append(polygon)
                road_triangles.append(triangle)
        if not polygons:
            results.append({'road_id': road_id, 'status': 'no_road_triangles'})
            continue
        tree = STRtree(polygons)
        chosen = np.linspace(0, len(paint_faces) - 1, min(sample_limit, len(paint_faces)), dtype=int)
        offsets, unmatched_distances, unmatched_points = [], [], []
        for i in chosen:
            centre = paint_positions[paint_faces[i]].mean(axis=0)
            point = Point(float(centre[0]), float(centre[1]))
            heights = [triangle_height(road_triangles[j], centre[0], centre[1])
                       for j in tree.query(point) if polygons[j].covers(point)]
            heights = [z for z in heights if z is not None]
            if not heights:
                _, distances = tree.query_nearest(point, return_distance=True)
                distance = float(np.min(distances))
                unmatched_distances.append(distance)
                unmatched_points.append({'position_m': centre.tolist(), 'distance_m': distance})
                continue
            offsets.append(min((float(centre[2] - z) for z in heights), key=abs))
        all_offsets.extend(offsets)
        results.append({'road_id': road_id, 'status': 'sampled', 'samples': len(chosen),
                        'matched': len(offsets), 'unmatched': len(unmatched_distances),
                        'max_unmatched_distance_m': max(unmatched_distances) if unmatched_distances else None,
                        'unmatched_points': unmatched_points,
                        'min_offset_m': min(offsets) if offsets else None,
                        'median_offset_m': statistics.median(offsets) if offsets else None,
                        'max_offset_m': max(offsets) if offsets else None,
                        'below_road': sum(v < -0.001 for v in offsets),
                        'over_10cm': sum(v > 0.1 for v in offsets)})
    return {'source_paint': str(paint_path), 'city_checkpoint': str(city_checkpoint),
            'sample_limit_per_road': sample_limit, 'roads': results,
            'matched_samples': len(all_offsets),
            'median_offset_m': statistics.median(all_offsets) if all_offsets else None,
            'below_road': sum(v < -0.001 for v in all_offsets),
            'over_10cm': sum(v > 0.1 for v in all_offsets),
            'limits': 'Source GLB triangle samples only; not a rendered, collision, or full-coverage test.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paint', type=Path, required=True)
    parser.add_argument('--city-checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples-per-road', type=int, default=200)
    args = parser.parse_args()
    if args.samples_per_road < 1:
        parser.error('--samples-per-road must be positive')
    report = audit(args.paint, args.city_checkpoint, args.samples_per_road)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('matched_samples', 'median_offset_m', 'below_road', 'over_10cm')}))


if __name__ == '__main__':
    main()
