"""Root-only additive preview of estimated stone balcony fronts."""
import importlib
import json
from pathlib import Path
import unreal
import corridor_reconstruction_install as ci
import tower_infill_native_pilot as shared

ROOT=Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration27-granite-parapet')
SPEC=Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration27-granite-parapet/corrected/fraction-0.7/report.json')


def stage(action):
    importlib.reload(shared)
    shared.ROOT=ROOT
    shared.LABEL='Granite parapet pilot - estimated, NOT accepted'
    shared.SOURCE='beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1'
    ROOT.mkdir(parents=True,exist_ok=True)
    if action=='prepare':
        pre=shared.preflight();assert pre['static_actor_count']==504
        s=ci._read(SPEC);assert s['source_id']==shared.SOURCE and s['panel_counts']=={'wall_4':39,'wall_1':52}
        assert ci._sha(s['overlay_glb'])==s['overlay_glb_sha256']
        path=ci._import(s['overlay_glb'],'/Game/DynaCity/GraniteParapetPilot/'+s['overlay_glb_sha256'][:12],'Granite_parapets')
        if path is None:return {'importing':True}
        mesh=unreal.load_asset(path);editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        settings=editor.get_nanite_settings(mesh)
        if settings.get_editor_property('enabled'):
            settings.set_editor_property('enabled',False);editor.set_nanite_settings(mesh,settings,True)
        materials=ci._component(shared._actors()[shared.REFERENCE]).get_materials()
        found=[m.get_path_name() for m in materials if m and 'M_stone_light__granite_' in m.get_name()]
        assert len(found)==1,found
        slots=[str(s.material_slot_name) for s in mesh.static_materials]
        assert slots==['stone_light__granite_overlay'],slots
        out={'mesh':path,'materials':{slots[0]:found[0]},'spec':str(SPEC),'sha256':s['overlay_glb_sha256']}
        (ROOT/'prepared.json').write_text(json.dumps(out,indent=2));return out
    if action in ('apply','rollback','preflight'):
        return getattr(shared,action)()
    raise ValueError(action)
