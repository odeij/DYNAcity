"""Export agreement and honest held-out observation gates."""
from dataclasses import replace
from copy import deepcopy
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from dynacity.street_twin import build_street_twin
from dynacity.street_twin_validation import (validate_observations, validate_sumo_network,
                                             validate_unreal_lane_alignment)
from dynacity.traffic import TrafficModel


@pytest.fixture
def model():
    paths = [[(733000, 3750000), (733050, 3750005), (733100, 3750000)],
             [(733100, 3750000), (733200, 3750000)]]
    return TrafficModel(crs='EPSG:32636', geometry=paths, u=np.array([0, 1]), v=np.array([1, 2]),
                        length_m=np.array([101, 100]), oneway=np.array([0, 1]),
                        highway=['residential']*2, way_id=[123, 456], lanes=np.array([2, 1]),
                        speed_kmh=np.full((2, 24), 30), free_kmh=np.full(2, 30),
                        readings=np.ones(2), estimated=np.zeros(2, dtype=bool))


def write_net(twin, path):
    root = ET.Element('net')
    offset = [-733000, -3750000]
    origin = twin['coordinates']['origin_utm_m']
    ET.SubElement(root, 'location', netOffset=','.join(map(str, offset)))
    for road in twin['roads']:
        for edge in road['edges']:
            points = road['path'] if edge['direction'] == 1 else road['path'][::-1]
            shape = ' '.join(f'{p[0]+origin[0]+offset[0]},{p[1]+origin[1]+offset[1]}' for p in points)
            element = ET.SubElement(root, 'edge', id=edge['sumo_edge_id'], shape=shape,
                                    **{'from': edge['from_node'], 'to': edge['to_node']})
            for lane in edge['lanes']:
                ET.SubElement(element, 'lane', id=lane['sumo_lane_id'], width=str(lane['width_m']))
    ET.ElementTree(root).write(path)
    return root


def test_offsets_and_directed_lane_identities(model, tmp_path):
    twin = build_street_twin(model, origin_utm_m=(733050, 3750001, 0))
    path = tmp_path/'net.xml'
    root = write_net(twin, path)
    assert validate_sumo_network(twin, path)['status'] == 'pass'
    # Same line reversed has zero Hausdorff error but wrong movement direction.
    edge = root.find('edge')
    edge.set('shape', ' '.join(edge.attrib['shape'].split()[::-1]))
    ET.ElementTree(root).write(path)
    report = validate_sumo_network(twin, path)
    assert report['status'] == 'fail'
    assert report['identity_status'] == 'pass'
    assert report['reference_geometry_status'] == 'fail'
    assert report['drifted_edge_count'] == 1
    assert report['checks'][0]['endpoint_error_m'] == pytest.approx(100)


def test_missing_lanes_wrong_width_and_coordinates_fail(model, tmp_path):
    twin = build_street_twin(model)
    path = tmp_path/'net.xml'
    root = write_net(twin, path)
    edge = root.find('edge')
    edge.remove(edge.find('lane'))
    edge.find('lane').set('width', '2.0')
    edge.set('shape', '0,20 50,25 100,20')
    ET.ElementTree(root).write(path)
    result = validate_sumo_network(twin, path)
    assert result['status'] == 'fail'
    assert 'lane_identity_mismatch' in result['checks'][0]['issues']
    assert 'reference_geometry_mismatch' in result['checks'][0]['issues']
    assert any(i.startswith('lane_width_mismatch') for i in result['checks'][0]['issues'])


def test_absent_transform_blocks(model, tmp_path):
    path = tmp_path/'net.xml'
    path.write_text('<net/>')
    assert validate_sumo_network(build_street_twin(model), path)['status'] == 'blocked'


def test_actual_netconvert_matches_canonical_reference(model, tmp_path):
    from dynacity.traffic_sumo import export_network, runtime
    try:
        _, _, converter = runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    twin = build_street_twin(model, origin_utm_m=(733000, 3750000, 10))
    net = export_network(model, tmp_path, converter)
    result = validate_sumo_network(twin, net)
    assert result['status'] == 'pass', result
    # Canonical measured corrections must not silently agree with the old net.
    altered = build_street_twin(replace(model, lanes=np.array([3, 1])))
    assert validate_sumo_network(altered, net)['status'] == 'fail'


def test_actual_lane_alignment_and_incomplete_height_coverage(model, tmp_path):
    from dynacity.traffic_sumo import export_network, runtime
    from dynacity.street_twin_unreal import export_street_twin_unreal
    try:
        _, _, converter = runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    twin = build_street_twin(model, origin_utm_m=(733000, 3750000, 10))
    # Synthetic test elevations. Real unknown elevations must stay unknown.
    for road in twin['roads']:
        for point in road['path']:
            point[2] = 12.0
    net = export_network(model, tmp_path, converter)
    manifest = export_street_twin_unreal(twin, tmp_path/'assets', net_xml=net)
    report = validate_unreal_lane_alignment(twin, manifest, net)
    assert report['status'] == report['eligible_alignment_status'] == 'pass', report
    assert report['known_height_lane_count'] == report['exported_lane_count'] == 5
    assert max(c['geometry_error_m'] for c in report['checks']) < 1e-7
    wrong_frame = deepcopy(manifest)
    wrong_frame['coordinates']['origin_utm_m'][0] += 1
    assert validate_unreal_lane_alignment(twin, wrong_frame, net)['eligible_alignment_status'] == 'fail'
    wrong_count = deepcopy(manifest)
    wrong_count['tiles'][0]['lane_count'] += 1
    assert validate_unreal_lane_alignment(twin, wrong_count, net)['eligible_alignment_status'] == 'fail'
    # A shifted mesh path must fail even when its lane metadata IDs are right.
    shifted = deepcopy(manifest)
    for point in shifted['tiles'][0]['lanes'][0]['lane_path_local_m']:
        point[1] += 0.2
    assert validate_unreal_lane_alignment(twin, shifted, net)['status'] == 'fail'
    # Correctly omitted unknown heights do not establish complete visual coverage.
    for point in twin['roads'][0]['path']:
        point[2] = None
    manifest = export_street_twin_unreal(twin, tmp_path/'partial', net_xml=net)
    report = validate_unreal_lane_alignment(twin, manifest, net)
    assert report['status'] == 'blocked'
    assert report['eligible_alignment_status'] == 'pass'
    assert report['unknown_height_lane_count'] == 4
    assert report['exported_lane_count'] == 1
    # Missing a known-height export is a failure, distinct from an unknown height.
    for tile in manifest['tiles']:
        tile['lanes'] = []
        tile['lane_count'] = 0
    assert validate_unreal_lane_alignment(twin, manifest, net)['status'] == 'fail'


def observed(**changes):
    row = dict(location_id='junction-a:northbound:car', metric='turn_count', unit='vehicles',
               interval_start='2026-10-20T08:00:00+03:00', interval_end='2026-10-20T08:15:00+03:00',
               value=100, split='validation', source_id='survey-20261020')
    return row | changes


TOLERANCE = {'turn_count': {'unit': 'vehicles', 'absolute': 5, 'relative': 0.1}}


def test_only_held_out_measurements_determine_accuracy():
    held = observed()
    calibration = observed(split='calibration', interval_start='2026-10-20T07:00:00+03:00',
                           interval_end='2026-10-20T07:15:00+03:00')
    report = validate_observations([held, calibration], [held | {'value': 115}], TOLERANCE)
    assert report['status'] == 'pass'
    assert report['validation_observation_count'] == 1
    assert report['calibration_observation_count'] == 1
    report = validate_observations([held], [held | {'value': 116}], TOLERANCE)
    assert report['status'] == 'fail'
    assert report['checks'][0]['allowed_error'] == 15


def test_missing_measurements_units_or_tolerances_never_pass():
    assert validate_observations([], [], {})['status'] == 'blocked'
    assert validate_observations([observed()], [], TOLERANCE)['status'] == 'blocked'
    assert validate_observations([observed()], [observed()], {})['status'] == 'blocked'
    assert validate_observations([observed()], [observed(unit='vehicles/hour')], TOLERANCE)['status'] == 'blocked'
    assert validate_observations([observed(split='calibration')], [], TOLERANCE)['status'] == 'blocked'


def test_timezone_equivalent_intervals_match_and_leakage_is_rejected():
    simulation = observed(interval_start='2026-10-20T05:00:00Z', interval_end='2026-10-20T05:15:00Z')
    assert validate_observations([observed()], [simulation], TOLERANCE)['status'] == 'pass'
    with pytest.raises(ValueError, match='overlap'):
        validate_observations([observed(), observed(split='calibration',
                              interval_start='2026-10-20T08:10:00+03:00',
                              interval_end='2026-10-20T08:25:00+03:00')], [], TOLERANCE)


@pytest.mark.parametrize('change', [{'value': float('nan')}, {'source_id': ''},
                                  {'split': 'unknown'}, {'interval_start': '2026-10-20T08:00:00'}])
def test_malformed_evidence_rejected(change):
    with pytest.raises(ValueError):
        validate_observations([observed(**change)], [], TOLERANCE)
