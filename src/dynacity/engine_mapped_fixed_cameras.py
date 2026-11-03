"""Prepare fixed surveillance cameras mapped on poles, with explicit bearing."""
import math

def fixed_camera_placements(context):
    rows=[]; skipped=[]; ids=set()
    for source in context.get('points',[]):
        tags=source.get('tags',{})
        if source.get('kind')!='man_made_surveillance' or tags.get('camera:mount')!='pole' or tags.get('camera:type')!='fixed':continue
        sid=source.get('id');position=source.get('position_local_m',[])
        if not isinstance(sid,str) or not sid.startswith('node/') or sid in ids:raise ValueError('Unique mapped node required')
        ids.add(sid)
        if len(position)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in position):
            skipped.append({'source_id':sid,'reason':'missing ground coordinates'});continue
        try:
            bearing=float(tags.get('camera:direction',''))
            if not math.isfinite(bearing) or not 0<=bearing<=360:raise ValueError()
        except (TypeError,ValueError):
            skipped.append({'source_id':sid,'reason':'missing numeric camera direction'});continue
        rows.append({'source_id':sid,'position_local_m':position,'source_position_lonlat':source.get('source_position_lonlat'),
            'production_area':source.get('production_area','Unassigned'),'heading_degrees':(90-bearing)%360,
            'heading_source':'OSM camera:direction bearing converted from north clockwise',
            'elevation_source':source.get('elevation_source'),'as_of':source.get('as_of'),'tags':tags,
            'height_source':'generic 4.42 m model, physical dimensions unverified'})
    return {'schema':'dynacity-mapped-fixed-cameras/1','origin_utm_m':context.get('origin_utm_m'),
            'placements':rows,'skipped':skipped,'model_facing_blender':'-Y; glTF +Z'}
