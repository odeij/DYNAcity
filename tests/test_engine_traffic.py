import pytest
import io
import json
from dynacity.engine_traffic import SceneTraffic
from dynacity.engine_traffic import record
from dynacity.viewer import lonlat_to_utm,utm_to_lonlat


def converter():
    x,y=lonlat_to_utm(35.5,33.88,36,True)
    return SceneTraffic({'coordinates':{'origin_utm_m':[x,y,20]},'roads':[
        {'id':1,'path':[[0,0,1],[100,0,11]]},
        {'id':2,'path':[[0,2,21],[100,2,21]]}]}),x,y


def test_vehicle_uses_own_road_grade_at_overlapping_bridge():
    view,x,y=converter();lon,lat=utm_to_lonlat(x+50,y,36,True)
    assert view.position(lon,lat,'1:0')[2]==pytest.approx(6,abs=.001)
    assert view.position(lon,lat,'2:0')[2]==pytest.approx(21,abs=.001)


def test_junction_vehicle_finds_nearby_road_but_far_vehicle_is_omitted():
    view,x,y=converter();lon,lat=utm_to_lonlat(x+50,y+.5,36,True)
    assert view.position(lon,lat,':junction')[2]==pytest.approx(6,abs=.001)
    lon,lat=utm_to_lonlat(x+500,y+500,36,True)
    assert view.position(lon,lat,':junction') is None


def test_frame_keeps_authoritative_time_identity_lights_and_signal_state():
    view,x,y=converter();lon,lat=utm_to_lonlat(x+20,y,36,True)
    result=view.frame({'time_s':12.4,'vehicles':[['car',lon,lat,90,8,'1:0',0,'car',9]],
                       'signals':[['junction',2,lon,lat,'r',False]],'metrics':{'active':700}})
    assert result['time_s']==12.4
    assert result['vehicles'][0]['id']=='car'
    assert result['vehicles'][0]['light_bits']==9
    assert result['signals'][0]['state']=='r'
    assert result['metrics']['active']==700


def test_record_releases_its_session_when_later_request_fails(monkeypatch):
    calls=[]
    def urlopen(request,timeout):
        calls.append((request.get_method(),request.full_url))
        if request.get_method()=='DELETE':return io.BytesIO(b'{}')
        if len(calls)>1:raise RuntimeError('simulated request failure')
        return io.BytesIO(json.dumps({'session':'owned-session','frames':[{'time_s':0,'vehicles':[],'signals':[]}]}).encode())
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    scene={'coordinates':{'origin_utm_m':[0,0,0]},'roads':[],'bounds_lonlat':[35,33,36,34]}
    with pytest.raises(RuntimeError,match='request failure'):
        record(scene,'http://127.0.0.1:8014',seconds=4)
    assert calls[-1]==('DELETE','http://127.0.0.1:8014/v1/viewer/traffic/simulation/owned-session')
