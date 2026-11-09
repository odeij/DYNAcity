import pytest
from dynacity.engine_mapped_cameras import camera_placements

def test_only_explicit_dome_pole_nodes_and_osm_bearing():
    point={'id':'node/1','kind':'man_made_surveillance','position_local_m':[1,2,3],
           'tags':{'camera:type':'dome','camera:mount':'pole','camera:direction':'213'}}
    context={'schema':'dynacity-engine-street-context/1','points':[point, {**point,'id':'node/2','tags':{'camera:type':'fixed','camera:mount':'pole'}}]}
    rows=camera_placements(context)['placements']
    assert len(rows)==1 and rows[0]['source_id']=='node/1'
    assert rows[0]['camera_bearing_degrees_clockwise_from_north']==213
    assert rows[0]['position_local_m']==[1,2,3]

def test_invalid_coordinates_rejected_and_missing_direction_unknown():
    p={'id':'node/1','kind':'man_made_surveillance','position_local_m':[1,2,3], 'tags':{'camera:type':'dome','camera:mount':'pole'}}
    ctx={'schema':'dynacity-engine-street-context/1','points':[p]}
    assert camera_placements(ctx)['placements'][0]['camera_bearing_degrees_clockwise_from_north'] is None
    p['position_local_m']=[1,2,float('nan')]
    with pytest.raises(ValueError): camera_placements(ctx)
