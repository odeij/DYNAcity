from dynacity.osm_geometry import osm_geometry
from dynacity.landscape import landscape_payload


def point(x,y):
    return {'lon':x,'lat':y}


def test_multipolygon_joins_reversed_pieces_and_preserves_holes():
    outer=[point(35,33),point(36,33),point(36,34),point(35,34),point(35,33)]
    hole=[point(35.2,33.2),point(35.4,33.2),point(35.4,33.4),point(35.2,33.2)]
    relation={'type':'relation','id':1,'tags':{'type':'multipolygon','leisure':'park'},'members':[
        {'type':'way','role':'outer','geometry':outer[:3]},
        {'type':'way','role':'inner','geometry':hole},
        {'type':'way','role':'outer','geometry':list(reversed(outer[2:]))},
    ]}
    geometry=osm_geometry(relation,{}, {})
    assert geometry['type']=='MultiPolygon'
    assert len(geometry['coordinates'][0])==2
    assert geometry['coordinates'][0][0][0]==geometry['coordinates'][0][0][-1]
    payload=landscape_payload({'elements':[relation]})
    assert payload['features'][0]['geometry']==geometry
    relation['members'].pop()
    assert osm_geometry(relation,{}, {}) is None


def test_city_context_retains_paths_stairs_boundaries_places_and_furniture():
    elements=[
        {'type':'way','id':1,'geometry':[point(35.5,33.9),point(35.501,33.9)],'tags':{'highway':'steps','step_count':'12','width':'2 m','wheelchair':'no'}},
        {'type':'way','id':2,'geometry':[point(35.5,33.9),point(35.501,33.9)],'tags':{'barrier':'wall','height':'2.5'}},
        {'type':'node','id':3,**point(35.5,33.9),'tags':{'highway':'street_lamp','height':'7'}},
        {'type':'node','id':4,**point(35.5,33.9),'tags':{'amenity':'cafe','name':'Local cafe','opening_hours':'08:00-17:00'}},
        {'type':'way','id':5,'geometry':[point(35.5,33.9),point(35.501,33.9)],'tags':{'highway':'residential','sidewalk':'no','surface':'concrete'}},
    ]
    result=landscape_payload({'elements':elements,'retrieved_on':'2026-10-24','query_bbox':[35,33,36,34]})
    by_id={f['properties']['id']:f for f in result['features']}
    assert len(by_id)==5
    assert by_id['way/1']['properties']['width_m']==2
    assert by_id['way/1']['properties']['tags']['step_count']=='12'
    assert by_id['way/2']['properties']['height_m']==2.5
    assert by_id['node/3']['properties']['kind']=='street_lamp'
    assert by_id['node/4']['properties']['tags']['opening_hours']=='08:00-17:00'
    assert by_id['way/5']['properties']['context_group']=='roads'
    assert result['counts']=={'paths':1,'boundaries':1,'furniture':1,'places':1,'roads':1}
    assert result['tree_nodes']==0


def test_building_height_provenance_and_invalid_geometry_are_explicit():
    ring=[point(35.5,33.9),point(35.501,33.9),point(35.501,33.901),point(35.5,33.9)]
    result=landscape_payload({'elements':[
        {'type':'way','id':1,'geometry':ring,'tags':{'building':'yes','building:levels':'4','roof:shape':'flat'}},
        {'type':'way','id':2,'geometry':ring,'tags':{'building':'yes','height':'nan'}},
        {'type':'way','id':3,'nodes':[99,100],'tags':{'highway':'footway'}},
    ]})
    first,second=result['features']
    assert first['properties']['height_m']==12.0
    assert 'floors' in first['properties']['height_source']
    assert first['properties']['height_kind']=='floors' and first['properties']['estimated'] is True
    assert second['properties']['height_m']==6
    assert second['properties']['height_source']=='illustrative fallback'
    assert second['properties']['height_kind']=='fallback'
    assert result['geometry_rejected']=={'paths':1}


def test_display_geometry_never_closes_a_missing_osm_way():
    relation={'type':'relation','tags':{'type':'multipolygon'},'members':[
        {'type':'way','role':'outer','ref':1}]}
    assert osm_geometry(relation,{}, {}) is None


def test_infrastructure_records_retain_type_geometry_and_tags_without_inventing_props():
    elements=[
        {'type':'node','id':10,**point(35.5,33.9),'tags':{'emergency':'fire_hydrant','fire_hydrant:type':'pillar'}},
        {'type':'node','id':11,**point(35.5,33.9),'tags':{'man_made':'street_cabinet','utility':'power'}},
        {'type':'node','id':12,**point(35.5,33.9),'tags':{'traffic_calming':'bump'}},
        {'type':'node','id':13,**point(35.5,33.9),'tags':{'highway':'kerb','kerb':'lowered'}},
        {'type':'way','id':14,'geometry':[point(35.5,33.9),point(35.501,33.9)],
         'tags':{'highway':'residential','traffic_calming':'bump'}},
    ]
    result=landscape_payload({'elements':elements,'retrieved_on':'2026-10-27'})
    by_id={f['properties']['id']:f for f in result['features']}
    assert [by_id[f'node/{i}']['properties']['kind'] for i in (10,11,12,13)] == [
        'fire_hydrant','man_made_street_cabinet','traffic_calming','kerb']
    assert by_id['node/10']['properties']['tags']['fire_hydrant:type']=='pillar'
    assert by_id['node/11']['properties']['tags']['utility']=='power'
    assert by_id['way/14']['properties']['context_group']=='roads'
    assert by_id['way/14']['properties']['tags']['traffic_calming']=='bump'
    assert all(by_id[f'node/{i}']['geometry']['type']=='Point' for i in (10,11,12,13))


def test_infrastructure_tags_do_not_replace_transport_geometry():
    geom=[point(35.5,33.9),point(35.501,33.9)]
    elements=[
        {'type':'way','id':20,'geometry':geom,'tags':{'highway':'residential','power':'line'}},
        {'type':'way','id':21,'geometry':geom,'tags':{'waterway':'drain','man_made':'tunnel'}},
        {'type':'way','id':22,'geometry':geom,'tags':{'railway':'abandoned','advertising':'billboard'}},
    ]
    features=landscape_payload({'elements':elements})['features']
    by_id={f['properties']['id']:f for f in features}
    assert [by_id[f'way/{i}']['properties']['context_group'] for i in (20,21,22)] == ['roads','water','transport']
    assert by_id['way/20']['properties']['tags']['power']=='line'
    assert by_id['way/21']['properties']['tags']['man_made']=='tunnel'
    assert by_id['way/22']['properties']['tags']['advertising']=='billboard'
