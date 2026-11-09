from types import SimpleNamespace
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from dynacity.street_twin_pipeline import elevation_overrides, select_roads


def test_elevation_binding_uses_geometry_not_array_index_and_preserves_traffic_xy():
    model = SimpleNamespace(crs='EPSG:32636', way_id=[42], geometry=[[(100., 200.), (110., 200.)]])
    metadata = {'coordinates': {'crs': model.crs, 'units': 'metres', 'origin_utm_m': [100, 200, 50]},
                'roads': [{'id': 999, 'source_way': 42, 'path': [[10, 0, 20], [5, 0, 15], [0, 0, 10]]}]}
    result, report = elevation_overrides(model, metadata, [0])
    assert result[0]['path'] == [[100, 200, 60], [105, 200, 65], [110, 200, 70]]
    assert result[0]['evidence']['elevation']['status'] == 'modelled'
    assert report['matched'] == 1
    metadata['roads'].append(metadata['roads'][0].copy())
    result, report = elevation_overrides(model, metadata, [0])
    assert not result
    assert report['unmatched'][0]['reason'] == 'ambiguous elevation match'


def test_way_match_alone_does_not_accept_wrong_road_or_invent_height():
    model = SimpleNamespace(crs='EPSG:32636', way_id=[42], geometry=[[(100., 200.), (110., 200.)]])
    metadata = {'coordinates': {'crs': model.crs, 'units': 'metres', 'origin_utm_m': [0, 0, 0]},
                'roads': [{'source_way': 42, 'path': [[100, 205, 10], [110, 205, 20]]}]}
    assert elevation_overrides(model, metadata, [0])[0] == {}


def test_dense_source_grade_breaks_survive_sparse_traffic_geometry():
    model = SimpleNamespace(crs='EPSG:32636', way_id=[42], geometry=[[(100., 200.), (110., 200.)]])
    metadata = {'coordinates': {'crs': model.crs, 'units': 'metres', 'origin_utm_m': [100, 200, 50]},
                'roads': [{'source_way': 42, 'height_source': 'source bridge deck', 'level': 1,
                           'path': [[0, 0, 10], [3, 0, 17], [10, 0, 10]]}]}
    result, _ = elevation_overrides(model, metadata, [0])
    assert [103, 200, 67] in result[0]['path']
    assert result[0]['evidence']['elevation']['height_source'] == 'source bridge deck'


def test_selection_keeps_complete_cross_boundary_roads():
    class Model:
        geometry = [[(-10, 0), (10, 0)], [(20, 20), (30, 30)]]
        def __len__(self): return len(self.geometry)
    assert select_roads(Model(), [-1, -1, 1, 1]) == [0]
    with pytest.raises(ValueError):
        select_roads(Model(), [1, 1, -1, -1])


def _script():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'prepare_street_twin.py'
    spec = importlib.util.spec_from_file_location('prepare_street_twin', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_failed_rebuild_invalidates_previous_success(tmp_path, monkeypatch):
    script = _script()
    report = tmp_path / 'build-report.json'
    report.write_text('{"build_status":"complete"}')
    def fail(*args, **kwargs):
        assert json.loads(report.read_text())['build_status'] == 'building'
        raise ValueError('invalid input')
    monkeypatch.setattr(script, '_prepare', fail)
    with pytest.raises(ValueError, match='invalid input'):
        script.prepare('unused', tmp_path)
    assert json.loads(report.read_text())['build_status'] == 'failed'


def test_end_to_end_corridor_keeps_citywide_sumo_and_unknown_accuracy(tmp_path):
    from dynacity.traffic import TrafficModel
    from dynacity.traffic_sumo import runtime
    try:
        runtime()
    except RuntimeError:
        pytest.skip('Optional SUMO runtime unavailable')
    model = TrafficModel(crs='EPSG:32636',
        geometry=[[(733000,3750000),(733100,3750000)], [(733100,3750000),(733200,3750000)]],
        u=np.array([0,1]), v=np.array([1,2]), length_m=np.array([100,100]), oneway=np.array([0,0]),
        highway=['residential']*2, way_id=[42,43], speed_kmh=np.full((2,24),30),
        free_kmh=np.full(2,30), readings=np.ones(2), estimated=np.zeros(2,dtype=bool))
    traffic = tmp_path / 'traffic.json'
    model.save(traffic)
    metadata = tmp_path / 'elevation.json'
    metadata.write_text(json.dumps({'coordinates': {'crs': model.crs, 'units': 'metres',
                         'origin_utm_m': [733000,3750000,0]},
        'roads': [{'source_way':42, 'path': [[0,0,10],[50,0,15],[100,0,10]]}]}))
    output = tmp_path / 'output'
    report = _script().prepare(traffic, output, elevation_metadata=metadata,
                               bounds_utm=[733020,3749999,733080,3750001])
    assert report['build_status'] == 'complete'
    assert report['city_roads'] == 2 and report['selected_roads'] == 1
    assert report['exported_lanes'] == report['expected_lanes'] == 2
    assert report['sumo_identity'] == report['known_height_lane_alignment'] == 'pass'
    assert report['observed_accuracy'] == 'blocked'
    consistency = json.loads((output / 'sumo-consistency.json').read_text())
    assert consistency['extra_edge_count'] == 2
    manifest = json.loads((output / 'unreal' / 'street-twin-unreal.json').read_text())
    assert all((output / 'unreal' / tile['asset']).exists() for tile in manifest['tiles'] if tile['asset'])
