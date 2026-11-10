"""Real SUMO integration tests; skip explicitly when the optional runtime is absent."""
import numpy as np
import pytest
from dynacity.traffic import TrafficModel, road_events
from dynacity.traffic_sumo import Simulation, Trip, runtime


def test_comparison_counts_include_unfinished_trips_and_ignore_internal_roads():
    """Known subscription samples verify comparison accounting without optional binaries."""
    from types import SimpleNamespace as NS
    constants = NS(VAR_POSITION=1, VAR_ANGLE=2, VAR_SPEED=3, VAR_ROAD_ID=4, VAR_LANE_INDEX=5, VAR_SIGNALS=6)
    states = {
        'a': {1:(733000,3750000), 2:90, 3:0, 4:'1:1', 5:0, 6:2},
        'b': {1:(733010,3750000), 2:90, 3:6, 4:':junction_0', 5:0},
        'c': {1:(733020,3750000), 2:90, 3:0.05, 4:'2:-1', 5:0},
    }
    scenario = NS(junction=NS(getContextSubscriptionResults=lambda _:states),
                  vehicle=NS(getIDList=lambda:list(states)),
                  simulation=NS(getPendingVehicles=lambda:['e'], getTime=lambda:10,
                                getStartingTeleportNumber=lambda:0))
    baseline = NS(junction=NS(getContextSubscriptionResults=lambda _:{'b':{3:5}, 'c':{3:0}}),
                  vehicle=NS(getIDCount=lambda:2), simulation=NS(getPendingVehicles=lambda:['e']))
    sim = Simulation.__new__(Simulation)
    sim.connections = [scenario, baseline]
    sim.traci = NS(constants=constants)
    sim.context_node = 'junction'
    sim.offset, sim.zone, sim.north = (0,0), 36, True
    sim.trips = list('abcde')
    sim.trip_ids = set(sim.trips)
    sim.arrivals = [{'d':9}, {'a':6, 'd':7}]
    sim.held, sim.blocked, sim.rerouted = {'a'}, {'a'}, {'b','c'}
    frame = sim.frame()
    assert_accounting(frame)
    m = frame['metrics']
    assert m['baseline'] == {'active':2, 'pending':1, 'arrived':2, 'stopped':1}
    assert m['stopped_roads'] == [(1,1), (2,1)]
    assert m['detour'] == [(2,1)]
    assert m['overdue'] == 1
    assert m['paired_arrivals'] == 1
    assert m['mean_completed_delay_s'] == 2
    # Left indicator on 'a'; vehicles reporting no bits read as 0.
    assert {v[0]: v[8] for v in frame['vehicles']} == {'a': 2, 'b': 0, 'c': 0}


def test_unstick_moves_only_free_gridlocked_front_cars():
    """Front cars standing past the limit move to their next road; held, queued and closed moves do not."""
    from types import SimpleNamespace as NS
    from dynacity.traffic_sumo import UNSTICK_AFTER_S
    W = 9
    states = {'stuck': {W: UNSTICK_AFTER_S + 1}, 'held': {W: 999}, 'queued': {W: 999},
              'toclosed': {W: 999}, 'fresh': {W: 5}}
    routes = {'stuck': ('1:1', '2:1'), 'held': ('3:1', '4:1'), 'queued': ('1:1', '2:1'),
              'toclosed': ('5:1', '6:1'), 'fresh': ('1:1', '2:1')}
    moved = []
    c = NS(junction=NS(getContextSubscriptionResults=lambda _: states),
           vehicle=NS(getLaneID=lambda v: 'own_' + v, getRoute=lambda v: routes[v], getRouteIndex=lambda v: 0,
                      getLanePosition=lambda v: 50.0 if v == 'ahead' else 40.0,
                      moveTo=lambda v, lane, pos: moved.append((v, lane))),
           edge=NS(getLaneNumber=lambda e: 2),
           # 'queued' has a car ahead of it on its own lane; '2:1_0' has room only past 40 m.
           lane=NS(getLastStepVehicleIDs=lambda lane: ['ahead', 'queued'] if lane == 'own_queued'
                   else [lane[4:]] if lane.startswith('own_') else ['x'] if lane == '2:1_0' else []))
    sim = Simulation.__new__(Simulation)
    sim.traci = NS(constants=NS(VAR_WAITING_TIME=W), TraCIException=RuntimeError)
    sim.context_node = 'junction'
    sim.held, sim.blocked, sim.closed = {'held'}, set(), {'6:1'}
    sim.unstuck = [0, 0]
    sim.unstick(0, c)
    assert moved == [('stuck', '2:1_0')]  # car far ahead on lane 0 leaves room
    assert sim.unstuck == [1, 0]

def test_inflow_routes_skip_stub_entry_roads():
    from dynacity.traffic_sumo import trim_entry
    length = {'stub': 2.0, 'short': 30.0, 'long': 120.0, 'end': 10.0}.get
    assert trim_entry(('stub', 'short', 'long', 'end'), length) == ('long', 'end')
    assert trim_entry(('long', 'end'), length) == ('long', 'end')
    # No long road before the last: keep the route rather than lose the trip.
    assert trim_entry(('stub', 'short', 'end'), length) == ('stub', 'short', 'end')

@pytest.fixture
def network():
    # Directed diamond with a shared destination edge, plus a second origin.
    xy = {0:(733000,3750000),1:(733100,3750000),2:(733200,3750000),
          3:(733100,3750100),4:(733200,3750100),5:(733300,3750000),6:(732900,3750000)}
    arcs = [(0,1),(1,2),(1,3),(3,4),(4,2),(2,5),(6,0)]
    geometry = [[xy[u],xy[v]] for u,v in arcs]
    return TrafficModel(crs='EPSG:32636',geometry=geometry,u=np.array([a for a,b in arcs]),
        v=np.array([b for a,b in arcs]), length_m=np.array([100.]*7),oneway=np.ones(7),
        highway=['residential']*7,way_id=list(range(7)),speed_kmh=np.full((7,24),36.),
        free_kmh=np.full(7,36.),readings=np.full(7,100),estimated=np.zeros(7,dtype=bool),
        lanes=np.ones(7))

@pytest.fixture
def simulation(network):
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    sim = Simulation(network,trips=[Trip('a',('0:1','1:1','5:1'),0,20),
        Trip('b',('0:1','1:1','5:1'),0,5)])
    yield sim
    sim.close()

def assert_accounting(frame):
    m=frame['metrics']
    assert m['total']==m['active']+m['pending']+m['arrived']
    assert len(frame['vehicles'])==m['active']
    b = m['baseline']
    assert m['total'] == b['active'] + b['pending'] + b['arrived']
    assert 0 <= b['stopped'] <= b['active']
    assert m['stopped'] == sum(v[4] < 0.1 for v in frame['vehicles'])
    # SUMO's light bits ride along for drawing indicators.
    assert all(isinstance(v[8], int) and v[8] >= 0 for v in frame['vehicles'])


def test_replay_samples_each_step_and_keeps_junction_safety(simulation, network):
    from dynacity.traffic_sumo import JUNCTION_BLOCKER_S, IMPATIENCE_S, STEP_S
    # Held drivers eventually push past junction blockers and grow impatient
    # (citywide junctions otherwise lock for good), but never before waiting,
    # and no car is ever teleported.
    assert JUNCTION_BLOCKER_S >= 10
    assert IMPATIENCE_S >= 60
    assert '-1' == simulation._sumo_command[simulation._sumo_command.index('--time-to-teleport') + 1]
    # Every driver re-plans from current speeds, in both runs (same command).
    from dynacity.traffic_sumo import REROUTE_PERIOD_S
    command = simulation._sumo_command
    assert command[command.index('--device.rerouting.probability') + 1] == '1'
    assert command[command.index('--device.rerouting.period') + 1] == str(REROUTE_PERIOD_S) and REROUTE_PERIOD_S > 0
    # Any old gridlock relocation call would turn this into an immediate failure.
    simulation.unstick = lambda *args: pytest.fail('Replay must not relocate cars')
    original_frame = simulation.frame
    def checked_frame():
        assert simulation.connections[0].simulation.getCollidingVehiclesNumber() == 0
        return original_frame()
    simulation.frame = checked_frame
    result = simulation.advance(road_events(network), 2)
    times = [f['time_s'] for f in result['frames']]
    assert len(times) == 3
    assert np.allclose(np.diff(times), STEP_S)
    for frame in result['frames']:
        assert_accounting(frame)
    assert simulation.connections[0].simulation.getCollidingVehiclesNumber() == 0


@pytest.mark.parametrize('oneway', [1, -1, 0])
@pytest.mark.parametrize('lanes', [1, 2, 3])
def test_simulated_lanes_fit_the_rendered_carriageway(network, tmp_path, oneway, lanes):
    """A one-way way is its carriageway centre, not its left-hand boundary."""
    from dataclasses import replace
    from dynacity.traffic import traffic_payload
    from dynacity.traffic_sumo import export_network
    try:
        _, _, converter = runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    import sumolib
    model = replace(network, oneway=np.full(len(network), oneway),
                    lanes=np.full(len(network), lanes))
    net = sumolib.net.readNet(str(export_network(model, tmp_path, converter)))
    offset = net.getLocationOffset()
    width = traffic_payload(model)['roads'][0]['width_m']
    lateral = []
    for direction in (1, -1):
        if oneway == -direction:
            continue
        for lane in net.getEdge(f'0:{direction}').getLanes():
            x, y = sumolib.geomhelper.positionAtShapeOffset(lane.getShape(), lane.getLength() / 2)
            distance = y - offset[1] - model.geometry[0][0][1]
            lateral.append(distance)
            # The lane's outer boundary, not just its centre, must fit.
            assert abs(distance) + lane.getWidth() / 2 <= width / 2 + 0.01
    assert np.mean(lateral) == pytest.approx(0, abs=0.01)

def test_closed_destination_retains_fleet_and_reopen_completes(simulation,network):
    closed=road_events(network,closed=[5]); opened=road_events(network)
    result=simulation.advance(closed,10)
    for _ in range(5): result=simulation.advance(closed,10)
    frame=result['frames'][-1]; assert_accounting(frame)
    assert frame['metrics']['arrived']==0
    assert frame['metrics']['no_detour']==2
    assert frame['metrics']['baseline']['arrived']==2
    assert frame['metrics']['overdue']==2
    assert frame['metrics']['stopped']==2
    assert sum(n for _, n in frame['metrics']['stopped_roads'])==2
    assert {v[0] for v in frame['vehicles']}=={'a','b'}
    for _ in range(10): result=simulation.advance(opened,10)
    frame=result['frames'][-1]; assert_accounting(frame)
    assert frame['metrics']['arrived']==2
    assert frame['metrics']['paired_arrivals']==2
    assert frame['metrics']['mean_completed_delay_s']>20

def test_detour_uses_actual_positions_and_fixed_destination(simulation,network):
    result=simulation.advance(road_events(network,closed=[1]),10)
    assert result['frames'][-1]['metrics']['rerouted']==2
    c=simulation.connections[0]
    assert c.vehicle.getRoute('a')[-1]=='5:1'
    assert '1:1' not in c.vehicle.getRoute('a')
    seen=set()
    for _ in range(6):
        result=simulation.advance(road_events(network,closed=[1]),10)
        for frame in result['frames']:
            assert_accounting(frame)
            seen.update(v[5] for v in frame['vehicles'])
    assert seen.intersection({'2:1','3:1','4:1'})
    assert result['frames'][-1]['metrics']['arrived']==2

def test_spacing_and_same_edge_closure_resume(simulation,network):
    result=simulation.advance(road_events(network,closed=[0]),5)
    c=simulation.connections[0]
    assert c.vehicle.getSpeed('a')==0
    assert c.vehicle.getSpeed('b')==0
    assert c.vehicle.getLanePosition('a')-c.vehicle.getLanePosition('b')>=7.4
    assert result['frames'][-1]['metrics']['total']==2
    simulation.advance(road_events(network),5)
    assert c.vehicle.getSpeed('a')>0

def test_limits_are_free_flow_and_hourly_speeds_check_the_run(network,tmp_path,monkeypatch):
    from dataclasses import replace
    import dynacity.traffic_sumo as adapter
    try: _, _, converter = runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    import sumolib
    # Congested at every hour (18 km/h) on roads that run 36 km/h when free.
    slow=replace(network,speed_kmh=np.full((7,24),18.),_graph_cache={})
    net=sumolib.net.readNet(str(adapter.export_network(slow,tmp_path,converter)))
    assert net.getEdge('0:1').getSpeed()==pytest.approx(10.0)
    # One car on empty roads: no queue, so it beats the congested median.
    monkeypatch.setattr(adapter,'SPEED_CHECK_WARMUP_S',0)
    monkeypatch.setattr(adapter,'SPEED_CHECK_MIN_SAMPLES',3)
    sim=Simulation(slow,trips=[Trip('lone',('6:1','0:1','1:1','5:1'),0,5)])
    try:
        assert sim.advance(road_events(slow),1)['frames'][-1]['metrics']['speed_check'] is None
        check=sim.advance(road_events(slow),10)['frames'][-1]['metrics']['speed_check']
        assert check['roads']>=1 and check['observed_kmh']==18.0
        assert check['ratio_p50']>1
        # Samples stop while a road is closed: the check describes undisturbed traffic.
        before=sim.speed_samples.sum()
        sim.advance(road_events(slow,closed=[5]),3)
        assert sim.speed_samples.sum()==before
    finally: sim.close()

def test_identical_baseline_has_zero_delay(simulation,network):
    for _ in range(8):
        result=simulation.advance(road_events(network),10)
        for frame in result['frames']:
            assert_accounting(frame)
            m = frame['metrics']
            assert m['baseline'] == {key:m[key] for key in ('active','pending','arrived','stopped')}
    m=result['frames'][-1]['metrics']
    assert m['arrived']==2 and m['mean_completed_delay_s']==0

def test_api_rejects_invalid_roads_and_reports_missing_runtime(network,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import dynacity.traffic_sumo as adapter
    app=FastAPI(); adapter.install_routes(app,network,None)
    def unavailable(*args,**kwargs): raise RuntimeError('SUMO unavailable (test)')
    monkeypatch.setattr(adapter,'Simulation',unavailable)
    with TestClient(app) as client:
        assert client.post('/v1/viewer/traffic/simulation',json={'closed':[999]}).status_code==422
        response=client.post('/v1/viewer/traffic/simulation',json={})
        assert response.status_code==503 and 'unavailable' in response.json()['detail']
        assert client.post('/v1/viewer/traffic/simulation',json={'hour':24}).status_code==422
        for multiplier in (-1, 2.1, 'NaN', 'Infinity'):
            assert client.post('/v1/viewer/traffic/simulation',json={'demand_scale':multiplier}).status_code==422
        assert client.post('/v1/viewer/traffic/simulation/missing',json={}).status_code==404

def test_generated_signal_holds_and_releases_vehicle(network):
    from dataclasses import replace
    # Four incident segments at junction 1 create an assumed signal program.
    extra=[(733100,3749900),(733100,3750000)]
    m=replace(network,geometry=network.geometry+[extra],u=np.append(network.u,7),v=np.append(network.v,1),
        length_m=np.append(network.length_m,100),oneway=np.append(network.oneway,1),
        highway=network.highway+['residential'],way_id=network.way_id+[7],
        speed_kmh=np.vstack([network.speed_kmh,np.full((1,24),36.)]),
        free_kmh=np.append(network.free_kmh,36),readings=np.append(network.readings,100),
        estimated=np.append(network.estimated,False),lanes=np.append(network.lanes,1),_graph_cache={})
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    sim=Simulation(m,trips=[Trip('signal',('0:1','1:1','5:1'),0,20)])
    try:
        c=sim.connections[0]; tls=c.trafficlight.getIDList()
        assert '1' in tls
        state=c.trafficlight.getRedYellowGreenState('1')
        c.trafficlight.setRedYellowGreenState('1','r'*len(state))
        for _ in range(3): sim.advance(road_events(m),10)
        # Main-map replay must include lights without an optional mobility pilot.
        red_signals = [s for s in sim.frame()['signals'] if s[0] == '1']
        assert red_signals and all(s[4] == 'r' and not s[5] for s in red_signals)
        assert all(len(s) == 11 and -180 <= s[6] <= 180 and 0 <= s[9] < len(m) for s in red_signals)
        assert any('l' in s[10] for s in red_signals) and any('r' in s[10] for s in red_signals)
        assert all((s[7], s[8]) != (s[2], s[3]) for s in red_signals)
        assert c.vehicle.getRoadID('signal')=='0:1'
        assert c.vehicle.getSpeed('signal')<0.1
        c.trafficlight.setRedYellowGreenState('1','G'*len(state))
        for _ in range(8): result=sim.advance(road_events(m),10)
        assert all(s[4] == 'G' for s in result['frames'][-1]['signals'] if s[0] == '1')
        assert result['frames'][-1]['metrics']['arrived']==1
    finally: sim.close()

def test_real_session_api_reuses_fleet_and_releases_runtime(network,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import dynacity.traffic_sumo as adapter
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    original=adapter.Simulation
    made=[]
    def small_fleet(model,hour,**kwargs):
        sim=original(model,hour,trips=[Trip('api-car',('0:1','1:1','5:1'),0,20)],**kwargs)
        made.append(sim); return sim
    monkeypatch.setattr(adapter,'Simulation',small_fleet)
    app=FastAPI();adapter.install_routes(app,network,None)
    with TestClient(app) as client:
        created=client.post('/v1/viewer/traffic/simulation',json={'seconds':1})
        assert created.status_code==200
        body=created.json(); key=body['session']
        frame=body['frames'][-1];assert_accounting(frame)
        assert frame['vehicles'][0][0]=='api-car'
        result=client.post(f'/v1/viewer/traffic/simulation/{key}',json={'seconds':1,'closed':[1]})
        assert result.status_code==200
        frame=result.json()['frames'][-1];assert_accounting(frame)
        assert frame['metrics']['rerouted']==1
        assert frame['vehicles'][0][0]=='api-car'
        assert client.post(f'/v1/viewer/traffic/simulation/{key}',json={'hour':9}).status_code==409
        assert client.post(f'/v1/viewer/traffic/simulation/{key}',json={'continuous':True}).status_code==409
        assert client.post(f'/v1/viewer/traffic/simulation/{key}',json={'demand_scale':2}).status_code==409
        assert client.delete(f'/v1/viewer/traffic/simulation/{key}').status_code==200
        assert client.post(f'/v1/viewer/traffic/simulation/{key}',json={}).status_code==404
        assert made[0].connections==[]
        continuous=client.post('/v1/viewer/traffic/simulation',json={'continuous':True,'seconds':1})
        assert continuous.status_code==200
        assert continuous.json()['frames'][-1]['metrics']['demand']['mode']=='boundary_inflow'
        assert made[-1].continuous is True


def test_continuous_entries_keep_arriving_with_identical_paired_demand(network):
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    with_entries=Simulation(network,trips=[],continuous=True,seed=7)
    try:
        early=0
        for i in range(12):
            result=with_entries.advance(road_events(network),10)
            for frame in result['frames']:
                assert_accounting(frame)
                m=frame['metrics']
                assert m['baseline']=={k:m[k] for k in ('active','pending','arrived','stopped')}
                assert m['mean_completed_delay_s'] in (None,0)
            if i==5:
                early=result['frames'][-1]['metrics']['total']
        m=result['frames'][-1]['metrics']
        assert m['total']>early>0
        assert m['demand']['mode']=='boundary_inflow'
        assert m['demand']['added_trips']==m['total']
        assert 0<=m['demand']['interior_trips']<=m['demand']['added_trips']
        assert m['arrived']>0
    finally: with_entries.close()


def test_continuous_demand_is_independent_of_request_chunk_size(network):
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    runs=[]
    for seconds in (10,1):
        sim=Simulation(network,trips=[],continuous=True,seed=31)
        try:
            for _ in range(round(40/seconds)):
                result=sim.advance(road_events(network),seconds)
                assert_accounting(result['frames'][-1])
            runs.append((dict(sim.routes),set(sim.trip_ids),result['frames'][-1]['metrics']['arrived']))
        finally: sim.close()
    assert runs[0]==runs[1]


def test_incoming_demand_multiplier_changes_real_entry_schedule_and_zero_stops_entries(network):
    from dynacity.traffic_sumo import BoundaryInflow
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    sim = Simulation(network, trips=[], continuous=True, demand_scale=0)
    try:
        assert sim.inflow.heap == []
        result = sim.advance(road_events(network), 2)
        assert result['frames'][-1]['metrics']['total'] == 0
        normal = BoundaryInflow(network, sim.net, 8, 42, router=sim.inflow.router)
        double = BoundaryInflow(network, sim.net, 8, 42, router=sim.inflow.router, demand_scale=2)
        assert double.expected_vph == pytest.approx(normal.expected_vph*2)
        assert double.interior_vph == pytest.approx(normal.interior_vph*2)
        assert len(double.heap) == len(normal.heap)
        assert double.heap[0][0] == pytest.approx(normal.heap[0][0]/2)
    finally:
        sim.close()


def test_closed_inflow_origin_queues_new_trips_and_reopens_without_losses(network):
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    sim=Simulation(network,trips=[],continuous=True,seed=7)
    try:
        # Every road a trip can start on, at the fringe or inside.
        entry_roads=sorted({int(edge.split(':')[0]) for _,_,edge,_ in sim.inflow.heap})
        for _ in range(6):
            result=sim.advance(road_events(network,closed=entry_roads),10)
            for frame in result['frames']:
                assert_accounting(frame)
        m=result['frames'][-1]['metrics']
        assert m['pending']>0 and m['active']==0
        assert m['pending']==m['total']
        for _ in range(6):
            result=sim.advance(road_events(network),10)
            assert_accounting(result['frames'][-1])
        assert result['frames'][-1]['metrics']['arrived']>0
    finally: sim.close()


def test_closing_a_road_with_cars_waiting_to_enter_on_it_does_not_abort_sumo(network):
    """SUMO aborts if a queued car's departure road closes; such cars wait in the entry queue instead."""
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    trips=[Trip(f'queued-{i}',('6:1','0:1','1:1','5:1'),0,0) for i in range(40)]
    sim=Simulation(network,trips=trips)
    try:
        sim.advance(road_events(network),1)
        assert sim.connections[0].simulation.getPendingVehicles()
        for _ in range(3):
            result=sim.advance(road_events(network,closed=[6]),5)
            for frame in result['frames']:
                assert_accounting(frame)
        assert sim.entry_queue
        for _ in range(20):
            result=sim.advance(road_events(network),5)
            assert_accounting(result['frames'][-1])
        m=result['frames'][-1]['metrics']
        assert not sim.entry_queue and m['arrived']>0 and m['total']==40
    finally: sim.close()


def test_new_entries_reroute_around_an_existing_interior_closure(network):
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    sim=Simulation(network,trips=[],continuous=True,seed=7)
    try:
        for _ in range(10):
            result=sim.advance(road_events(network,closed=[1]),5)
            for frame in result['frames']:
                assert_accounting(frame)
        assert result['frames'][-1]['metrics']['total']>0
    finally: sim.close()


def test_route_setup_keeps_child_environment_after_worker_threads_exit():
    """pyproj empties the environment when its importing thread exits; the routes import it up front."""
    import os
    import subprocess
    import sys
    pytest.importorskip("sumolib")
    script = (
        "import os, subprocess, sys, threading\n"
        "from fastapi import FastAPI\n"
        "from dynacity.traffic_sumo import install_routes\n"
        "install_routes(FastAPI(), None, None)\n"
        "t = threading.Thread(target=lambda: __import__('sumolib')); t.start(); t.join()\n"
        "out = subprocess.run([sys.executable, '-c', \"import os; print(os.environ.get('DYNACITY_TEST_MARKER', ''))\"], capture_output=True, text=True, check=True).stdout\n"
        "print(out.strip())\n"
    )
    env = dict(os.environ, DYNACITY_TEST_MARKER="retained")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "retained"


def test_comparison_run_starts_as_an_exact_copy_at_the_first_disruption(network):
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    sim=Simulation(network,trips=[],continuous=True,seed=7)
    try:
        assert not sim.subscribed[1]
        for _ in range(3):
            sim.advance(road_events(network),5)
        assert not sim.forked
        assert not sim.subscribed[1]
        assert len(sim.connections) == 1
        # A closure on a road no car uses yet: the copy must match the scenario.
        result=sim.advance(road_events(network,closed=[3]),5)
        assert sim.forked and len(sim.connections) == 2
        assert sim.connections[1].simulation.getTime() == sim.connections[0].simulation.getTime()
        assert sim.subscribed[1]
        for _ in range(4):
            result=sim.advance(road_events(network,closed=[3]),5)
            for frame in result['frames']:
                assert_accounting(frame)
        m=result['frames'][-1]['metrics']
        assert m['total']>0 and m['baseline']['arrived']>0
    finally: sim.close()


def test_frames_send_only_cars_in_view_but_count_all(simulation,network):
    everything=simulation.advance(road_events(network),2)['frames'][-1]
    lons=sorted(v[1] for v in everything['vehicles'])
    view=[lons[0]-1,-90,(lons[0]+lons[-1])/2 if lons[0]<lons[-1] else lons[0]-0.5,90]
    frame=simulation.advance(road_events(network),2,view=view)['frames'][-1]
    assert frame['metrics']['active']==everything['metrics']['active']
    assert frame['metrics']['sent']==len(frame['vehicles'])<=frame['metrics']['active']
    assert all(view[0]<=v[1]<=view[2] for v in frame['vehicles'])
    capped=simulation.advance(road_events(network),2,limit=1)['frames']
    assert all(len(f['vehicles'])<=1 for f in capped)
    assert len({f['vehicles'][0][0] for f in capped if f['vehicles']})<=1


def test_view_subscription_refreshes_on_steps_and_changes_only_with_view(simulation, network, monkeypatch):
    poi = simulation.connections[0].poi
    original = poi.subscribeContext
    calls = []
    def count_subscriptions(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(poi, 'subscribeContext', count_subscriptions)
    view = [35, 33, 36, 35]
    first = simulation.advance(road_events(network), 2, view=view)['frames']
    second = simulation.advance(road_events(network), 2, view=view)['frames']
    assert len(calls) == 1
    assert first[-1]['vehicles'] and second[-1]['vehicles']
    assert first[-1]['vehicles'][0][1:3] != second[-1]['vehicles'][0][1:3]
    shifted = [35.00001, 33, 36.00001, 35]
    nearby = simulation.advance(road_events(network), 2, view=shifted)['frames'][-1]
    assert len(calls) == 1
    assert all(shifted[0] <= car[1] <= shifted[2] and shifted[1] <= car[2] <= shifted[3]
               for car in nearby['vehicles'])
    simulation.advance(road_events(network), 2, view=[34.9, 33, 36, 35])
    assert len(calls) == 2
    full = simulation.advance(road_events(network), 2, view=None)['frames'][-1]
    assert not simulation.watching
    assert len(full['vehicles']) == full['metrics']['active']


def test_moving_view_reuse_matches_forced_refresh(network):
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    trips = [Trip('a', ('0:1', '1:1', '5:1'), 0, 20),
             Trip('b', ('0:1', '1:1', '5:1'), 0, 5)]
    results = []
    for force_refresh in (False, True):
        sim = Simulation(network, trips=trips)
        try:
            original = sim.watch
            if force_refresh:
                def watch(c):
                    if sim.watching:
                        sim.watch_centre = (float('inf'), float('inf'))
                    return original(c)
                sim.watch = watch
            batches = []
            for i, shift in enumerate((0, .00001, .00002, .00003)):
                view = [35 + shift, 33, 36 + shift, 35]
                disruption = road_events(network, closed=[3] if i >= 2 else [])
                batches.append(sim.advance(disruption, 2, view=view)['frames'])
            results.append(batches)
        finally:
            sim.close()
    assert results[0] == results[1]


def test_fitted_coordinates_match_the_exact_conversion():
    from dynacity.traffic_sumo import lonlat_fit
    from dynacity.viewer import utm_to_lonlat
    # About Beirut's extent in UTM 36N, with a network offset.
    offset=(-730000.0,-3740000.0)
    fit=lonlat_fit(((0,0),(9500,8800)),offset,36,True)
    assert fit is not None
    rng=np.random.default_rng(1)
    x,y=rng.uniform(0,9500,200),rng.uniform(0,8800,200)
    exact=np.array([utm_to_lonlat(a-offset[0],b-offset[1],36,True) for a,b in zip(x,y)])
    assert np.abs(fit(x,y)-exact).max()<1e-7


def test_forked_copy_without_changes_matches_the_scenario_exactly(network):
    try: runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    sim=Simulation(network,trips=[],continuous=True,seed=11)
    try:
        sim.advance(road_events(network),5)
        sim.fork()
        for _ in range(8):
            result=sim.advance(road_events(network),5)
            for frame in result['frames']:
                m=frame['metrics']
                assert m['baseline']=={key:m[key] for key in ('active','pending','arrived','stopped')}
        assert m['paired_arrivals']>0 and m['mean_completed_delay_s']==0
    finally: sim.close()


def test_edge_router_matches_sumolib_shortest_paths(network,tmp_path):
    from dynacity.traffic_sumo import EdgeRouter, export_network
    try: _, _, converter = runtime()
    except RuntimeError as exc: pytest.skip(str(exc))
    import sumolib
    net=sumolib.net.readNet(str(export_network(network,tmp_path,converter)))
    router=EdgeRouter(net)
    for origin in net.getEdges():
        for target in net.getEdges():
            found,_=net.getShortestPath(origin,target)
            expected=tuple(e.getID() for e in found) if found else ()
            assert router(origin.getID(),target.getID())==expected


def test_network_conversion_cache_reuses_only_matching_inputs(network, tmp_path, monkeypatch):
    from dataclasses import replace
    import dynacity.traffic_sumo as adapter
    try:
        _, _, converter = runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with adapter._NETWORK_CACHE_LOCK:
        adapter._NETWORK_CACHE.clear()
    original = adapter.subprocess.run
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(adapter.subprocess, 'run', counted)
    paths = [tmp_path / str(i) for i in range(3)]
    for path in paths: path.mkdir()
    first = adapter.export_network(network, paths[0], converter).read_bytes()
    second = adapter.export_network(network, paths[1], converter).read_bytes()
    changed = replace(network, free_kmh=network.free_kmh + 1)
    third = adapter.export_network(changed, paths[2], converter).read_bytes()
    assert first == second
    assert first != third
    assert len(calls) == 2


def test_cached_network_preserves_simulation_frames(network, monkeypatch):
    import dynacity.traffic_sumo as adapter
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with adapter._NETWORK_CACHE_LOCK:
        adapter._NETWORK_CACHE.clear()
    converter_calls = []
    original_run = adapter.subprocess.run
    def counted(args, *rest, **kwargs):
        if '--node-files' in args:
            converter_calls.append(1)
        return original_run(args, *rest, **kwargs)
    monkeypatch.setattr(adapter.subprocess, 'run', counted)
    frames = []
    for _ in range(2):
        sim = Simulation(network, trips=[Trip('a', ('0:1', '1:1', '5:1'), 0, 20)])
        try:
            frames.append(sim.advance(road_events(network), 3)['frames'])
        finally:
            sim.close()
    assert len(converter_calls) == 1
    assert frames[0] == frames[1]


def test_prewarmed_network_is_reused_by_first_session(network, monkeypatch):
    import dynacity.traffic_sumo as adapter
    try:
        runtime()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with adapter._NETWORK_CACHE_LOCK:
        adapter._NETWORK_CACHE.clear()
    calls = []
    original = adapter.subprocess.run
    def counted(args, *rest, **kwargs):
        if '--node-files' in args: calls.append(1)
        return original(args, *rest, **kwargs)
    monkeypatch.setattr(adapter.subprocess, 'run', counted)
    assert adapter.prewarm_network(network)
    sim = Simulation(network, trips=[Trip('a', ('0:1', '1:1', '5:1'), 0, 20)])
    try:
        assert sim.advance(road_events(network), 2)['frames']
    finally:
        sim.close()
    assert len(calls) == 1
