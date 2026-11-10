"""Root-only transient Nanite/source-mask diagnostic. Never saves assets or map."""
import json
from pathlib import Path
import unreal
import corridor_reconstruction_install as ci
import tower_infill_native_pilot as pilot
R=Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration19-mask')
LABEL='Corridor visual - buildings_remeil_material_9'

def _mesh():
    return ci._component(next(a for a in ci.city_world._actors() if a.get_actor_label()==LABEL)).static_mesh

def stage(action):
    ci.cb._ready();R.mkdir(parents=True,exist_ok=True)
    editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    if action=='begin':
        pre=pilot.preflight();mesh=_mesh()
        data={'snapshot':pilot._snapshot(),'mesh':mesh.get_path_name(),'nanite':bool(editor.get_nanite_settings(mesh).get_editor_property('enabled')),'preflight':pre}
        (R/'backup.json').write_text(json.dumps(data,indent=2));return {'mesh':data['mesh'],'nanite':data['nanite'],'static_actors':len(data['snapshot'])}
    data=json.loads((R/'backup.json').read_text());mesh=unreal.load_asset(data['mesh'])
    if action in ('disable','restore'):
        expected=False if action=='disable' else data['nanite']
        settings=editor.get_nanite_settings(mesh);settings.set_editor_property('enabled',expected)
        editor.set_nanite_settings(mesh,settings,True)
        actual=bool(editor.get_nanite_settings(mesh).get_editor_property('enabled'));assert actual==expected
        result={'mesh':data['mesh'],'nanite_enabled':actual,'asset_saved':False,'scene_saved':False}
    elif action=='verify':
        after=pilot._snapshot();actual=bool(editor.get_nanite_settings(mesh).get_editor_property('enabled'))
        result={'original_static_actors_exact':after==data['snapshot'],'static_actor_count':len(after),'nanite_original_restored':actual==data['nanite'],'asset_saved':False,'scene_saved':False}
        assert result['original_static_actors_exact'] and result['nanite_original_restored'],result
    else:raise ValueError(action)
    (R/(action+'.json')).write_text(json.dumps(result,indent=2));return result
