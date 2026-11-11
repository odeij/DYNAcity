"""Derive finished-model placements strictly along mapped barrier source lines.

These are display segments of mapped geometry, not independently surveyed
barrier units. No placement is made for unknown or incomplete ground geometry.
"""
from __future__ import annotations

import math


def barrier_placements(context, model_length_m):
    if not math.isfinite(model_length_m) or model_length_m <= 0:
        raise ValueError('A positive finished model length is required')
    records=[]; omitted=[]
    for line in context.get('lines', []):
        if line.get('kind') != 'jersey_barrier':
            continue
        points=line.get('points_local_m') or []
        if not line.get('render_geometry', True) or len(points)<2:
            omitted.append({'source_id':line['id'],'reason':'no usable mapped ground line'})
            continue
        for segment,(a,b) in enumerate(zip(points,points[1:])):
            if len(a)!=3 or len(b)!=3 or not all(math.isfinite(v) for v in (*a,*b)):
                raise ValueError('Barrier line contains invalid coordinates')
            dx,dy=b[0]-a[0],b[1]-a[1]
            length=math.hypot(dx,dy)
            if length<1e-6:continue
            count=max(1,math.ceil(length/model_length_m))
            for index in range(count):
                fraction=(index+.5)/count
                records.append({'source_id':line['id'],
                    'source_instance_id':line.get('instance_id',line['id']),
                    'display_segment':segment,'display_part':index,
                    'production_area':line.get('production_area','Unassigned'),
                    'position_local_m':[a[i]+(b[i]-a[i])*fraction for i in range(3)],
                    'heading_degrees':math.degrees(math.atan2(dy,dx))%360,
                    'scale_x':length/count/model_length_m,
                    'placement_source':'Subdivided mapped OSM barrier line; illustrative units',
                    'source_as_of':line.get('as_of'),'retrieved_on':line.get('retrieved_on')})
    return {'schema':'dynacity-mapped-barrier-placements/1',
            'origin_utm_m':context.get('origin_utm_m'),
            'asset_key':'concrete_road_barrier','model_length_m':model_length_m,
            'placements':records,'omitted':omitted}
