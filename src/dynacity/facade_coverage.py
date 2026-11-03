"""Complete wall grids with bounded fitting and whole-building admission."""
import hashlib
import math
import numpy as np
from dynacity.building_facade_modules import source_walls


def fitted_building(building, panel_m=3., scale_bounds=(.70, 1.40), tile_m=250):
    vertices = np.asarray(building['vertices'], dtype=float)
    center = (vertices.min(axis=0)+vertices.max(axis=0))/2
    tile = f'{math.floor(center[0]/tile_m)}:{math.floor(center[1]/tile_m)}'
    placements, coverage = [], []
    for index, wall in enumerate(source_walls(vertices, building['triangles'])):
        a, b = np.asarray(wall['a']), np.asarray(wall['b'])
        normal = np.asarray(wall['normal'])[:2]
        if np.dot(normal, (a[:2]+b[:2])/2-center[:2]) < 0: normal *= -1
        tangent = np.array([normal[1], -normal[0]])
        if np.dot(b[:2]-a[:2], tangent) < 0: a, b = b, a
        length = float(np.linalg.norm(b[:2]-a[:2])); height = wall['height_m']
        def fitted_count(span):
            candidates={max(1,math.floor(span/panel_m)),max(1,math.ceil(span/panel_m))}
            valid=[n for n in candidates if scale_bounds[0]<=span/n/panel_m<=scale_bounds[1]]
            if not valid:raise ValueError('No bounded panel count for source wall')
            return min(valid,key=lambda n:(abs(span/n/panel_m-1),n))
        columns,rows=fitted_count(length),fitted_count(height)
        width, step = length/columns, height/rows
        sx, sz = width/panel_m, step/panel_m
        if not all(scale_bounds[0] <= value <= scale_bounds[1] for value in (sx, sz)):
            raise ValueError(f'Wall requires excessive library-piece fitting: {building["id"]}, {length}, {height}, {sx}, {sz}')
        coverage.append({'wall':index,'height_m':height,'rows':rows,'columns':columns,
                         'residual_height_m':height-rows*step,'residual_width_m':length-columns*width,
                         'area_m2':length*height,'scale':[sx,1.,sz]})
        for row in range(rows):
            for column in range(columns):
                identity = f"{building['id']}/fitted/{index}/{row}/{column}"
                xy = a[:2]+tangent*((column+.5)*width)+normal*.28
                variant = hashlib.sha256(identity.encode()).digest()[0] % 2
                placements.append({'id':identity,'source_id':building['id'],
                    'module':'window_small' if variant else 'window_large',
                    'position_m':[float(xy[0]),float(xy[1]),float(a[2]+row*step)],
                    'yaw_deg':math.degrees(math.atan2(tangent[1],tangent[0])),
                    'scale':[sx,1.,sz], 'tile':tile, 'cull_distance_m':180,
                    'evidence':{'status':'assumed','source':'fitted licensed library kit; not observed architecture'}})
    return {'source_id':building['id'],'placements':placements,'wall_coverage':coverage,
            'limits':'Only vertical source wall edges >=3.6 m and height >=2.7 m are eligible; small facets use the existing base material.'}


def admit_buildings(buildings, max_instances):
    """Never admit a partial building when its complete grid exceeds the budget."""
    placements, coverage, deferred = [], [], []
    for building in sorted(buildings, key=lambda b:b['id']):
        plan = fitted_building(building)
        if len(placements)+len(plan['placements']) > max_instances:
            deferred.append({'source_id':building['id'],'reason':'whole building exceeds remaining instance budget'})
            continue
        placements.extend(plan['placements']);coverage.append({k:v for k,v in plan.items() if k!='placements'})
    return placements, coverage, deferred
