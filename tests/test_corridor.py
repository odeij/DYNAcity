from dataclasses import replace
from datetime import date
import json

import numpy as np

from dynacity.contracts import CanonicalState, UrbanObjectState, UrbanStateSnapshot
from dynacity.corridor import Corridor, crop_bbed, crop_beirut001, crop_osm, crop_snapshot
from dynacity.mobility_inputs import pilot_network
from dynacity.viewer import utm_to_lonlat
from test_traffic_sumo import network


def lonlat(x, y):
    return list(utm_to_lonlat(x, y, 36, True))


def corridor_file(tmp_path):
    # A 200 m east-west line through the fixture network's first road.
    path = tmp_path / 'line.geojson'
    path.write_text(json.dumps({'type': 'FeatureCollection', 'name': 'Test corridor', 'features': [
        {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'LineString',
         'coordinates': [lonlat(733000, 3750000), lonlat(733200, 3750000)]}}]}))
    return path


def snapshot():
    def building(i, x, y):
        return UrbanObjectState(object_id=f'BULBuildingID:{i}|OBJECTID:{i}', state=CanonicalState.UNKNOWN,
                                centroid_x_m=x, centroid_y_m=y)
    return UrbanStateSnapshot(snapshot_id='s', as_of=date(2024, 1, 1), data_version='t', objects=[
        building(1, 733100, 3750030), building(2, 733100, 3750400),
        UrbanObjectState(object_id='BULBuildingID:3|OBJECTID:3', state=CanonicalState.UNKNOWN)])


def test_corridor_keeps_buildings_inside_its_buffer_only(tmp_path):
    corridor = Corridor.load(corridor_file(tmp_path), 'EPSG:32636', 50)
    assert corridor.name == 'Test corridor'
    kept = crop_snapshot(snapshot(), corridor)
    # Inside the 50 m buffer; 400 m away and centroid-less buildings are left out.
    assert [o.object_id for o in kept.objects] == ['BULBuildingID:1|OBJECTID:1']
    collection = {'features': [{'properties': {'OBJECTID': i, 'BULBuildingID': i}, 'geometry': None} for i in (1, 2)]}
    assert [f['properties']['OBJECTID'] for f in crop_bbed(collection, kept)['features']] == [1]
    w, s, e, n = corridor.bounds()
    assert w < lonlat(733000, 3750000)[0] and e > lonlat(733200, 3750000)[0] and s < n


def test_corridor_crops_osm_and_beirut001_to_its_box(tmp_path):
    bounds = Corridor.load(corridor_file(tmp_path), 'EPSG:32636', 50).bounds()
    near, far = lonlat(733100, 3750000), lonlat(733100, 3752000)
    raw = {'elements': [{'type': 'node', 'id': 1, 'lon': near[0], 'lat': near[1]},
                        {'type': 'node', 'id': 2, 'lon': far[0], 'lat': far[1]},
                        {'type': 'way', 'id': 3, 'bounds': {'minlon': far[0], 'minlat': near[1] - 1e-4,
                                                            'maxlon': far[0] + 1e-4, 'maxlat': far[1]}}]}
    assert [e['id'] for e in crop_osm(raw, bounds)['elements']] == [1, 3]
    payload = {'buildings': [{'parts': [[[near, near, near]]], 'objectid': 7},
                             {'parts': [[[far, far, far]]], 'objectid': 8}],
               'bbed': {'7': {}, '8': {}}}
    cropped = crop_beirut001(payload, bounds)
    assert [b['objectid'] for b in cropped['buildings']] == [7] and list(cropped['bbed']) == ['7']


def test_road_subset_renumbers_bans_crossings_and_levels(network):
    # Road 6 (6→0) and road 0 (0→1) touch the box; the rest start beyond 733150.
    model = replace(network, level=np.arange(7), turn_bans=[(6, 0, 0), (0, 1, 2)],
                    crossings=[(0, 10.0, 'marked'), (3, 5.0, 'signals')])
    a, b = lonlat(732950, 3749990), lonlat(733050, 3750010)
    subset, keep = pilot_network(model, [a[0], a[1], b[0], b[1]], note='Test subset.')
    assert keep == [0, 6]
    np.testing.assert_array_equal(subset.level, [0, 6])
    assert subset.turn_bans == [(1, 0, 0)]  # the ban into road 2 left with road 2
    assert subset.crossings == [(0, 10.0, 'marked')]
    assert subset.source.endswith('Test subset.')
