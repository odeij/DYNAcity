"""MCP-invoked, reversible LGB-only native preview. No scene save or collision change."""
import json,hashlib
from pathlib import Path
import unreal
import corridor_reconstruction_install as ci
ROOT=Path('C:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration6')
SOURCE='beirut001:d31eb718-f947-49d5-b82d-14838045a9c7'
LABEL='LGB pilot - estimated architecture and height, NOT accepted'
VISUAL='Corridor visual - buildings_remeil_material_9'
OLD=('Reference balcony cornice - LGB historical approximation','Reference balcony glass - LGB historical approximation')

def actors():return {a.get_actor_label():a for a in ci.city_world._actors()}

def void_material():
    path='/Game/DynaCity/CorridorReconstruction/LGBPilot/M_InvisibleSourceLGB'
    if unreal.EditorAssetLibrary.does_asset_exist(path):return unreal.load_asset(path)
    mat=unreal.AssetToolsHelpers.get_asset_tools().create_asset('M_InvisibleSourceLGB',str(Path(path).parent).replace('\\','/'),unreal.Material,unreal.MaterialFactoryNew())
    mat.set_editor_property('blend_mode',unreal.BlendMode.BLEND_MASKED)
    lib=unreal.MaterialEditingLibrary;n=lib.create_material_expression(mat,unreal.MaterialExpressionConstant,-300,0);n.set_editor_property('r',0.)
    lib.connect_material_property(n,'',unreal.MaterialProperty.MP_OPACITY_MASK);lib.recompile_material(mat);unreal.EditorAssetLibrary.save_loaded_asset(mat);return mat

def prepare(spec_path):
    ci.cb._ready();s=ci._read(spec_path);assert s['source_id']==SOURCE
    assert ci._sha(s['geometry'])==s['sha256']
    path=ci._import(s['geometry'],'/Game/DynaCity/CorridorReconstruction/LGBPilot/'+s['sha256'][:12],'LGB_pilot')
    if path is None:return {'importing':True}
    mesh=unreal.load_asset(path);editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem);ns=editor.get_nanite_settings(mesh)
    if ns.get_editor_property('enabled'):ns.set_editor_property('enabled',False);editor.set_nanite_settings(mesh,ns,True)
    mats={name:ci._material('LGBpilot_'+name,sp).get_path_name() for name,sp in s['materials'].items()}
    result={'mesh':path,'materials':mats,'spec':spec_path,'void':void_material().get_path_name()};(ROOT/'prepared.json').write_text(json.dumps(result,indent=2));return result

def apply():
    ci.cb._ready();ROOT.mkdir(parents=True,exist_ok=True);a=actors();data=ci._read(ROOT/'prepared.json')
    if LABEL in a:raise RuntimeError('preview already active; rollback first')
    if VISUAL not in a or not all(n in a for n in OLD):raise RuntimeError('expected LGB actors not loaded')
    visual=ci._component(a[VISUAL]);slots=[str(x.material_slot_name) for x in visual.static_mesh.static_materials]
    if slots!=['SourceExisting','ReferenceFacade']:raise RuntimeError('unexpected visual source slots')
    # This protected slot contains only the confirmed44-triangle LGB source; offline input audit required by root.
    backup={'snapshot':ci.snapshot(),'states':{n:ci._state(a[n]) for n in (VISUAL,*OLD)},'preview_scene_saved':False}
    (ROOT/'preview-backup.json').write_text(json.dumps(backup,indent=2))
    try:
        visual.set_material(1,unreal.load_asset(data['void']))
        for n in OLD:ci._component(a[n]).set_visibility(False,True)
        sub=unreal.get_editor_subsystem(unreal.EditorActorSubsystem);new=sub.spawn_actor_from_class(unreal.StaticMeshActor,unreal.Vector());new.set_actor_label(LABEL)
        new.tags=['DYNACITY_LGB_NATIVE_PREVIEW', 'ESTIMATED_GEOMETRY_NOT_SURVEYED','source:'+SOURCE]
        new.set_actor_transform(a[VISUAL].get_actor_transform(),False,True);comp=ci._component(new);comp.set_static_mesh(unreal.load_asset(data['mesh']))
        for i,slot in enumerate(comp.static_mesh.static_materials):comp.set_material(i,unreal.load_asset(data['materials'][str(slot.material_slot_name)]))
        ci.cb._disable_visual_collision(new,comp)
    except Exception:
        rollback();raise
    return report()

def report():
    a=actors();backup=ci._read(ROOT/'preview-backup.json');after=ci.snapshot();unexpected=[];changed=[]
    for key,before in backup['snapshot']['actors'].items():
        now=after['actors'].get(key)
        if before!=now:
            changed.append(before['label'])
            if before['label']!=VISUAL:unexpected.append(before['label'])
            elif any(before[k]!=now[k] for k in before if k!='materials') or before['materials'][0]!=now['materials'][0]:unexpected.append(VISUAL+' outside material-slot scope')
    result={'preview':ci._state(a[LABEL]) if LABEL in a else None,'old_details':{n:ci._state(a[n]) for n in OLD},'changed_existing_batches':changed,'unexpected':unexpected,'scene_saved':False,'source_collision_unchanged':not unexpected,'native_acceptance':False}
    (ROOT/'preview-report.json').write_text(json.dumps(result,indent=2));return result

def rollback():
    ci.cb._ready();a=actors();backup=ci._read(ROOT/'preview-backup.json');sub=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if LABEL in a:sub.destroy_actor(a[LABEL])
    for name,row in backup['states'].items():
        c=ci._component(a[name]);c.set_visibility(row['visible'],True)
        for i,path in enumerate(row['materials']):c.set_material(i,unreal.load_asset(path))
    after=ci.snapshot();same=backup['snapshot']==after
    details=all(ci._state(a[n])==row for n,row in backup['states'].items())
    result={'original_snapshot_restored':same,'detail_states_restored':details,'scene_saved':False};(ROOT/'rollback-report.json').write_text(json.dumps(result,indent=2));return result
