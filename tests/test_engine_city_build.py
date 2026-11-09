from types import SimpleNamespace
import json
import numpy as np
from shapely.geometry import box
from dynacity.engine_city_build import build_packages


def test_incremental_packages_keep_coordinates_and_omit_duplicate_calibration(tmp_path):
    pack=SimpleNamespace(building_ids=['a'],buildings=[box(100,200,110,210)],
                         ground_z=np.array([5.]),top_z=np.array([20.]),
                         bbed_objectid=np.array([-1]),roof_levels=np.array([1]))
    manifest={'groups':{'features':[{'properties':{'name':'Area','content_hash':'source-hash'}}]},
              'buildings':[{'id':'a','owner':'Area'}]}
    first=build_packages(pack,manifest,[100,200,5],tmp_path)
    assert not first['packages'][0]['cache_hit']
    assert first['packages'][0]['exported_buildings']==1
    import struct
    data=open(first['packages'][0]['asset'],'rb').read()
    n=struct.unpack_from('<I',data,12)[0];doc=json.loads(data[20:20+n])
    assert [n['name'] for n in doc['nodes']]==['beirut001_a']
    mesh=doc['meshes'][0];accessor=doc['accessors'][mesh['primitives'][0]['attributes']['POSITION']]
    assert accessor['max']==[10.,15.,-0.]
    assert build_packages(pack,manifest,[100,200,5],tmp_path)['packages'][0]['cache_hit']
    changed=build_packages(pack,manifest,[101,200,5],tmp_path)
    assert not changed['packages'][0]['cache_hit']
    assert changed['packages'][0]['asset']!=first['packages'][0]['asset']


def test_buildings_outside_the_model_ground_use_the_merged_ground():
    from dynacity.engine_city_build import build_group_scene
    from dynacity.terrain import GroundModel
    ground=GroundModel('EPSG:32636',0,100,10,np.full((10,10),12.))
    pack=SimpleNamespace(building_ids=['in','out','off','low'],
        buildings=[box(20,20,30,30),box(40,40,50,50),box(500,500,510,510),box(60,60,70,70)],
        ground_z=np.array([5.,np.nan,np.nan,np.nan]),top_z=np.array([20.,30.,30.,12.5]),
        bbed_objectid=np.array([-1]*4),roof_levels=np.array([1]*4))
    plain=build_group_scene(pack,['in','out','off','low'],[0,0,0],'A')
    assert [m['id'] for m in plain['meshes']]==['beirut001:in']
    scene=build_group_scene(pack,['in','out','off','low'],[0,0,0],'A',ground)
    by={m['id']:m for m in scene['meshes']}
    assert set(by)=={'beirut001:in','beirut001:out'}
    assert by['beirut001:in']['metadata']['ground_source']=='pack' and by['beirut001:in']['metadata']['height_m']==15
    assert by['beirut001:out']['metadata']['ground_source']=='glo30' and by['beirut001:out']['metadata']['height_m']==18
    assert np.asarray(by['beirut001:out']['vertices'])[:,2].min()==12.
    assert {s['id']:s['reason'] for s in scene['skipped']}=={'off':'missing source ground/roof','low':'estimated ground not below roof'}
