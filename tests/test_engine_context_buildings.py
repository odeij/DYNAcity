import json

import numpy as np
from shapely.geometry import box

from dynacity.beirut001_pack import Beirut001Pack
from dynacity.engine_context_buildings import build_context_buildings, building_height, select_footprints
from dynacity.engine_city_batches import Glb
from dynacity.terrain import GroundModel


def pack_with(buildings, heights, ground_m=10.0):
    n = len(buildings)
    ground = GroundModel(crs="EPSG:32636", x0=0.0, y0=1000.0, cell_m=10.0, ground=np.full((100, 100), ground_m))
    return Beirut001Pack(meta={}, building_ids=np.array([str(i) for i in range(n)]), buildings=buildings,
                         base_z=np.full(n, ground_m - 5), top_z=ground_m + np.asarray(heights, float),
                         ground_z=np.full(n, ground_m), roof_levels=np.ones(n), bbed_objectid=np.arange(n),
                         link_iou=np.ones(n), link_distance_m=np.zeros(n), bridges=[], bridge_z_min=np.zeros(0),
                         bridge_z_max=np.zeros(0), ground=ground)


def row(geometry, source='OpenStreetMap', identifier='a', props=None):
    return {'geometry': geometry, 'props': props or {}, 'tags': {}, 'source': source,
            'record_id': identifier, 'overture_id': identifier}


def test_footprints_overlapping_beirut001_are_dropped_and_others_kept_unchanged():
    pack = pack_with([box(100, 100, 120, 120)], [20])
    rows = [row(box(102, 102, 118, 118), identifier='dup'), row(box(300, 300, 320, 315), identifier='new'),
            row(box(500, 500, 501, 501), identifier='tiny')]
    kept, skipped = select_footprints(rows, pack)
    assert [r['record_id'] for r in kept] == ['new']
    assert kept[0]['geometry'].equals(box(300, 300, 320, 315))
    assert skipped == {'overlaps a Beirut 001 building': 1, 'footprint under 8 m2': 1}


def test_mapped_heights_win_and_missing_heights_are_labelled_estimates():
    medians = [5, 6, 7, 8, 9, 10, 11]
    assert building_height({'height': 31}, {}, 60, medians) == (31.0, 'mapped height')
    assert building_height({'num_floors': 5}, {}, 60, medians) == (16.0, 'mapped floor count x 3.2 m')
    height, source = building_height({}, {}, 60, medians)
    assert height == 6 and source.startswith('estimated')


def test_packages_sit_on_the_ground_and_keep_source_identity(tmp_path):
    pack = pack_with([box(100, 100, 120, 120), box(140, 100, 150, 110)], [20, 9])
    ground = GroundModel(crs="EPSG:32636", x0=0.0, y0=1000.0, cell_m=10.0, ground=np.full((100, 100), 30.0))
    rows = [row(box(300, 300, 320, 315), source='Microsoft ML Buildings', identifier='ml-1', props={'height': 12})]
    result = build_context_buildings(rows, pack, ground, [0.0, 0.0, 0.0], tmp_path)
    assert result['counts']['exported'] == 1 and result['counts']['height_source'] == {'mapped height': 1}
    job = result['jobs'][0]
    assert job['kind'] == 'buildings' and job['object_count'] == 1
    index = json.loads(open(job['metadata'], encoding='utf-8').read())
    obj = index['objects'][0]
    assert obj['source_id'] == 'context:ml-1' and obj['metadata']['source'] == 'Microsoft ML Buildings'
    low, high = obj['local_bounds_m']
    assert low[2] == 29.0 and high[2] == 42.0  # base sunk 1 m below ground; roof = ground + mapped height
    assert all(node['name'].startswith('buildings_context_') for node in Glb(job['asset']).doc['nodes'])


def test_web_companion_gives_osm_heights_by_id_and_adds_only_non_osm_footprints():
    from dynacity.engine_context_buildings import osm_identifier, web_buildings
    pack = pack_with([box(100, 100, 120, 120)], [20])
    lonlat = {'type': 'Polygon', 'coordinates': [[[35.5, 33.9], [35.5001, 33.9], [35.5001, 33.9001], [35.5, 33.9]]]}
    rows = [dict(row(box(102, 102, 118, 118), identifier='o1'), record_id='w875@3', lonlat=lonlat),
            dict(row(box(300, 300, 320, 315), source='Microsoft ML Buildings', identifier='ml-1'), lonlat=lonlat)]
    web = web_buildings(rows, pack)
    assert osm_identifier('r12@1') == 'relation/12' and osm_identifier('ms-123') is None
    assert web['osm_heights'] == {'way/875': [20.0, 'estimated: Beirut 001 median for similar footprint area']}
    assert [f['properties']['id'] for f in web['features']] == ['overture/ml-1']
    assert web['features'][0]['properties']['source'] == 'Microsoft ML Buildings'


def test_landscape_takes_estimates_only_where_osm_has_no_mapped_height():
    from dynacity.landscape import apply_context_buildings
    def building(identifier, source):
        return {'type': 'Feature', 'geometry': None, 'properties': {
            'id': identifier, 'context_group': 'buildings', 'kind': 'osm_building', 'height_m': 6, 'height_source': source}}
    payload = {'features': [building('way/1', 'illustrative fallback'), building('way/2', 'OSM height tag; unverified')],
               'counts': {'buildings': 2}}
    extra = {'type': 'Feature', 'geometry': None, 'properties': {'id': 'overture/m', 'context_group': 'buildings'}}
    companion = {'schema': 'dynacity-context-buildings-web/1', 'features': [extra], 'height_bins_m': {}, 'attribution': 'x',
                 'osm_heights': {'way/1': [17.1, 'estimated'], 'way/2': [25.6, 'estimated']}}
    out = apply_context_buildings(payload, companion)
    assert [f['properties']['height_m'] for f in out['features'][:2]] == [17.1, 6]
    assert out['counts']['buildings'] == 3 and out['features'][-1] is extra
