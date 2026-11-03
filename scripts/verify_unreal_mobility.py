"""Verify the installed native map, cockpit, night state and advancing SUMO clock."""
import argparse
import base64
import json
import os
from pathlib import Path
import time
from unreal_mcp_client import call


def invoke(toolset,name,**arguments):
    response=call(os.environ.get('DYNACITY_UNREAL_MCP_URL','http://127.0.0.1:8016/mcp'),'tools/call',{'name':'call_tool','arguments':{
        'toolset_name':toolset,'tool_name':name,'arguments':arguments}},timeout=45)
    result=response.get('result',{})
    if response.get('error') or result.get('isError'):raise RuntimeError(str(response)[:1000])
    value=json.loads(result['content'][0]['text']).get('returnValue')
    if isinstance(value,str):
        try:return json.loads(value)
        except ValueError:pass
    return value


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    parser.add_argument('--use-current',action='store_true',help='Only for an agent-owned verification Play session')
    args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    drive='driving_tools.DrivingTools';mobility='mobility_tools.MobilityTools'
    checks={};samples=[];error=None;owns_play=False
    def status():return invoke(drive,'status')['driving']
    def capture(name):
        value=invoke('EditorToolset.EditorAppToolset','CaptureEditorImage')
        (out/(name+'.png')).write_bytes(base64.b64decode(value['data']))
    try:
        playing=invoke(drive,'status').get('playing')
        if playing and not args.use_current:raise RuntimeError('A Play session already exists; refusing to interrupt it.')
        if not playing:invoke(drive,'play')
        owns_play=True
        for _ in range(30):
            state=status()
            if state['surface_ready'] and json.loads(state['mobility']).get('roads'):break
            time.sleep(.5)
        checks['source_surface']=state['surface_ready']
        checks['map_roads']=json.loads(state['mobility']).get('roads',0)>0
        checks['cockpit_asset']=state['cockpit_available']
        checks['left_driver_eye']=state.get('driver_eye_y_cm',0)<0
        assets=invoke(mobility,'status').get('assets',{})
        checks['authored_left_hand_cabin']=(assets.get('handedness')=='left' and
            assets.get('steering_y_cm',0)<-15 and assets.get('cockpit_scale')==[1,1,1] and
            abs(assets.get('eye_y_cm',0)-assets.get('steering_y_cm',0))<10)
        capture('chase-day')
        views=[invoke(drive,'camera')['camera_view'] for _ in range(3)]
        checks['cockpit_camera']=views==[1,2,3]
        time.sleep(1);capture('cockpit-day')
        invoke(mobility,'night',enabled=True);time.sleep(1)
        checks['night_enabled']=status()['night_mode'];capture('cockpit-night')
        invoke(mobility,'night',enabled=False)
        checks['day_restored']=not status()['night_mode']
        invoke(mobility,'traffic',enabled=True)
        deadline=time.monotonic()+65
        while time.monotonic()<deadline:
            state=invoke(mobility,'status')['mobility'];samples.append(state)
            if state['enabled'] and state['render'].get('visible_vehicles',0)>0:break
            if 'failed' in state['message']:raise RuntimeError(state['message'])
            time.sleep(1)
        first=state['render']
        # A first buffered batch can animate briefly even when all later requests
        # fail. Require several successful follow-up batches and sustained time.
        # Editor focus/throttling and finished-asset loading can make simulation
        # time slower than wall time. Keep the same simulation/batch criteria.
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            state=invoke(mobility,'status')['mobility'];samples.append(state)
            if not state['enabled']:raise RuntimeError(state['message'])
            if state.get('batches_received',0)>=5 and state['render']['time_s']-first['time_s']>=8:break
            time.sleep(1)
        second=state['render']
        checks['sumo_visible']=first.get('visible_vehicles',0)>0
        checks['sumo_clock_advances']=second.get('time_s',-1)>first.get('time_s',-1)
        checks['sumo_sustained_batches']=state['enabled'] and state.get('batches_received',0)>=5 and second['time_s']-first['time_s']>=8
        checks['bounded_instances']=0<second.get('visible_vehicles',0)<=150
        capture('cockpit-sumo-day')
        previous_view=state['view_lonlat'];previous_batches=state['batches_received'];previous_time=second['time_s']
        invoke(drive,'next_area')
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            moved=invoke(mobility,'status')['mobility']
            if not moved['enabled']:raise RuntimeError(moved['message'])
            if moved['view_lonlat']!=previous_view and moved['batches_received']>=previous_batches+2:break
            time.sleep(1)
        checks['sumo_view_follows_car']=(moved['view_lonlat']!=previous_view and
            moved['batches_received']>=previous_batches+2 and moved['render']['time_s']>previous_time)
        checks['new_area_surface']=status()['surface_ready']
        samples.append({'after_area_change':moved})
        invoke(mobility,'traffic',enabled=False);time.sleep(1)
        checks['disconnect_clears_instances']=invoke(mobility,'status')['mobility']['render']['visible_vehicles']==0
        if not all(checks.values()):raise RuntimeError('A native mobility check failed.')
    except Exception as exception:error=str(exception)
    finally:
        if owns_play:
            try:invoke(mobility,'traffic',enabled=False)
            except Exception:pass
            try:invoke(drive,'stop')
            except Exception:pass
        report={'passed':bool(checks) and all(checks.values()) and error is None,'checks':checks,
            'error':error,'traffic_samples':samples,'limits':['Editor-hosted native integration, not a packaged game.',
                'Traffic instances are decorative; no interaction with player physics.',
                'No matched before/after frame-time performance claim.']}
        (out/'native-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'passed':report['passed'],'checks':checks,'error':error}))
    if error:raise SystemExit(1)


if __name__=='__main__':main()
