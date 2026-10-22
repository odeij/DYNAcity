"""Evidence validation and real SUMO checks; observations here are synthetic fixtures."""
from dataclasses import replace
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from dynacity.mobility_inputs import MobilityInputs
from dynacity.traffic import road_events, traffic_payload
from dynacity.traffic_sumo import Simulation, Trip, export_network, runtime
from test_traffic_sumo import network, assert_accounting


def package(**updates):
    data = dict(title='Synthetic fixture, not Beirut observations', bounds=[-180, -90, 180, 90],
        sources=[dict(id='survey', title='Synthetic test observations', url='https://example.org/test',
                      as_of='2026-01-01', kind='survey', limitations='Synthetic fixture only.')],
        types=[dict(id='car', label='Car', vclass='passenger'),
               dict(id='bus', label='Bus', vclass='bus'),
               dict(id='van', label='Van', vclass='delivery'),
               dict(id='motorcycle', label='Motorcycle', vclass='motorcycle')],
        fleet=dict(source='survey', label='Synthetic counts', scope='Synthetic network only',
                   weights={'car': 70, 'bus': 10, 'van': 10, 'motorcycle': 10}))
    data.update(updates)
    return MobilityInputs.model_validate(data)


@pytest.mark.parametrize('bad', [0, -1, float('inf'), float('nan')])
def test_invalid_weights_are_not_silently_turned_into_a_fleet(bad):
    data = package().model_dump()
    data['fleet']['weights']['bus'] = bad
    with pytest.raises(ValidationError):
        MobilityInputs.model_validate(data)


def test_missing_class_and_missing_evidence_cannot_be_filled_with_guesses():
    data = package().model_dump()
    data['fleet']['missing_classes'] = ['motorcycle']
    with pytest.raises(ValidationError, match='unmeasured class'):
        MobilityInputs.model_validate(data)
    data = package().model_dump()
    data['fleet']['source'] = 'absent'
    with pytest.raises(ValidationError, match='unknown mobility evidence'):
        MobilityInputs.model_validate(data)
    data = package().model_dump()
    data['sources'][0]['kind'] = 'mapped'
    data['signals'] = [dict(node=1, source='survey', phases=[dict(duration_s=30, state='rr')])]
    with pytest.raises(ValidationError, match='survey evidence'):
        MobilityInputs.model_validate(data)


def test_private_control_records_are_not_embedded_as_public_observations():
    m = package(stops=[dict(vehicle='v', edge='0:1', position_m=40, duration_s=5, source='survey')])
    public = m.public()
    assert public['observed_stop_events'] == 1
    assert public['pedestrian_demand'] == 'unavailable'
    assert 'stops' not in public and 'signals' not in public


def require_sumo():
    try:
        return runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))


def test_real_mixed_fleet_uses_distinct_types_and_preserves_paired_accounting(network):
    require_sumo()
    m = replace(network, mobility=package())
    trips = [Trip(f'v{i}', ('0:1', '1:1', '5:1'), 0, 5+i*12) for i in range(7)]
    sim = Simulation(m, trips=trips, seed=17, mixed=True)
    try:
        types = sim.network_quality['vehicle_types']
        assert types['bus']['length_m'] > types['car']['length_m']
        assert types['motorcycle']['width_m'] < types['car']['width_m']
        assert 'SUMO class defaults' in types['bus']['parameters']
        for i, connection in enumerate(sim.connections):
            for vehicle in connection.vehicle.getIDList():
                assert connection.vehicle.getTypeID(vehicle) == m.mobility.vehicle_type(vehicle, 17)
        frames = sim.advance(road_events(m), 3)['frames']
        for f in frames:
            assert_accounting(f)
            assert sum(f['metrics']['vehicle_types'].values()) == f['metrics']['active']
            assert all(v[7] == m.mobility.vehicle_type(v[0], 17) for v in f['vehicles'])
        assert frames[-1]['metrics']['mean_completed_delay_s'] in (None, 0)
    finally:
        sim.close()


@pytest.mark.parametrize('kind', ['bus', 'van', 'motorcycle'])
def test_every_mixed_class_obeys_closure_and_reopens_without_losing_vehicles(network, kind):
    require_sumo()
    p = package()
    p.fleet.weights = {kind: 1}
    m = replace(network, mobility=p)
    sim = Simulation(m, trips=[Trip('v', ('0:1', '1:1', '5:1'), 0, 20)], mixed=True)
    try:
        for _ in range(4):
            result = sim.advance(road_events(m, closed=[5]), 10)
            for frame in result['frames']:
                assert_accounting(frame)
        assert result['frames'][-1]['metrics']['arrived'] == 0
        for _ in range(12):
            result = sim.advance(road_events(m), 10)
        assert_accounting(result['frames'][-1])
        assert result['frames'][-1]['metrics']['arrived'] == 1
    finally:
        sim.close()


@pytest.mark.parametrize('parking', [False, True])
def test_survey_stop_changes_actual_vehicle_motion_in_both_runs(network, parking):
    require_sumo()
    p = package(stops=[dict(vehicle='v', edge='0:1', position_m=60, duration_s=8, source='survey', parking=parking)])
    m = replace(network, mobility=p)
    sim = Simulation(m, trips=[Trip('v', ('0:1', '1:1', '5:1'), 0, 10)])
    try:
        frames = []
        for _ in range(3):
            frames.extend(sim.advance(road_events(m), 10)['frames'])
        held = [f for f in frames if f['time_s'] > 2 and f['vehicles'] and f['vehicles'][0][4] < .1
                and f['vehicles'][0][5] == '0:1']
        assert len(held) >= 6
        assert frames[-1]['metrics']['mean_completed_delay_s'] in (None, 0)
        for f in frames:
            assert_accounting(f)
    finally:
        sim.close()


def test_observed_signal_program_and_replay_match_actual_controller(network, tmp_path):
    _, _, converter = require_sumo()
    import sumolib
    p = package(signals=[dict(node=1, source='survey', phases=[dict(duration_s=20, state='r')])])
    m = replace(network, mobility=p)
    net = sumolib.net.readNet(str(export_network(m, tmp_path, converter)))
    count = max(c[2] for c in net.getTLS('1').getConnections()) + 1
    p.signals[0].phases[0].state = 'r'*count
    sim = Simulation(m, trips=[Trip('v', ('0:1', '1:1', '5:1'), 0, 10)])
    try:
        frame = sim.advance(road_events(m), 10)['frames'][-1]
        assert sim.network_quality['surveyed_signals'] == 1
        assert frame['signals']
        states = sim.connections[0].trafficlight.getRedYellowGreenState('1')
        assert all(s[4] == states[s[1]] and s[5] for s in frame['signals'])
        assert frame['metrics']['arrived'] == 0
        assert_accounting(frame)
    finally:
        sim.close()
    p.signals[0].phases[0].state = 'r'*(count+1)
    with pytest.raises(ValueError, match='controlled links'):
        Simulation(m, trips=[])


def test_pilot_compiler_does_not_confuse_infrastructure_with_activity(tmp_path):
    spec = importlib.util.spec_from_file_location('prepare_pilot', Path(__file__).parents[1]/'scripts/prepare_mobility_pilot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path/'osm.xml'
    path.write_text('''<osm><node id="1" lon="35.52" lat="33.89"><tag k="highway" v="bus_stop"/>
      <tag k="name" v="B7-A1"/></node><node id="2" lon="35.521" lat="33.891">
      <tag k="amenity" v="parking"/><tag k="capacity" v="20"/></node></osm>''')
    p = module.prepare(path, [35.5, 33.8, 35.6, 34], '2026-10-22')
    assert len(p.features) == 2
    assert p.features[1].capacity == 20
    assert not p.signals and not p.stops
    assert 'motorcycle' not in p.fleet.weights
    assert p.fleet.missing_classes == ['motorcycle']
    assert any('Cola' in gap for gap in p.gaps)


def test_corridor_subset_preserves_reference_flows_and_has_reproducible_road_mapping(network, tmp_path):
    import numpy as np
    from dynacity.mobility_inputs import pilot_network
    from dynacity.traffic import TrafficModel, background_flow
    from dynacity.viewer import utm_to_lonlat
    # A small box around the first road; keep its source geometry and node IDs.
    a = utm_to_lonlat(733010, 3749999, 36, True)
    b = utm_to_lonlat(733090, 3750001, 36, True)
    subset, indices = pilot_network(network, [a[0], a[1], b[0], b[1]])
    assert indices == [0]
    assert subset.geometry[0] == network.geometry[0]
    np.testing.assert_array_equal(subset.u, network.u[indices])
    np.testing.assert_allclose(background_flow(subset), background_flow(network)[indices])
    subset.save(tmp_path/'pilot.json')
    reloaded = TrafficModel.load(tmp_path/'pilot.json')
    np.testing.assert_allclose(background_flow(reloaded), background_flow(network)[indices])
    assert 'not a citywide run' in subset.source
