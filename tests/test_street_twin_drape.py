import copy
import numpy as np
import pytest

from dynacity.street_twin_drape import drape_scene, read_asphalt_triangles
from dynacity.engine_asset import export_glb


def scene(vertices):
    return {'meshes': [{'id': 'canonical/paint', 'kind': 'marking', 'vertices': vertices,
                       'triangles': [[0, 1, 2]], 'metadata': {'lane_id': 'canonical/lane',
                       'evidence': {'status': 'assumed'}}}], 'limitations': []}


def test_sloping_asphalt_exact_plane_and_input_unchanged():
    source = scene([[0, 0, 0], [1, 0, 0], [0, 1, 0]])
    original = copy.deepcopy(source)
    result, report = drape_scene(source, [[[0, 0, 0], [2, 0, 1], [0, 2, 0]]])
    points = np.asarray(result['meshes'][0]['vertices'])
    assert np.allclose(points[:, 2], .5 * points[:, 0] + .02)
    assert report['covered_area_m2'] == pytest.approx(.5)
    assert result['meshes'][0]['metadata']['lane_id'] == 'canonical/lane'
    assert source == original


def test_partial_coverage_clips_and_accounts_omissions():
    result, report = drape_scene(scene([[0, 0, 0], [2, 0, 0], [0, 2, 0]]),
                                 [[[0, 0, 0], [1, 0, 0], [0, 1, 0]]])
    assert report['covered_area_m2'] == pytest.approx(.5)
    assert report['omitted_area_m2'] == pytest.approx(1.5)
    assert np.asarray(result['meshes'][0]['vertices'])[:, :2].sum(axis=1).max() <= 1 + 1e-9


def test_overlapping_layers_choose_nearest_without_duplicate_area():
    ground = np.array([[0, 0, 0], [2, 0, 0], [0, 2, 0]])
    bridge = ground + [0, 0, 5]
    result, report = drape_scene(scene([[0, 0, 4.8], [1, 0, 4.8], [0, 1, 4.8]]), [ground, bridge])
    assert np.allclose(np.asarray(result['meshes'][0]['vertices'])[:, 2], 5.02)
    assert report['covered_area_m2'] == pytest.approx(.5)


def test_reject_remote_layer_and_nonfinite_input():
    source = scene([[0, 0, 0], [1, 0, 0], [0, 1, 0]])
    result, report = drape_scene(source, [[[0, 0, 8], [2, 0, 8], [0, 2, 8]]])
    assert not result['meshes']
    assert report['omitted_features'] == ['canonical/paint']
    with pytest.raises(ValueError, match='finite'):
        drape_scene(source, [[[0, 0, float('nan')], [2, 0, 0], [0, 2, 0]]])


def test_glb_coordinate_conversion_and_origin_rejection(tmp_path):
    vertices = [[10, 20, 3], [12, 20, 4], [10, 22, 5]]
    coordinates = {'crs': 'EPSG:32636', 'origin_utm_m': [1, 2, 3],
                   'axes': 'X east, Y north, Z up', 'units': 'metres'}
    data = {'coordinates': coordinates, 'bounds_lonlat': None, 'limitations': [],
            'meshes': [{'id': 'road', 'kind': 'road', 'vertices': vertices,
                        'triangles': [[0, 1, 2]], 'metadata': {'road_id': 'road'}}], 'trees': []}
    path = tmp_path / 'road.glb'
    export_glb(data, path, include_calibration=False)
    assert np.allclose(read_asphalt_triangles(path, expected_coordinates=coordinates), [vertices])
    with pytest.raises(ValueError, match='origin_utm_m'):
        read_asphalt_triangles(path, expected_coordinates={**coordinates, 'origin_utm_m': [2, 3, 4]})


def test_city_coordinate_precision_slivers_are_explicitly_omitted():
    source = scene([[2400., 1500., 0.], [2401., 1500., 0.], [2400., 1500.0003, 0.]])
    result, report = drape_scene(source, [[[2399., 1499., 0.], [2404., 1499., 0.], [2399., 1504., 0.]]])
    assert not result['meshes']
    assert report['precision_omitted_triangles'] > 0
    assert report['precision_omitted_area_m2'] == pytest.approx(report['input_area_m2'])
    assert report['omitted_area_m2'] == pytest.approx(report['input_area_m2'])
