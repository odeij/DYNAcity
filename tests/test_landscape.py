from dynacity.landscape import landscape_payload


def test_only_mapped_trees_and_closed_supported_areas_are_rendered():
    ring = [{'lon':35.5,'lat':33.9},{'lon':35.501,'lat':33.9},
            {'lon':35.501,'lat':33.901},{'lon':35.5,'lat':33.9}]
    data = {'osm3s':{'timestamp_osm_base':'2026-10-23T00:00:00Z'},'elements':[
        {'type':'node','id':1,'lon':35.5,'lat':33.9,'tags':{'natural':'tree'}},
        {'type':'node','id':2,'lon':35.501,'lat':33.9,'tags':{'natural':'tree','height':'9'}},
        {'type':'way','id':3,'geometry':ring,'tags':{'leisure':'park'}},
        {'type':'way','id':4,'geometry':ring[:-1],'tags':{'landuse':'grass'}},
        {'type':'way','id':5,'geometry':ring,'tags':{'building':'yes'}},
    ]}
    payload=landscape_payload(data)
    assert len(payload['features']) == 5
    a,b,park,reference,building=payload['features']
    assert reference['properties']['kind']=='greenery_reference'
    assert reference['geometry']['type']=='LineString'
    assert reference['properties']['representation']=='reference outline only'
    assert a['properties']['height_m'] == 6 and a['properties']['height_source'] == 'illustrative'
    assert b['properties']['height_m'] == 9 and b['properties']['height_source'] == 'OSM tag; unverified'
    assert park['properties']['kind'] == 'park'
    assert building['properties']['kind'] == 'osm_building'
    assert payload['as_of'] == data['osm3s']['timestamp_osm_base']


def test_osm_api_ways_resolve_nodes_without_inventing_missing_geometry():
    elements=[{'type':'node','id':1,'lon':35.5,'lat':33.9},
              {'type':'node','id':2,'lon':35.501,'lat':33.9},
              {'type':'node','id':3,'lon':35.5,'lat':33.901},
              {'type':'way','id':4,'nodes':[1,2,3,1],'tags':{'leisure':'garden'}},
              {'type':'way','id':5,'nodes':[1,2,99,1],'tags':{'leisure':'garden'}}]
    payload=landscape_payload({'elements':elements})
    assert len(payload['features']) == 1
    assert payload['features'][0]['properties']['id'] == 'way/4'


def test_tree_rows_follow_source_line_and_do_not_duplicate_mapped_trunks():
    osm={'elements':[
        {'type':'node','id':1,'lon':35.5,'lat':33.9,'tags':{'natural':'tree'}},
        {'type':'way','id':2,'geometry':[{'lon':35.5,'lat':33.9},{'lon':35.5,'lat':33.9003}],
         'tags':{'natural':'tree_row','height':'10'}},
    ]}
    result=landscape_payload(osm)
    assert result['tree_nodes']==1 and result['tree_rows']==1
    row=[f for f in result['features'] if f['properties']['id']=='way/2']
    assert len(row)>=3
    assert all(f['geometry']['coordinates'][0]==35.5 for f in row)
    assert all(33.9<f['geometry']['coordinates'][1]<=33.9003 for f in row)
    assert all(f['properties']['height_m']==10 for f in row)
    assert all('Illustrative spacing' in f['properties']['position_source'] for f in row)
    assert len({f['properties']['instance_id'] for f in row})==len(row)


def test_incomplete_or_nonfinite_tree_rows_are_not_invented():
    osm={'elements':[
        {'type':'node','id':1,'lon':35.5,'lat':33.9},
        {'type':'way','id':2,'nodes':[1,99],'tags':{'natural':'tree_row'}},
        {'type':'way','id':3,'geometry':[{'lon':35.5,'lat':33.9},{'lon':float('nan'),'lat':33.91}], 'tags':{'natural':'tree_row'}},
    ]}
    assert not landscape_payload(osm)['features']


def test_point_public_spaces_keep_location_without_inventing_area():
    osm={'retrieved_on':'2026-10-27','elements':[
        {'type':'node','id':42,'lon':35.5,'lat':33.9,'tags':{'leisure':'playground','name':'Play area'}},
        {'type':'node','id':43,'lon':35.501,'lat':33.9,'tags':{'leisure':'pitch','sport':'football'}},
    ]}
    result=landscape_payload(osm)
    assert len(result['features'])==2
    for feature in result['features']:
        assert feature['geometry']['type']=='Point'
        assert feature['properties']['context_group']=='places'
        assert feature['properties']['representation']=='mapped location only; area and equipment unknown'
        assert feature['properties']['retrieved_on']=='2026-10-27'
    assert result['features'][1]['properties']['tags']['sport']=='football'
