"""Bounded placement of authored facade modules on existing source walls.

Placement is illustrative. The source building mesh remains authoritative and
unchanged; module geometry, UVs and materials belong to the licensed library.
"""
from __future__ import annotations
import hashlib
import math
import numpy as np


def native_outward_sign(source_y_bounds, native_y_bounds_cm, tolerance_cm=.1):
    """Use the authored asymmetric recess to detect the importer's Y sign."""
    low, high = [100 * float(value) for value in source_y_bounds]
    actual = np.asarray(native_y_bounds_cm, dtype=float)
    same = np.max(np.abs(actual - [low, high])) <= tolerance_cm
    flipped = np.max(np.abs(actual - [-high, -low])) <= tolerance_cm
    if same == flipped:
        raise ValueError('Facade importer outward axis is ambiguous or shifted')
    return 1 if same else -1


def unreal_module_yaw(source_yaw_deg, outward_sign):
    """Map authored outward onto east/south coordinates using a proper rotation."""
    if outward_sign not in (-1,1):raise ValueError('Invalid module outward sign')
    return -source_yaw_deg + (180 if outward_sign == 1 else 0)


def source_walls(vertices, triangles):
    """Recover bottom edges only from genuinely vertical source triangles."""
    points = np.asarray(vertices, dtype=float)
    result = {}
    for face in triangles:
        tri = points[face]
        cross = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        size = np.linalg.norm(cross)
        if size < 1e-8 or abs(cross[2]) / size > .015:
            continue
        low = np.flatnonzero(np.abs(tri[:, 2] - tri[:, 2].min()) < .015)
        if len(low) != 2 or np.ptp(tri[:, 2]) < 2.7:
            continue
        a, b = tri[low]
        length = np.linalg.norm(b[:2] - a[:2])
        if length < 3.6:
            continue
        normal = cross / size
        key = tuple(sorted((tuple(a.round(4)), tuple(b.round(4)))))
        result[key] = {'a': a.tolist(), 'b': b.tolist(),
            'normal': normal.tolist(), 'height_m': float(np.ptp(tri[:, 2]))}
    return list(result.values())


def plan_modules(buildings, *, bounds, max_instances=1800, tile_size_m=250,
                 view_distance_m=180, width_m=3., height_m=3., lift_m=.035,
                 focus_point=None, priority_point=None, exclude_ids=None):
    """Emit deterministic positive-scale transforms and retain source identities.

    Local module X runs along the wall, local Y points outward, local Z is up.
    Source winding is checked against the building centroid, so the depth face
    points outwards even when input triangles have inverted winding.
    """
    if (max_instances < 1 or tile_size_m <= 0 or view_distance_m <= 0 or
            width_m <= 0 or height_m <= 0 or lift_m <= 0):
        raise ValueError('Invalid facade placement budget or dimensions')
    xmin, ymin, xmax, ymax = bounds
    if xmin >= xmax or ymin >= ymax:
        raise ValueError('Empty facade selection bounds')
    placements, omissions = [], []
    excluded=set(exclude_ids or [])
    selected = 0
    def order(item):
        if priority_point is None:return (0,item['id'])
        verts=np.asarray(item['vertices'],dtype=float)
        center=(verts.min(axis=0)+verts.max(axis=0))/2
        distance=float(np.sum((center[:2]-np.asarray(priority_point))**2))
        return (distance,item['id'])
    for building in sorted(buildings, key=order):
        vertices = np.asarray(building['vertices'], dtype=float)
        if not np.isfinite(vertices).all():
            raise ValueError('Nonfinite building coordinates')
        center = (vertices.min(axis=0) + vertices.max(axis=0)) / 2
        if not (xmin <= center[0] <= xmax and ymin <= center[1] <= ymax):
            continue
        selected += 1
        walls = source_walls(vertices, building['triangles'])
        if not walls:
            omissions.append({'source_id': building['id'], 'reason': 'no eligible vertical wall'})
        for wall_index, wall in enumerate(walls):
            a, b = np.asarray(wall['a']), np.asarray(wall['b'])
            normal = np.asarray(wall['normal'])[:2]
            if np.dot(normal, (a[:2] + b[:2]) / 2 - center[:2]) < 0:
                normal *= -1
            if focus_point is not None and np.dot(normal,
                    np.asarray(focus_point) - (a[:2] + b[:2]) / 2) <= 0:
                continue
            # X cross Y must point up: this guarantees a proper rotation,
            # without mirrored source geometry or negative instance scales.
            tangent = np.array([normal[1], -normal[0]])
            if np.dot(b[:2] - a[:2], tangent) < 0:
                a, b = b, a
            length = float(np.linalg.norm(b[:2] - a[:2]))
            columns = math.floor((length - .6) / width_m)
            rows = math.floor((wall['height_m'] - .3) / height_m)
            if not columns or not rows:
                continue
            margin = (length - columns * width_m) / 2
            yaw = math.degrees(math.atan2(tangent[1], tangent[0]))
            for row in range(rows):
                for column in range(columns):
                    identity = f"{building['id']}/facade/{wall_index}/{row}/{column}"
                    if identity in excluded:continue
                    if len(placements) >= max_instances:
                        omissions.append({'source_id': building['id'], 'reason': 'instance budget exhausted'})
                        break
                    xy = a[:2] + tangent * (margin + (column + .5) * width_m) + normal * lift_m
                    position = [float(xy[0]), float(xy[1]), float(a[2] + row * height_m)]
                    variant = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:2], 'little') % 2
                    placements.append({'id': identity, 'source_id': building['id'],
                        'module': 'window_small' if variant else 'window_large',
                        'position_m': position, 'yaw_deg': yaw, 'scale': [1., 1., 1.],
                        'tile': f'{math.floor(xy[0] / tile_size_m)}:{math.floor(xy[1] / tile_size_m)}',
                        'cull_distance_m': view_distance_m,
                        'evidence': {'status': 'assumed', 'source': 'licensed modular presentation',
                            'building_source': building.get('metadata', {})}})
                if len(placements) >= max_instances:
                    break
    return {'schema': 'dynacity-building-facade-modules/1', 'placements': placements,
        'selected_buildings': selected, 'omissions': omissions,
        'budget': {'max_instances': max_instances, 'tile_size_m': tile_size_m,
            'view_distance_m': view_distance_m},
        'module_basis': 'X along wall; Y outward; Z up; metres',
        'limits': ['Facade modules and their repetition are illustrative, not surveyed Beirut architecture.',
            'Source building geometry and collision remain unchanged.',
            'No module stretching, mirroring, fabricated roof height or ground height.']}
