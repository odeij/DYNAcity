from types import SimpleNamespace
import numpy as np
import pytest
from shapely.geometry import Polygon, box

from dynacity.engine_scene import polygon_mesh, build_scene
from dynacity.terrain import GroundModel
from dynacity.viewer import lonlat_to_utm


def test_extrusion_keeps_courtyard_and_upward_roof():
    polygon=Polygon([(0,0),(10,0),(10,10),(0,10)],holes=[[(3,3),(7,3),(7,7),(3,7)]])
    vertices,faces=polygon_mesh(polygon,lambda x,y:12,bottom=lambda x,y:2)
    roof_area=0
    for face in faces:
        v=np.array([vertices[i] for i in face])
        if np.all(v[:,2]==12):
            cross=np.cross(v[1]-v[0],v[2]-v[0])
            assert cross[2]>0
            roof_area+=cross[2]/2
            assert polygon.covers(Polygon(v[:,:2]))
    assert roof_area==pytest.approx(84)


def test_local_scene_preserves_source_height_ids_and_ground():
    x,y=lonlat_to_utm(35.49525,33.87908,36,True)
    ground=GroundModel('EPSG:32636',x-200,y+200,10,np.full((40,40),30.0))
    pack=SimpleNamespace(meta={'crs':'EPSG:32636'},ground=ground,
        buildings=[box(x-5,y-5,x+5,y+5)],ground_z=np.array([30]),top_z=np.array([50]),
        building_ids=['original-uuid'],bbed_objectid=[123],roof_levels=[1],
        bridges=[],heights=lambda:np.array([20]))
    traffic=SimpleNamespace(crs='EPSG:32636',geometry=[[(x-40,y),(x+40,y)]],
        lanes=np.array([1]),oneway=np.array([0]),highway=['residential'],level=None,
        u=[1],v=[2],way_id=[456],name=['Example'])
    scene=build_scene(pack,traffic,{'features':[]},longitude=35.49525,latitude=33.87908,radius_m=100)
    building=next(m for m in scene['meshes'] if m['kind']=='building')
    assert building['id']=='beirut001:original-uuid'
    assert building['metadata']['bbed_objectid']==123
    assert max(v[2] for v in building['vertices'])==20
    assert min(v[2] for v in building['vertices'])==0
    assert scene['roads'][0]['source_way']==456
    assert scene['roads'][0]['path'][0][2]==pytest.approx(0.12)
    assert np.array_equal(pack.top_z,[50])


def test_missing_origin_ground_does_not_turn_into_zero_elevation():
    pack=SimpleNamespace(meta={'crs':'EPSG:32636'},ground=None)
    with pytest.raises(ValueError,match='ground grid'):
        build_scene(pack,SimpleNamespace(crs='EPSG:32636'),{},longitude=35.5,latitude=33.9)
