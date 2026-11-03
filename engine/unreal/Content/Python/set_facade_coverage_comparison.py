"""Temporary A/B visibility only; captures never save the comparison state."""
import json
from pathlib import Path
import unreal

root=Path('C:/private/dynacity-data/engine/facade-coverage-20261030')
mode=json.loads((root/'capture-mode.json').read_text())['mode']
if mode not in ['before','after']:raise ValueError('Unknown comparison mode')
new=json.loads((root/'facade-install.json').read_text())
old=json.loads((root/'superseded-state-before.json').read_text())
for group in new['actors']:
    actor=unreal.load_object(None,group['actor'])
    if actor is None:raise RuntimeError('Missing replacement actor')
    comp=actor.get_component_by_class(unreal.StaticMeshComponent)
    comp.set_visibility(mode=='after',True);comp.set_hidden_in_game(mode!='after',True)
for group in old:
    actor=unreal.load_object(None,group['actor'])
    if actor is None:raise RuntimeError('Missing preserved old actor')
    comp=actor.get_component_by_class(unreal.StaticMeshComponent)
    comp.set_visibility(mode=='before' and group['visible'],True)
    comp.set_hidden_in_game(group['hidden_in_game'] if mode=='before' else True,True)
(root/'capture-state-result.json').write_text(json.dumps({'mode':mode,'status':'applied'}))
