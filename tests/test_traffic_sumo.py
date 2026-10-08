"""Real SUMO integration tests; skip explicitly when the optional runtime is absent."""
import numpy as np
import pytest
from dynacity.traffic import TrafficModel, road_events
from dynacity.traffic_sumo import Simulation, Trip, runtime

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

def test_closed_destination_retains_fleet_and_reopen_completes(simulation,network):
    closed=road_events(network,closed=[5]); opened=road_events(network)
    result=simulation.advance(closed,10)
    for _ in range(5): result=simulation.advance(closed,10)
    frame=result['frames'][-1]; assert_accounting(frame)
    assert frame['metrics']['arrived']==0
    assert frame['metrics']['no_detour']==2
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

def test_identical_baseline_has_zero_delay(simulation,network):
    for _ in range(8): result=simulation.advance(road_events(network),10)
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
        assert c.vehicle.getRoadID('signal')=='0:1'
        assert c.vehicle.getSpeed('signal')<0.1
        c.trafficlight.setRedYellowGreenState('1','G'*len(state))
        for _ in range(8): result=sim.advance(road_events(m),10)
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
    def small_fleet(model,hour):
        sim=original(model,hour,trips=[Trip('api-car',('0:1','1:1','5:1'),0,20)])
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
        assert client.delete(f'/v1/viewer/traffic/simulation/{key}').status_code==200
        assert client.post(f'/v1/viewer/traffic/simulation/{key}',json={}).status_code==404
        assert made[0].connections==[]
