"""Adapter invariants: map scale, bridge grade, clock identity and session ownership."""
import io
import json
from pathlib import Path
import sys
import numpy as np
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python'
sys.path.insert(0,str(SCRIPTS))
import location_core
from mobility_core import map_payload,traffic_view
from traffic_client import TrafficClient


def index():
    x,y=location_core.lonlat_to_utm(35.52,33.89)
    return {'origin_utm_m':[x,y,10],'roads':[
        {'id':1,'path':[[0,0,1],[50,0,6],[100,0,11]]},
        {'id':2,'path':[[0,0,21],[100,0,21]]}]}


def test_map_simplifies_without_changing_endpoints_or_source():
    data=index();original=json.dumps(data);result=map_payload(data)
    assert len(result['roads'][0]['path'])==2
    assert result['roads'][0]['path']==[data['roads'][0]['path'][0],data['roads'][0]['path'][-1]]
    assert json.dumps(data)==original


def test_map_payload_registers_native_and_web_maps_to_the_same_utm_origin():
    data = index()
    payload = map_payload(data)
    assert payload['origin_utm_m'] == data['origin_utm_m']
    assert payload['origin_utm_m'] is not data['origin_utm_m']
    assert payload['map_reference']['crs'] == 'EPSG:32636'
    assert payload['map_reference']['origin_lonlat'] == pytest.approx([35.52, 33.89], abs=1e-7)
    # The same local point round-trips through the basemap without flipping axes.
    point = [2100, -1500]
    lonlat = location_core.utm_to_lonlat(*(data['origin_utm_m'][i] + point[i] for i in range(2)))
    projected = location_core.lonlat_to_utm(*lonlat)
    assert [projected[i] - payload['origin_utm_m'][i] for i in range(2)] == pytest.approx(point, abs=.001)


@pytest.mark.parametrize('origin', [None, [], [True, 3750000], [float('nan'), 3750000],
                                    [731000, float('inf')], [0, 3750000], [731000, -1]])
def test_map_payload_does_not_guess_missing_or_invalid_georeferencing(origin):
    data = index()
    data['origin_utm_m'] = origin
    with pytest.raises(ValueError, match='EPSG:32636'):
        map_payload(data)


def test_view_is_in_beirut_and_has_metric_radius():
    data=index();view=traffic_view(data,[200,300],radius=900)
    west,south=location_core.lonlat_to_utm(*view[:2]);east,north=location_core.lonlat_to_utm(*view[2:])
    assert east-west==pytest.approx(1800,abs=.01)
    assert north-south==pytest.approx(1800,abs=.01)
    assert west==pytest.approx(data['origin_utm_m'][0]-700,abs=.01)


def test_adapter_uses_own_bridge_grade_and_rejects_unmodeled_roads():
    data=index();client=TrafficClient(data,'http://127.0.0.1:8014',[35,33,36,34])
    lon,lat=location_core.utm_to_lonlat(data['origin_utm_m'][0]+50,data['origin_utm_m'][1])
    assert client._position(lon,lat,'1:0')[2]==pytest.approx(6,abs=.001)
    assert client._position(lon,lat,'2:0')[2]==21
    assert client._position(lon,lat,'999:0') is None


@pytest.mark.parametrize('bits', [0, 1, 2, 4, 8, 10, 31, None])
def test_vehicle_lights_preserve_zero_and_missing_source_data(monkeypatch, bits):
    data=index();lon,lat=location_core.utm_to_lonlat(*data['origin_utm_m'][:2])
    row=['car',lon,lat,90,5,'1:0',0,'car']
    if bits is not None:row.append(bits)
    body={'session':'owned','frames':[{'time_s':12.5,'vehicles':[row]}]}
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:io.BytesIO(json.dumps(body).encode()))
    client=TrafficClient(data,'http://127.0.0.1:8014',[35,33,36,34])
    vehicle=json.loads(client.batch())['frames'][0]['vehicles'][0]
    if bits is None:assert 'light_bits' not in vehicle
    else:assert vehicle['light_bits']==bits


@pytest.mark.parametrize('bits', [True, -1, 1.5, 16384, '8'])
def test_vehicle_lights_reject_invalid_source_bits(monkeypatch, bits):
    data=index();lon,lat=location_core.utm_to_lonlat(*data['origin_utm_m'][:2])
    body={'session':'owned','frames':[{'time_s':1,'vehicles':[['car',lon,lat,0,0,'1:0',0,'car',bits]]}]}
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:io.BytesIO(json.dumps(body).encode()))
    with pytest.raises(ValueError,match='signal bitmask'):
        TrafficClient(data,'http://127.0.0.1:8014',[35,33,36,34]).batch()


def test_session_preserves_time_and_identity_and_releases_only_owned_session(monkeypatch):
    calls=[];data=index();lon,lat=location_core.utm_to_lonlat(*data['origin_utm_m'][:2])
    def urlopen(request,timeout):
        calls.append((request.get_method(),request.full_url))
        if request.get_method()=='DELETE':return io.BytesIO(b'{}')
        return io.BytesIO(json.dumps({'session':'owned','frames':[
            {'time_s':12.5,'vehicles':[['car',lon,lat,90,5,'1:0',0,'bus']]},
            {'time_s':13,'vehicles':[['car',lon,lat,90,5,':junction']]}]}).encode())
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    client=TrafficClient(data,'http://127.0.0.1:8014',[35,33,36,34]);result=json.loads(client.batch())
    assert [f['time_s'] for f in result['frames']]==[12.5,13]
    assert [f['vehicles'][0]['id'] for f in result['frames']]==['car','car']
    assert result['frames'][0]['vehicles'][0]['type_id']=='bus'
    assert 'type_id' not in result['frames'][1]['vehicles'][0]
    assert (client.last_batch_received,client.last_batch_placed)==(2,2)
    client.close();client.close()
    assert calls[-1]==('DELETE','http://127.0.0.1:8014/v1/viewer/traffic/simulation/owned')
    assert sum(method=='DELETE' for method,_ in calls)==1


def test_adapter_counts_unplaceable_vehicle_samples(monkeypatch):
    data=index();lon,lat=location_core.utm_to_lonlat(*data['origin_utm_m'][:2])
    body={'session':'owned','frames':[{'time_s':1,'vehicles':[['car',lon,lat,0,0,'999:0']]}]}
    monkeypatch.setattr('urllib.request.urlopen',lambda request,timeout:io.BytesIO(json.dumps(body).encode()))
    client=TrafficClient(data,'http://127.0.0.1:8014',[35,33,36,34])
    result=json.loads(client.batch())
    assert result['frames'][0]['vehicles']==[]
    assert (client.last_batch_received,client.last_batch_placed)==(1,0)


def test_adapter_uses_only_identity_matched_road_mapping(monkeypatch):
    import hashlib
    data=index();lon,lat=location_core.utm_to_lonlat(*data['origin_utm_m'][:2])
    digest=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    mapping={'schema':'dynacity-traffic-road-map/1','city_index_identity':digest,
             'source_road_identity':'source','roads':{'99':'1'}}
    body={'session':'owned','network':{'road_identity_sha256':'source'},'frames':[
        {'time_s':1,'vehicles':[['car',lon,lat,0,0,'99:1',0,'taxi']]},
        {'time_s':2,'vehicles':[['car',lon,lat,0,0,':junction',0,'taxi']]}]}
    monkeypatch.setattr('urllib.request.urlopen',lambda request,timeout:io.BytesIO(json.dumps(body).encode()))
    client=TrafficClient(data,'http://127.0.0.1:8014',[35,33,36,34],road_mapping=mapping)
    frames=json.loads(client.batch())['frames']
    assert all(frame['vehicles'][0]['type_id']=='taxi' for frame in frames)
    assert client.last_batch_placed==2
    body['network']['road_identity_sha256']='different'
    with pytest.raises(ValueError,match='SUMO road model'):
        client.batch()
    mapping['city_index_identity']='different'
    with pytest.raises(ValueError,match='city index'):
        TrafficClient(data,'http://127.0.0.1:8014',[35,33,36,34],road_mapping=mapping)


def test_coverage_preserves_ground_holes():
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    from report_beirut_coverage import terrain_footprint
    from dynacity.terrain import GroundModel
    z=np.ones((4,4));z[1,1]=np.nan
    ground=GroundModel(crs='EPSG:32636',x0=0,y0=40,cell_m=10,ground=z,source='test')
    assert terrain_footprint(ground).area==500


def test_followup_batch_omits_session_id_and_view_moves_without_restarting(monkeypatch):
    calls=[]
    def urlopen(request,timeout):
        calls.append((request.get_method(),request.full_url,json.loads(request.data) if request.data else None))
        if request.get_method()=='DELETE':return io.BytesIO(b'{}')
        body={'frames':[{'time_s':len(calls),'vehicles':[]}]}
        if len(calls)==1:body['session']='owned'
        return io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    original=[35.50,33.88,35.52,33.90];moved=[35.52,33.88,35.54,33.90]
    client=TrafficClient(index(),'http://127.0.0.1:8014',original)
    assert json.loads(client.batch())['frames'][0]['time_s']==1
    assert json.loads(client.batch(moved))['frames'][0]['time_s']==2
    assert client.session=='owned'
    assert calls[0][1].endswith('/simulation')
    assert calls[1][1].endswith('/simulation/owned')
    assert calls[1][2]['view']==moved
    assert client.request['view']==original
    client.close()
    assert calls[-1][1].endswith('/simulation/owned')


def test_unexpected_session_cannot_replace_owned_session(monkeypatch):
    client=TrafficClient(index(),'http://127.0.0.1:8014',[35,33,36,34]);client.session='owned'
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:io.BytesIO(b'{"session":"other","frames":[]}'))
    with pytest.raises(ValueError,match='changed the owned session'):client.batch()
    assert client.session=='owned'


@pytest.mark.parametrize('view',[[35,33,34,34],[35,33,float('nan'),34],[True,33,36,34]])
def test_invalid_moving_view_does_not_make_a_request(view,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('No request should be made')
    monkeypatch.setattr('urllib.request.urlopen',forbidden)
    with pytest.raises(ValueError,match='view bounds'):
        TrafficClient(index(),'http://127.0.0.1:8014',[35,33,36,34]).batch(view)


def test_failed_batch_clears_stale_native_vehicles(monkeypatch):
    import importlib.util
    from concurrent.futures import Future
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules,'unreal',SimpleNamespace())
    spec=importlib.util.spec_from_file_location('mobility_runtime_under_test',SCRIPTS/'mobility_runtime.py')
    runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
    cleared=[]
    pawn=SimpleNamespace(night_mode=False)
    runtime._pawn=pawn;runtime._night=False;runtime._enabled=True
    runtime._renderer=SimpleNamespace(clear_traffic=lambda:cleared.append(True),status=lambda:'{}')
    runtime._client=SimpleNamespace(session=None)
    runtime._future=Future();runtime._future.set_exception(RuntimeError('backend unavailable'))
    runtime.tick(pawn)
    assert cleared==[True]
    assert runtime.status()['enabled'] is False
    assert 'backend unavailable' in runtime.status()['message']
    runtime._executor.shutdown(wait=False)


def test_unplaceable_batch_reports_road_mismatch(monkeypatch):
    import importlib.util
    from concurrent.futures import Future
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules,'unreal',SimpleNamespace())
    spec=importlib.util.spec_from_file_location('mobility_runtime_mismatch_test',SCRIPTS/'mobility_runtime.py')
    runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
    pawn=SimpleNamespace(night_mode=False)
    runtime._pawn=pawn;runtime._night=False;runtime._enabled=True
    runtime._renderer=SimpleNamespace(append_frames=lambda payload:True,status=lambda:'{"buffer_s":10}')
    runtime._client=SimpleNamespace(last_batch_received=10,last_batch_placed=0)
    runtime._future=Future();runtime._future.set_result('{"frames":[]}')
    runtime.tick(pawn)
    assert 'could not be placed' in runtime.status()['message']
    runtime._executor.shutdown(wait=False)
