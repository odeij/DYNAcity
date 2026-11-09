import copy

import pytest

from dynacity.viewer import utm_to_lonlat
from dynacity.viewer_pedestrians import pedestrian_payload


def walkers():
    return {'schema': 'dynacity-pedestrian-walkers/2', 'origin_utm_m': [730000, 3750000, 50],
            'walk_speed_ms': 1.3, 'baseline_removed_per_hour': 71.3925,
            'status': 'modelled, not observed', 'source': 'OSM contributors',
            'segments': [{'path': [[0, 0, -10], [100, 0, -9]], 'people': 3.5,
                          'offset_m': 5, 'pavement_m': 2.4, 'ped_per_hour_pm': 200}]}


def test_shared_origin_and_elevation_are_converted_without_engine_index_assumptions():
    doc = walkers()
    original = copy.deepcopy(doc)
    start = utm_to_lonlat(730000, 3750000, 36)
    end = utm_to_lonlat(730100, 3750000, 36)
    result = pedestrian_payload(doc, {'roads': [{'path': [start, end], 'lvl': 0}]})
    s = result['segments'][0]
    assert s['path'][0] == [*start, 40]
    assert s['road_index'] == 0
    assert s['people'] == 3.5
    assert doc == original


def test_unmatched_or_bridge_only_roads_do_not_gain_false_associations():
    doc = walkers()
    assert pedestrian_payload(doc)['segments'][0]['road_index'] is None
    roads = [{'path': [[35, 33], [35.01, 33]], 'lvl': 0}]
    assert pedestrian_payload(doc, {'roads': roads})['segments'][0]['road_index'] is None


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1])
def test_invalid_flow_is_rejected(value):
    doc = walkers()
    doc['segments'][0]['people'] = value
    with pytest.raises(ValueError):
        pedestrian_payload(doc)


def test_overlay_merges_identity_and_retains_capture_provenance(tmp_path):
    import json
    from dynacity.cli import _landscape_view
    first = tmp_path/'base.json'
    second = tmp_path/'extra.json'
    tree = {'type': 'node', 'id': 1, 'lon': 35.5, 'lat': 33.9, 'tags': {'natural': 'tree'}}
    first.write_text(json.dumps({'elements': [tree]}), encoding='utf-8')
    second.write_text(json.dumps({'retrieved_on': '2026-10-31', 'osm3s': {'timestamp_osm_base': '2026-10-30'},
                                  'elements': [{**tree, 'lat': 33.91}]}), encoding='utf-8')
    result = _landscape_view(str(first), overlays=[str(second)])
    features = [f for f in result['features'] if f['properties'].get('id') == 'node/1']
    assert len(features) == 1
    assert features[0]['geometry']['coordinates'][1] == 33.91
    assert features[0]['properties']['retrieved_on'] == '2026-10-31'


def test_untagged_osm_dependency_does_not_erase_existing_mapped_object(tmp_path):
    import json
    from dynacity.cli import _landscape_view
    first, second = tmp_path/'base.json', tmp_path/'overlay.json'
    tree = {'type': 'node', 'id': 1, 'lon': 35.5, 'lat': 33.9, 'tags': {'natural': 'tree'}}
    first.write_text(json.dumps({'elements': [tree]}), encoding='utf-8')
    second.write_text(json.dumps({'elements': [{k: v for k, v in tree.items() if k != 'tags'}]}), encoding='utf-8')
    result = _landscape_view(str(first), overlays=[str(second)])
    assert any(f['properties']['kind'] == 'tree' for f in result['features'])
