"""Prepare generic visual references only for explicitly mapped containers."""
import math

def recycling_placements(context):
    if context.get('schema') != 'dynacity-engine-street-context/1': raise ValueError('Expected street context')
    rows=[]; seen=set()
    for p in context.get('points', []):
        tags=p.get('tags', {})
        if p.get('kind')!='recycling' or tags.get('recycling_type')!='container': continue
        source_id=p.get('id',''); position=p.get('position_local_m')
        if not source_id.startswith('node/') or not isinstance(position,list) or len(position)!=3: continue
        if source_id in seen or any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) for v in position):
            raise ValueError('Duplicate or invalid recycling node')
        seen.add(source_id)
        rows.append({'source_id':source_id,'position_local_m':list(position), 'production_area':p.get('production_area'),
                     'source_position_lonlat':p.get('source_position_lonlat'),'elevation_source':p.get('elevation_source'),
                     'as_of':p.get('as_of'),'retrieved_on':p.get('retrieved_on'),'tags':tags,
                     'orientation_source':'unknown; any display rotation illustrative',
                     'appearance_source':'Generic recycling station; size and appearance unverified'})
    return {'schema':'dynacity-mapped-recycling/1','origin_utm_m':context.get('origin_utm_m'),'placements':rows}
