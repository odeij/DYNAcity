"""Exercise the compiled vehicle through Unreal's local MCP runtime tools.

Requires prepared drive starts. Ends Play on completion or failure. Keyboard
focus and visual quality still require inspection in the editor.
"""
import argparse
import json
import math
from pathlib import Path
import time

from unreal_mcp_client import call


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8016/mcp')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    events=[]

    def invoke(name,**arguments):
        response=call(args.url,'tools/call',{'name':'call_tool','arguments':{
            'toolset_name':'driving_tools.DrivingTools','tool_name':name,'arguments':arguments}})
        result=response.get('result',{})
        if response.get('error') or result.get('isError'):raise RuntimeError(response)
        payload=json.loads(result['content'][0]['text'])
        value=payload.get('returnValue',payload)
        if isinstance(value,str):
            try:value=json.loads(value)
            except ValueError:pass
        events.append({'operation':name,'result':value})
        return value

    def status():return invoke('status')['driving']

    def ready():
        for _ in range(40):
            state=status()
            if state and state['surface_ready']:return state
            time.sleep(.5)
        raise RuntimeError('Source surface did not become available')

    def position(state):return [state[key] for key in ('x_cm','y_cm','z_cm')]

    checks={};error=None;owns_play=False
    try:
        if invoke('status').get('playing'):raise RuntimeError('End existing Play before verification')
        invoke('play');owns_play=True;time.sleep(2)
        initial=ready()
        invoke('input',throttle=1.0,steering=0.0,brake=False);time.sleep(1.5)
        moving=status()
        checks['forward_motion']=math.dist(position(initial),position(moving))>25 and moving['speed_kmh']>0
        invoke('input',throttle=0.0,steering=0.0,brake=True);time.sleep(2)
        checks['braking']=abs(status()['speed_kmh'])<.1
        invoke('reset');time.sleep(.5)
        reset=ready()
        checks['reset']=math.dist(position(initial),position(reset))<2
        invoke('input',throttle=1.0,steering=.2,brake=False);time.sleep(1)
        turning=status()
        heading_change=(turning['yaw_degrees']-reset['yaw_degrees']+180)%360-180
        checks['steering']=heading_change>.2
        invoke('reset');time.sleep(.5);ready()
        invoke('input',throttle=-1.0,steering=0.0,brake=False);time.sleep(.6)
        checks['reverse']=status()['speed_kmh']<0
        invoke('reset');time.sleep(.5);ready()
        views=[invoke('camera')['camera_view'] for _ in range(4)]
        checks['camera_cycle']=views==[1,2,3,0]
        areas=[status()['start_area']]
        for _ in range(11):
            invoke('next_area');time.sleep(.5)
            areas.append(ready()['start_area'])
        checks['twelve_supported_areas']=len(set(areas))==12
        if not all(checks.values()):raise RuntimeError('One or more runtime checks failed')
    except Exception as exception:
        error=str(exception)
    finally:
        # A failed request must not leave externally held throttle active.
        if owns_play:
            try:invoke('release')
            except Exception:pass
            try:invoke('stop')
            except Exception:pass
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps({'passed':bool(checks) and all(checks.values()) and error is None,
            'checks':checks,'error':error,'events':events,
            'limits':['Keyboard focus, appearance and continuous district streaming are not tested by these commands.']},indent=2),encoding='utf-8')
    print(json.dumps({'checks':checks,'error':error,'output':str(output)}))
    if error:raise SystemExit(1)


if __name__=='__main__':main()
