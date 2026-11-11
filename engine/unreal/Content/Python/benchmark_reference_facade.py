"""A/B/A editor viewport frame-interval diagnostic, not a game FPS benchmark."""
import json
import time
from pathlib import Path
import unreal
import city_world

ROOT=Path('C:/private/dynacity-data/engine/facade-reference-20261030')
report=json.loads((ROOT/'native-install.json').read_text())
owned=[unreal.load_object(None,path) for path in report['actors']]
source=unreal.load_object(None,report['source_actor'])
if source is None or any(a is None for a in owned):raise RuntimeError('Missing benchmark actors')
unreal.SystemLibrary.execute_console_command(city_world._current(),'Editor.AsyncAssetCompilationFinishAll')
state={'phase':0,'start':time.perf_counter(),'samples':[[],[],[]],'handle':None}


def show(reference):
    source.get_component_by_class(unreal.StaticMeshComponent).set_visibility(not reference,True)
    for actor in owned:actor.get_component_by_class(unreal.StaticMeshComponent).set_visibility(reference,True)


def finish(error=None):
    show(True)
    if state['handle'] is not None:unreal.unregister_slate_post_tick_callback(state['handle'])
    results=[]
    for name,values in zip(['baseline_first','reference','baseline_repeat'],state['samples']):
        ordered=sorted(values);n=len(values)
        results.append({'phase':name,'samples':n,'median_interval_ms':ordered[n//2] if n else None,
                        'p95_interval_ms':ordered[min(n-1,int(n*.95))] if n else None})
    (ROOT/'editor-frame-diagnostic.json').write_text(json.dumps({'status':'failed' if error else 'recorded',
        'error':error,'phases':results,'metric':'Slate/editor tick interval, milliseconds',
        'limitations':['Not GPU frame time or packaged/PIE gameplay FPS','Foreground state, background load and viewport throttling affect results',
                       'Both material sets are resident; memory delta cannot be inferred from visibility toggles']},indent=2))


def tick(delta):
    try:
        elapsed=time.perf_counter()-state['start']
        if elapsed>=.8 and 0<float(delta)<1:state['samples'][state['phase']].append(float(delta)*1000)
        if elapsed>=3:
            state['phase']+=1
            if state['phase']==3:finish();return
            show(state['phase']==1);state['start']=time.perf_counter()
    except Exception as exc:finish(str(exc))


show(False)
state['handle']=unreal.register_slate_post_tick_callback(tick)
