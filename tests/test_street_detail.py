import copy

import pytest
from shapely.geometry import Polygon, mapping, shape

from dynacity.street_detail import TYPES, digest, enrich, plan


def road(identifier=1, y=0):
    return {'id': identifier, 'source_way': 101+identifier, 'path': [[0,y,0],[20,y,0]],
            'width_m':3.2,'level':0,'name':'Test','tags':{}}


def street():
    return {'id':'test','name':'Test','type':'residential_street','road_ids':[1]}


def test_repeat_is_deterministic_and_source_unchanged():
    roads=[road()]; before=copy.deepcopy(roads)
    a=plan(street(),roads,[],lambda x,y:0)
    assert a==plan(street(),roads,[],lambda x,y:0)
    assert roads==before
    assert a['features'] and not a['checks']['failures']
    assert 'width' in a['inventory'][0]['missing_measurements']
    assert a['inventory'][0]['confidence']=='low'


def test_overrides_survive_and_only_suppress_stable_station():
    base=plan(street(),[road()],[],lambda x,y:0)
    fid=base['features'][0]['id'].rsplit('/',1)[0]
    overrides={'features':{fid:{'disabled':True,'reason':'entrance'}}}
    snapshot=copy.deepcopy(overrides)
    new=plan(street(),[road()],[],lambda x,y:0,overrides=overrides)
    assert overrides==snapshot
    assert new['suppressed']==[fid]
    assert all(not f['id'].startswith(fid+'/') for f in new['features'])
    assert {f['id'] for f in new['features']}=={f['id'] for f in base['features'] if not f['id'].startswith(fid+'/')}


def test_holes_and_buildings_reject_placements_and_report_breaks():
    building={'geometry':mapping(Polygon([(8,1.6),(12,1.6),(12,5),(8,5)]))}
    result=plan(street(),[road()],[building],lambda x,y:None if 14<x<16 else 0)
    assert not result['checks']['failures']
    assert any(e['reason']=='disconnected_sidewalk' for e in result['exceptions'])
    for f in result['features']:
        assert shape(f['geometry']).intersection(shape(building['geometry'])).area<1e-5


def test_intersecting_road_keeps_traffic_clear():
    other=road(2);other['path']=[[10,-20,0],[10,20,0]]
    result=plan(street(),[road(),other],[],lambda x,y:0)
    assert not result['checks']['failures']
    assert any(e['reason']=='disconnected_sidewalk' for e in result['exceptions'])


def test_osm_wins_overture_buildings_ignored_and_ambiguity_rejected():
    r=road();r['tags']={'surface':'asphalt'}
    candidate={'id':'ov1','type':'segment','osm_way_ids':['102'],
               'geometry':{'type':'LineString','coordinates':[[0,0],[20,0]]},
               'attributes':{'surface':'concrete','width':4}}
    tags,sources,_=enrich(r,[candidate,dict(candidate,type='building')])
    assert tags['surface']=='asphalt' and tags['width']==4
    assert sources['width']=='Overture ov1'
    assert 'width' not in enrich(r,[candidate,dict(candidate,id='ov2')])[0]


def test_mapped_absence_and_missing_selected_road():
    r=road();r['tags']['sidewalk']='no'
    assert not plan(street(),[r],[],lambda x,y:0)['features']
    with pytest.raises(ValueError,match='missing'):
        plan(street(),[],[],lambda x,y:0)


def test_sidewalk_does_not_overlap_curb():
    result=plan(street(),[road()],[],lambda x,y:.01*x)
    walks=[shape(f['geometry']) for f in result['features'] if f['kind']=='sidewalk']
    curbs=[shape(f['geometry']) for f in result['features'] if f['kind']=='curb']
    assert max(w.intersection(c).area for w in walks for c in curbs)<1e-8
    assert all(f['width_m']>=.9 for f in result['features'] if f['kind']=='sidewalk')


def test_source_split_continuity_does_not_invent_crossings():
    first=road();first['path']=[[0,0,0],[10,0,0]]
    second=road(2);second['path']=[[10,0,0],[20,0,0]]
    selection={**street(),'road_ids':[1,2]}
    result=plan(selection,[first,second],[],lambda x,y:0)
    assert len(result['junctions'])==2
    assert all(j['status']=='connected_source_split' for j in result['junctions'])
    assert not result['checks']['failures']


def test_bad_rules_and_digest_reject_nonfinite():
    rules=copy.deepcopy(TYPES);rules['residential_street']['sidewalk_m']=float('nan')
    with pytest.raises(ValueError): plan(street(),[road()],[],lambda x,y:0,rules=rules)
    with pytest.raises(ValueError): digest({'x':float('nan')})


def test_batch_cache_invalidates_only_overridden_street(tmp_path):
    import importlib.util
    import json
    from pathlib import Path
    import numpy as np
    from dynacity.terrain import GroundModel
    spec=importlib.util.spec_from_file_location('prepare_detail_test',Path(__file__).parents[1]/'scripts/prepare_street_detail.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def save(name,data):
        path=tmp_path/name;path.write_text(json.dumps(data));return str(path)
    second=road(2,30)
    metadata=save('roads.json',{'coordinates':{'origin_utm_m':[0,0,0]},'roads':[road(),second]})
    checkpoint=save('city.json',{'origin_utm_m':[0,0,0],'packages':[],'roads_metadata':metadata})
    ground=tmp_path/'ground.npz';GroundModel('EPSG:32636',-20,60,1,np.zeros((100,100))).save(ground)
    override=save('overrides.json',{'streets':{},'features':{}})
    other={**street(),'id':'second','road_ids':[2]}
    cfg={'output':str(tmp_path/'output'),'city_checkpoint':checkpoint,'osm_roads':save('osm.json',{'elements':[]}),
         'ground':str(ground),'overrides':override,'streets':[street(),other]}
    config=save('config.json',cfg)
    first=module.run(config);assert first['changed_streets']==['test','second']
    repeat=module.run(config);assert repeat['changed_streets']==[] and len(repeat['cache_hits'])==2
    data={'streets':{'1/left':{'width_m':1.2,'reason':'building clearance'}},'features':{}}
    save('overrides.json',data)
    changed=module.run(config);assert changed['changed_streets']==['test']
    assert 'second' in changed['cache_hits']
    assert json.loads(Path(override).read_text())==data
    assert module.run(config)['changed_streets']==[]
