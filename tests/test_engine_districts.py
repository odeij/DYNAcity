from types import SimpleNamespace
import numpy as np
from shapely.geometry import box
from dynacity.engine_districts import assign_buildings


def test_whole_building_gets_one_owner_and_crossings_remain_visible():
    pack=SimpleNamespace(building_ids=['crossing','outside'],buildings=[box(8,0,14,5),box(30,0,32,2)],
                         ground_z=np.array([2,3]),top_z=np.array([10,20]))
    groups=[{'name':'West','geometry':box(0,0,10,10)},{'name':'East','geometry':box(10,0,20,10)}]
    records,members=assign_buildings(pack,groups)
    assert records[0]['owner']=='East'
    assert set(records[0]['intersects'])=={'West','East'}
    assert records[1]['owner'] is None
    assert pack.buildings[0].area==30
    assert len(members['East'])==1


def test_hashes_are_stable_and_detect_height_changes():
    pack=SimpleNamespace(building_ids=['a'],buildings=[box(0,0,2,2)],ground_z=np.array([1.]),top_z=np.array([10.]))
    groups=[{'name':'A','geometry':box(-1,-1,3,3)}]
    first=assign_buildings(pack,groups)[0][0]['content_hash']
    assert assign_buildings(pack,groups)[0][0]['content_hash']==first
    pack.top_z[0]=12
    assert assign_buildings(pack,groups)[0][0]['content_hash']!=first


def test_height_edit_only_marks_owning_area_for_rebuild():
    from pyproj import Transformer
    from shapely.geometry import mapping
    from dynacity.engine_districts import build_manifest,change_summary
    projection=Transformer.from_crs(4326,32636,always_xy=True)
    points=[projection.transform(x,33.9) for x in [35.45,35.55]]
    pack=SimpleNamespace(meta={'crs':'EPSG:32636'},ground=None,building_ids=['a','b'],
        buildings=[box(x-5,y-5,x+5,y+5) for x,y in points],ground_z=np.array([1.,1.]),top_z=np.array([10.,10.]))
    sectors={'features':[{'properties':{'OBJECTID_1':i,'CadastralN':name,'ZoneName':name},
                          'geometry':mapping(box(w,33.8,e,34.0))}
                         for i,name,w,e in [(1,'A',35.4,35.5),(2,'B',35.5,35.6)]]}
    before=build_manifest(pack,sectors);pack.top_z[0]=15
    after=build_manifest(pack,sectors)
    assert change_summary(before,after)=={'rebuild':['A'],'removed':[],'unchanged':['B']}
