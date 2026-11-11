"""Planner interventions (speed limits, lanes handed to pedestrians) in the paired SUMO solver."""
import dataclasses

import numpy as np
import pytest

from dynacity.traffic import RoadDisruption, TrafficModel, road_events
from dynacity.traffic_planning import RoadPlanning, resolve_planning, summarize
from dynacity.traffic_sumo import Simulation, Trip, runtime

ROUTE = ('0:1', '1:1', '5:1')


@pytest.fixture
def network():
    xy = {0: (733000, 3750000), 1: (733100, 3750000), 2: (733200, 3750000), 3: (733100, 3750100),
          4: (733200, 3750100), 5: (733300, 3750000), 6: (732900, 3750000)}
    arcs = [(0, 1), (1, 2), (1, 3), (3, 4), (4, 2), (2, 5), (6, 0)]
    return TrafficModel(crs='EPSG:32636', geometry=[[xy[u], xy[v]] for u, v in arcs],
                        u=np.array([a for a, _ in arcs]), v=np.array([b for _, b in arcs]),
                        length_m=np.array([100.] * 7), oneway=np.ones(7), highway=['residential'] * 7,
                        way_id=list(range(7)), speed_kmh=np.full((7, 24), 36.), free_kmh=np.full(7, 36.),
                        readings=np.full(7, 100), estimated=np.zeros(7, dtype=bool), lanes=np.ones(7))


@pytest.fixture
def wide(network):
    """The same streets with two lanes each way."""
    return dataclasses.replace(network, lanes=np.full(7, 2.))


@pytest.fixture
def sim(wide):
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    simulation = Simulation(wide, trips=[Trip('a', ROUTE, 0, 20), Trip('b', ROUTE, 1, 5)])
    yield simulation
    simulation.close()


def test_planning_validation(network):
    assert resolve_planning(network, None) == {}
    assert resolve_planning(network, []) == {}
    # Entries that change nothing are the unplanned network.
    assert resolve_planning(network, [{'road': 3}]) == {}
    plan = resolve_planning(network, [{'road': 1, 'speed_limit_kmh': 30}, RoadPlanning(road=2, lanes_removed=1)])
    assert plan == {1: (30.0, 0), 2: (None, 1)}
    for bad in ([{'road': 7}], [{'road': -1}], [{'road': 1}, {'road': 1, 'lanes_removed': 1}],
                [{'road': 1, 'speed_limit_kmh': 4.9}], [{'road': 1, 'speed_limit_kmh': 80.1}],
                [{'road': 1, 'speed_limit_kmh': float('nan')}], [{'road': 1, 'lanes_removed': 5}],
                [{'road': 1, 'lanes_removed': -1}], [{'road': 1, 'lanes_removed': 1.5}],
                [{'road': 1, 'walking_speed': 3}], [{'road': 1.5}]):
        with pytest.raises(ValueError):
            resolve_planning(network, bad)
    with pytest.raises(ValueError):
        resolve_planning(network, [{'road': i % 7} for i in range(1001)])


def test_lane_removal_leaves_motor_lanes_and_closes_roads_without_any(network):
    plan = resolve_planning(network, [{'road': 1, 'lanes_removed': 1}, {'road': 2, 'lanes_removed': 4},
                                      {'road': 3, 'speed_limit_kmh': 20}])
    # One lane each way: any removal leaves none.
    assert summarize(network, plan) == {'roads': 3, 'speed_limited': 1, 'lanes_removed_roads': 2,
                                        'closed_by_lane_removal': [1, 2]}
    wide = dataclasses.replace(network, lanes=np.full(7, 3.))
    assert summarize(wide, plan)['closed_by_lane_removal'] == [2]


def test_speed_limit_and_lane_restriction_use_real_sumo_and_restore(sim, wide):
    planned = [{'road': 1, 'speed_limit_kmh': 20, 'lanes_removed': 1}, {'road': 3, 'speed_limit_kmh': 50}]
    sim.advance(road_events(wide), 2, planning=planned)
    scenario, baseline = sim.connections
    assert sim.forked
    # Curb lane barred for every vehicle class, the other lane keeps its permissions.
    assert scenario.lane.getAllowed('1:1_0') == () and 'passenger' in scenario.lane.getDisallowed('1:1_0')
    assert 'passenger' in scenario.lane.getAllowed('1:1_1')
    assert scenario.lane.getMaxSpeed('1:1_0') == pytest.approx(20 / 3.6)
    # A cap above the road's own limit never speeds it up.
    assert scenario.lane.getMaxSpeed('3:1_0') == pytest.approx(36 / 3.6)
    # The comparison run is untouched, and untouched roads stay as they were.
    assert 'passenger' in baseline.lane.getAllowed('1:1_0')
    assert baseline.lane.getMaxSpeed('1:1_0') == pytest.approx(36 / 3.6)
    assert 'passenger' in scenario.lane.getAllowed('2:1_0')
    # Changing the plan within the run: more lanes, a different cap.
    sim.advance(road_events(wide), 2, planning=[{'road': 1, 'speed_limit_kmh': 10, 'lanes_removed': 2},
                                                 {'road': 6, 'lanes_removed': 1}])
    assert 'passenger' not in scenario.lane.getAllowed('1:1_1')
    assert scenario.lane.getMaxSpeed('1:1_0') == pytest.approx(10 / 3.6)
    assert 'passenger' in scenario.lane.getAllowed('3:1_0')
    assert scenario.lane.getMaxSpeed('3:1_0') == pytest.approx(36 / 3.6)
    assert 'passenger' not in scenario.lane.getAllowed('6:1_0')
    # Dropping the plan restores permissions and speeds exactly.
    result = sim.advance(road_events(wide), 2, planning=[])
    assert result['planning']['roads'] == 0
    for lane, allowed in sim.permissions.items():
        assert tuple(sorted(scenario.lane.getAllowed(lane))) == allowed
    for edge in ('1:1', '3:1', '6:1'):
        assert scenario.lane.getMaxSpeed(f'{edge}_0') == pytest.approx(36 / 3.6)
    assert not sim.closed


def test_speed_cap_combines_with_disruption_slowdown(sim, wide):
    factor = np.ones(7)
    factor[1] = factor[2] = 0.5
    slowdown = RoadDisruption(closed=np.zeros(7, dtype=bool), speed_factor=factor, reason=[None] * 7)
    sim.advance(slowdown, 2, planning=[{'road': 1, 'speed_limit_kmh': 30}, {'road': 2, 'speed_limit_kmh': 10}])
    scenario = sim.connections[0]
    assert scenario.lane.getMaxSpeed('1:1_0') == pytest.approx(18 / 3.6)  # disruption is already slower than the cap
    assert scenario.lane.getMaxSpeed('2:1_0') == pytest.approx(10 / 3.6)  # cap is slower than the disruption
    sim.advance(slowdown, 2, planning=[])
    assert scenario.lane.getMaxSpeed('1:1_0') == pytest.approx(18 / 3.6)
    assert scenario.lane.getMaxSpeed('2:1_0') == pytest.approx(18 / 3.6)


def test_planning_forks_the_unplanned_baseline_before_any_change(wide):
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    sim = Simulation(wide, trips=[], continuous=True, seed=7)
    try:
        for _ in range(2):
            sim.advance(road_events(wide), 5)
        assert not sim.forked
        sim.advance(road_events(wide), 5, planning=[{'road': 1, 'lanes_removed': 1}])
        assert sim.forked
        scenario, baseline = sim.connections
        assert baseline.simulation.getTime() == scenario.simulation.getTime()
        assert 'passenger' in baseline.lane.getAllowed('1:1_0')
        assert 'passenger' not in scenario.lane.getAllowed('1:1_0')
        for _ in range(3):
            result = sim.advance(road_events(wide), 5, planning=[{'road': 1, 'lanes_removed': 1}])
        frame = result['frames'][-1]
        assert frame['metrics']['total'] == frame['metrics']['active'] + frame['metrics']['pending'] + frame['metrics']['arrived']
        assert frame['metrics']['baseline']['arrived'] > 0
        # Baseline never saw the plan.
        assert 'passenger' in baseline.lane.getAllowed('1:1_0')
    finally:
        sim.close()


def test_lane_removed_under_a_car_waiting_to_enter_does_not_abort_sumo(wide):
    # The planning table sends its plan with the very first request, before
    # the initial fleet is on the road. A car due to enter on the curb lane of
    # a road that loses that lane used to abort SUMO ("Invalid departlane").
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    # Ten cars due at the same spot of the curb lane: one fits, nine wait to enter.
    sim = Simulation(wide, trips=[Trip(f'c{i}', ROUTE, 0, 20) for i in range(10)])
    try:
        assert sim.connections[0].simulation.getPendingVehicles()
        plan = [{'road': 0, 'lanes_removed': 1}]
        frame = None
        for _ in range(60):
            frame = sim.advance(road_events(wide), 2, planning=plan)['frames'][-1]
        metrics = frame['metrics']
        assert metrics['total'] == metrics['active'] + metrics['pending'] + metrics['arrived'] == 10
        assert metrics['arrived'] == 10
        assert 'passenger' not in sim.connections[0].lane.getAllowed('0:1_0')
    finally:
        sim.close()


def test_cars_leave_a_removed_lane_and_still_arrive(sim):
    sim.advance(road_events(sim.model), 1, planning=[{'road': 1, 'lanes_removed': 1}])
    on_removed, frame = 0, None
    for _ in range(40):
        result = sim.advance(road_events(sim.model), 2, planning=[{'road': 1, 'lanes_removed': 1}])
        frame = result['frames'][-1]
        on_removed += sum(1 for vid in sim.connections[0].vehicle.getIDList()
                          if sim.connections[0].vehicle.getLaneID(vid) == '1:1_0')
    assert frame['metrics']['arrived'] == 2
    assert frame['metrics']['paired_arrivals'] == 2
    # Both cars start behind the road and are never on the barred lane.
    assert on_removed == 0


def test_removing_the_only_lane_closes_the_road_like_a_closure_and_reopens(network):
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    sim = Simulation(network, trips=[Trip('a', ROUTE, 0, 20), Trip('b', ROUTE, 0, 5)])
    try:
        plan = [{'road': 5, 'lanes_removed': 1}]
        for _ in range(6):
            result = sim.advance(road_events(network), 10, planning=plan)
        frame = result['frames'][-1]
        assert result['planning']['closed_by_lane_removal'] == [5]
        assert sim.closed == {'5:1'}
        assert frame['metrics']['arrived'] == 0 and frame['metrics']['no_detour'] == 2
        assert frame['metrics']['baseline']['arrived'] == 2
        assert frame['metrics']['overdue'] == 2
        for _ in range(10):
            result = sim.advance(road_events(network), 10, planning=[])
        frame = result['frames'][-1]
        assert frame['metrics']['arrived'] == 2 and frame['metrics']['paired_arrivals'] == 2
        assert not sim.closed
    finally:
        sim.close()


def test_planned_closure_reroutes_like_a_manual_one(network):
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    sim = Simulation(network, trips=[Trip('a', ROUTE, 0, 20)])
    try:
        sim.advance(road_events(network), 1)
        result = sim.advance(road_events(network), 1, planning=[{'road': 1, 'lanes_removed': 1}])
        for _ in range(60):
            result = sim.advance(road_events(network), 2, planning=[{'road': 1, 'lanes_removed': 1}])
        frame = result['frames'][-1]
        assert frame['metrics']['rerouted'] == 1 and frame['metrics']['arrived'] == 1
    finally:
        sim.close()


def test_fork_after_a_trip_ended_does_not_drive_it_again_in_the_copy(network):
    # SUMO used to re-read the trip file when the copy loaded the saved state,
    # so starting cars that had already arrived drove again, in the copy only.
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    sim = Simulation(network, trips=[Trip('a', ROUTE, 0, 0)])
    try:
        for _ in range(30):
            frame = sim.advance(road_events(network), 2)['frames'][-1]
        assert frame['metrics']['arrived'] == 1 and not sim.forked
        for _ in range(5):
            frame = sim.advance(road_events(network), 2, planning=[{'road': 1, 'speed_limit_kmh': 20}])['frames'][-1]
        assert sim.forked
        baseline = sim.connections[1]
        assert baseline.vehicle.getIDCount() == 0 and not baseline.simulation.getPendingVehicles()
        assert frame['metrics']['baseline'] == {'active': 0, 'pending': 0, 'arrived': 1, 'stopped': 0}
    finally:
        sim.close()


def _replanning_sim(network, monkeypatch, period):
    """One car on ROUTE, drivers re-planning every `period` s from current speeds."""
    import dynacity.traffic_sumo as adapter
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    monkeypatch.setattr(adapter, 'REROUTE_PERIOD_S', period)
    monkeypatch.setattr(adapter, 'REROUTE_MEMORY_S', 1)
    return Simulation(network, trips=[Trip('a', ROUTE, 0, 0)])


@pytest.mark.parametrize('period', [0, 2])
def test_drivers_replan_around_a_slowed_street_only_when_rerouting_is_on(network, monkeypatch, period):
    # Road 1 slowed to 5 km/h takes 72 s; the three-road detour (2, 3, 4) takes 30 s.
    sim = _replanning_sim(network, monkeypatch, period)
    try:
        slow = [{'road': 1, 'speed_limit_kmh': 5}]
        for _ in range(3):
            sim.advance(road_events(network), 2, planning=slow)
        scenario, baseline = sim.connections
        took_detour = '2:1' in scenario.vehicle.getRoute('a')
        assert took_detour == bool(period)
        # The unchanged run has no reason to leave the direct road.
        assert '2:1' not in baseline.vehicle.getRoute('a')
        for _ in range(90):
            frame = sim.advance(road_events(network), 2, planning=slow)['frames'][-1]
        assert frame['metrics']['arrived'] == frame['metrics']['baseline']['arrived'] == 1
    finally:
        sim.close()


def test_closure_on_a_replanned_route_is_noticed(network, monkeypatch):
    # The car re-plans onto the detour inside SUMO; closing the detour must
    # still count it as rerouted (the adapter reads its new route, not the
    # route it entered with).
    sim = _replanning_sim(network, monkeypatch, 2)
    try:
        slow = [{'road': 1, 'speed_limit_kmh': 5}]
        for _ in range(3):
            sim.advance(road_events(network), 2, planning=slow)
        scenario = sim.connections[0]
        assert '3:1' in scenario.vehicle.getRoute('a') and scenario.vehicle.getRoadID('a') == '0:1'
        result = sim.advance(road_events(network), 1, planning=slow + [{'road': 3, 'lanes_removed': 1}])
        assert 'a' in sim.rerouted and '3:1' not in scenario.vehicle.getRoute('a')
        assert result['frames'][-1]['metrics']['no_detour'] == 0
    finally:
        sim.close()


def test_api_validates_and_accepts_planning_in_create_and_step(wide, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import dynacity.traffic_sumo as adapter
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    original = adapter.Simulation
    monkeypatch.setattr(adapter, 'Simulation', lambda model, hour, **kw: original(
        model, hour, trips=[Trip('api-car', ROUTE, 0, 20)], **kw))
    app = FastAPI()
    adapter.install_routes(app, wide, None)
    url = '/v1/viewer/traffic/simulation'
    with TestClient(app) as client:
        for bad in ([{'road': 99}], [{'road': 1}, {'road': 1, 'lanes_removed': 1}],
                    [{'road': 1, 'speed_limit_kmh': 4}], [{'road': 1, 'speed_limit_kmh': 81}],
                    [{'road': 1, 'lanes_removed': 5}], [{'road': 1, 'lanes_removed': -1}], [{'speed_limit_kmh': 30}],
                    [{'road': 1, 'extra': 1}], [{'road': 1}] * 1001):
            assert client.post(url, json={'planning': bad}).status_code == 422, bad
        created = client.post(url, json={'seconds': 1, 'planning': [{'road': 1, 'speed_limit_kmh': 20}]})
        assert created.status_code == 200
        key = created.json()['session']
        assert created.json()['planning']['speed_limited'] == 1
        # Invalid steps are rejected without touching the session; planning changes need no new session.
        assert client.post(f'{url}/{key}', json={'planning': [{'road': 99}]}).status_code == 422
        stepped = client.post(f'{url}/{key}', json={'seconds': 1, 'planning': [{'road': 1, 'lanes_removed': 1}]})
        assert stepped.status_code == 200 and stepped.json()['planning']['lanes_removed_roads'] == 1
        cleared = client.post(f'{url}/{key}', json={'seconds': 1})
        assert cleared.status_code == 200 and cleared.json()['planning']['roads'] == 0
        assert cleared.json()['frames'][-1]['metrics']['total'] == 1
        assert client.delete(f'{url}/{key}').status_code == 200
        # A rejected create does not leave a session behind.
        assert client.post(url, json={'planning': [{'road': 99}]}).status_code == 422
