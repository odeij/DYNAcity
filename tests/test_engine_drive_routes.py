from types import SimpleNamespace
import math
import numpy as np
import pytest
from shapely.geometry import box

from dynacity.engine_drive_routes import select_starts,unreal_position
from dynacity.terrain import GroundModel


def fixture(*,direction=0,buildings=(),ground=None,width=6.4):
    pack=SimpleNamespace(meta={'crs':'EPSG:32636'},buildings=list(buildings),
        ground=ground or GroundModel('EPSG:32636',0,300,2,np.full((150,150),10.)))
    traffic=SimpleNamespace(crs='EPSG:32636',geometry=[[(20,100),(180,100)]],
        oneway=np.array([direction]),highway=['residential'],way_id=np.array([123]))
    roads=[{'id':0,'path':[[10,80,5.12],[170,80,5.12]],'level':0,
        'height_source':'source ground','width_m':width,'source_way':123,'name':'Source Street'}]
    return pack,traffic,roads,{0:'Area'},{'Area':box(0,0,200,200)},[10,20,5]


def test_right_lane_axis_conversion_and_ground_spawn_height():
    data=select_starts(*fixture())
    start=data['starts'][0]
    assert start['yaw_degrees']==pytest.approx(0)
    assert start['local_position_m'][1]==pytest.approx(78.4)
    assert start['location_cm'][1]==pytest.approx(-7840)
    assert start['location_cm'][2]==pytest.approx(632)
    assert start['surface_local_z_m']==pytest.approx(5.12)
    assert len(start['preview_path_cm'])==14
    assert not data['unavailable_areas']
    assert unreal_position([1,2,3])==[100,-200,300]


def test_reverse_oneway_uses_reverse_heading_and_rightmost_lane():
    start=select_starts(*fixture(direction=-1))['starts'][0]
    assert abs(start['yaw_degrees'])==pytest.approx(180)
    assert start['local_position_m'][1]==pytest.approx(81.6)
    assert start['preview_path_cm'][-1][0]<start['preview_path_cm'][0][0]
    assert start['source_direction']=='reverse drawn geometry'


def test_buildings_and_wrong_area_cannot_produce_a_spawn():
    blocked=select_starts(*fixture(buildings=[box(18,95,182,105)]))
    assert not blocked['starts']
    assert blocked['unavailable_areas'][0]['area']=='Area'
    assert blocked['report']['exclusions']['building footprint conflict']>0
    args=list(fixture());args[4]={'Area':box(200,200,250,250)}
    wrong_area=select_starts(*args)
    assert not wrong_area['starts']
    assert wrong_area['report']['exclusions']['candidate outside named area']>0


def test_unknown_ground_steep_grade_and_inferred_lifts_are_rejected():
    unknown=GroundModel('EPSG:32636',0,300,2,np.full((150,150),np.nan))
    assert not select_starts(*fixture(ground=unknown))['starts']
    z=np.broadcast_to(np.arange(150)[None,:]*1.,(150,150)).copy()
    steep=select_starts(*fixture(ground=GroundModel('EPSG:32636',0,300,2,z)))
    assert not steep['starts']
    assert steep['report']['exclusions']['steep or discontinuous source grade']>0
    args=list(fixture());args[2][0]['height_source']='illustrative ramp/level offset'
    assert not select_starts(*args)['starts']


def test_unknown_direction_and_origin_are_not_silently_inferred():
    with pytest.raises(ValueError,match='direction'):select_starts(*fixture(direction=2))
    args=list(fixture());args[-1]=[0,math.nan,0]
    with pytest.raises(ValueError,match='origin'):select_starts(*args)
    args=list(fixture());args[1].way_id[0]=456
    with pytest.raises(ValueError,match='OSM way'):select_starts(*args)
    args=list(fixture());args[2][0]['path'][0][0]+=1
    with pytest.raises(ValueError,match='endpoints'):select_starts(*args)
