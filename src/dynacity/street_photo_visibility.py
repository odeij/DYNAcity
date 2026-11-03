"""Geometric shortlist screening, not automatic building identification."""
from __future__ import annotations

import math

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree


def angle_delta(bearing, heading):
    return (bearing - heading + 180.) % 360. - 180.


def visible_wall_samples(camera_xy, heading, target, blockers, *, half_angle=50.,
                         maximum_distance=80., minimum_distance=3.):
    """Find boundary points in an assumed forward cone with a clear 2D ray.

    Inputs share a metric planar CRS. Heading is clockwise from grid north.
    Blockers include the target itself; an inset ray rejects hidden rear walls.
    This deliberately leaves trees, camera calibration and GPS errors unverified.
    """
    if not math.isfinite(heading) or not 0 < half_angle < 180:
        raise ValueError('Invalid heading or viewing cone')
    point = Point(camera_xy)
    if target.covers(point):
        return {'status': 'camera_inside_source_footprint', 'samples': []}
    if target.distance(point) > maximum_distance:
        return {'status': 'outside_distance_budget', 'samples': []}
    tree = blockers if isinstance(blockers, STRtree) else STRtree(blockers)
    polygons = list(target.geoms) if target.geom_type == 'MultiPolygon' else [target]
    samples = []
    for polygon in polygons:
        coordinates = list(polygon.exterior.coords)
        for a, b in zip(coordinates, coordinates[1:]):
            edge = LineString([a, b])
            if edge.length < 1:
                continue
            for fraction in [.25, .5, .75]:
                endpoint = edge.interpolate(fraction, normalized=True)
                dx, dy = endpoint.x-point.x, endpoint.y-point.y
                distance = math.hypot(dx, dy)
                if not minimum_distance <= distance <= maximum_distance:
                    continue
                bearing = math.degrees(math.atan2(dx, dy)) % 360
                delta = angle_delta(bearing, heading)
                if abs(delta) > half_angle:
                    continue
                # End 20 cm short of wall so the target's near boundary is not
                # counted as an obstruction. Rear walls cross the target interior.
                fraction_short = max(0, 1-.2/distance)
                ray = LineString([point, (point.x+dx*fraction_short, point.y+dy*fraction_short)])
                blocked = False
                for index in tree.query(ray):
                    interior = tree.geometries[index].buffer(-.05)
                    if not interior.is_empty and ray.intersects(interior):
                        blocked = True
                        break
                if blocked:
                    continue
                samples.append({'wall_point_xy_m': [endpoint.x, endpoint.y],
                                'edge_xy_m': [list(a), list(b)],
                                'distance_m': round(distance, 3),
                                'bearing_grid_deg': round(bearing, 3),
                                'heading_offset_deg': round(delta, 3)})
    return {'status': 'geometry_candidate_needs_review' if samples else 'no_clear_wall_in_assumed_cone',
            'samples': samples}
