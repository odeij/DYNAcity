"""Prepare source-linked power tower positions without inventing measured sizes."""
import math


def power_tower_placements(context):
    positions=[]; skipped=[]; ids=set()
    for row in context.get('points',[]):
        if row.get('kind')!='power_tower':
            continue
        source=row.get('id'); point=row.get('position_local_m',[])
        if not isinstance(source,str) or not source.startswith('node/') or source in ids:
            raise ValueError('Power towers require unique mapped node IDs')
        ids.add(source)
        if len(point)!=3 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in point):
            skipped.append({'source_id':source,'reason':'no finite ground position'});continue
        positions.append({'source_id':source,'position_local_m':point,'source_position_lonlat':row.get('source_position_lonlat'),
                          'production_area':row.get('production_area','Unassigned'), 'tags':row.get('tags',{}),
                          'as_of':row.get('as_of'),'elevation_source':row.get('elevation_source'),
                          'heading_degrees':0,'heading_source':'illustrative; tower orientation unknown',
                          'height_source':'20.06 m generic model; mapped height unknown'})
    return {'schema':'dynacity-mapped-power-towers/1','origin_utm_m':context.get('origin_utm_m'),
            'placements':positions,'skipped':skipped}
