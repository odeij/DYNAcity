"""Sample one owned driving session under repeatable day/night/traffic conditions.

The existing pawn reports world delta time, not GPU duration. Keep that label
explicit: this measures tick cadence and RPC latency, not an isolated GPU FPS test.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
from verify_unreal_mobility import invoke


def summary(values):
    ordered=sorted(values)
    return {'count':len(values),'median':statistics.median(ordered),
            'p95':ordered[min(len(ordered)-1,int(.95*len(ordered)))],
            'max':max(ordered)}


def cadence_warnings(values):
    """Flag repeated world deltas near the apparent 333 ms reporting ceiling."""
    near_ceiling = sum(332.0 <= value <= 334.0 for value in values)
    if values and near_ceiling >= len(values) * 0.8:
        return ['World tick intervals cluster near 333 ms and may be clamped; '
                'this sample is unsuitable for comparing rendering performance.']
    return []


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--samples',type=int,default=30)
    args=parser.parse_args()
    if not 10<=args.samples<=120:parser.error('Use 10–120 samples per condition.')
    output=Path(args.output)
    if output.exists():raise FileExistsError(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    drive='driving_tools.DrivingTools';mobility='mobility_tools.MobilityTools'
    owns=False;phases=[];error=None
    try:
        if invoke(drive,'status')['playing']:raise RuntimeError('A Play session is already active; leave it undisturbed.')
        invoke(drive,'play');owns=True
        for _ in range(40):
            state=invoke(drive,'status').get('driving')
            if state and state['surface_ready'] and json.loads(state['mobility']).get('roads'):break
            time.sleep(.5)
        else:raise RuntimeError('Driving surface/map did not become ready.')
        for _ in range(3):invoke(drive,'camera')
        for traffic,night in ((False,False),(False,True),(True,False),(True,True)):
            if traffic and not any(p['traffic'] for p in phases):
                started=time.monotonic();invoke(mobility,'traffic',enabled=True)
                for _ in range(75):
                    status=invoke(mobility,'status')['mobility']
                    if not status['enabled']:raise RuntimeError(status['message'])
                    if status['render'].get('visible_vehicles',0):break
                    time.sleep(1)
                else:raise RuntimeError('Traffic did not appear before the profiling deadline.')
                connection_s=time.monotonic()-started
            invoke(mobility,'night',enabled=night);time.sleep(2)
            before=invoke(mobility,'status')['mobility'] if traffic else None
            samples=[]
            for _ in range(args.samples):
                started=time.perf_counter();state=invoke(drive,'status')['driving'];rpc_ms=(time.perf_counter()-started)*1000
                render=json.loads(state['mobility'])
                samples.append({'world_tick_ms':state['render_frame_ms'],
                    'game_tick_wall_ms':state.get('game_tick_wall_ms'),'rpc_ms':rpc_ms,
                    'vehicles':render['visible_vehicles'],'buffer_s':render['buffer_s'],'simulation_s':render['time_s']})
                time.sleep(.2)
            after=invoke(mobility,'status')['mobility'] if traffic else None
            if traffic and (not after['enabled'] or after['render']['time_s']-before['render']['time_s']<1):
                raise RuntimeError('Traffic stopped during profiling: '+after['message'])
            phase={'traffic':traffic,'night':night,'samples':samples,
                'world_tick_ms':summary([s['world_tick_ms'] for s in samples]),
                'warnings':cadence_warnings([s['world_tick_ms'] for s in samples]),
                'rpc_ms':summary([s['rpc_ms'] for s in samples]),
                'visible_vehicles':summary([s['vehicles'] for s in samples]),
                'buffer_s':summary([s['buffer_s'] for s in samples])}
            wall_intervals = [s['game_tick_wall_ms'] for s in samples
                              if s['game_tick_wall_ms'] is not None and s['game_tick_wall_ms'] > 0]
            if wall_intervals:
                phase['game_tick_wall_ms'] = summary(wall_intervals)
            else:
                phase['warnings'].append('Wall-clock tick cadence unavailable; rebuild the driving plugin.')
            if traffic:
                phase['first_connection_s']=connection_s
                phase['batches_received']=after['batches_received']
                phase['simulation_advance_s']=after['render']['time_s']-before['render']['time_s']
            phases.append(phase)
            print(json.dumps({k:v for k,v in phase.items() if k!='samples'}),flush=True)
    except Exception as exception:error=str(exception)
    finally:
        if owns:
            try:invoke(mobility,'traffic',enabled=False)
            except Exception:pass
            try:invoke(drive,'stop')
            except Exception:pass
        output.write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),
            'phases':phases,'error':error,'measurement':'World delta, game-thread wall-clock tick cadence and local MCP request latency',
            'limits':['Game-thread tick cadence is not GPU duration or a matched before/after FPS result.',
                      'Status polling samples ticks and can miss intermittent stalls.',
                      'Stationary cockpit at the preferred source start; traffic fleet changes over time.',
                      'Editor focus, frame caps and other application workloads can affect cadence.']},indent=2),encoding='utf-8')
    if error:raise SystemExit(error)


if __name__=='__main__':main()
