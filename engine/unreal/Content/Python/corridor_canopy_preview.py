"""Two estimated, ground-traced library trees. Reversible visual preview only."""
import json
from pathlib import Path
import unreal
import corridor_reconstruction_install as ci
import tower_infill_native_pilot as pilot
R=Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration22-canopy')
PREFIX='Corridor canopy estimate - '
POINTS=[(2430.,1502.),(2448.,1503.)]
MESH_OVERRIDE=None
def snapshot():return {a.get_path_name():ci._state(a) for a in ci.city_world._actors() if ci._component(a) and not a.get_actor_label().startswith(PREFIX)}
def stage(action):
 ci.cb._ready();R.mkdir(parents=True,exist_ok=True)
 if action=='save_candidate':
  pilot.preflight();before=snapshot()
  library=json.loads((ci.city_world._root()/'Saved/DynaCity/library-assets-active.json').read_text())['street']['tree_broadleaf']
  original=unreal.load_asset(library['mesh']);editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
  original_settings=editor.get_nanite_settings(original)
  target='/Game/DynaCity/CanopyPilot/tree_small_02_preserve_area_v1'
  mesh=unreal.load_asset(target) if unreal.EditorAssetLibrary.does_asset_exist(target) else unreal.EditorAssetLibrary.duplicate_asset(library['mesh'],target)
  assert isinstance(mesh,unreal.StaticMesh) and '/CanopyPilot/' in mesh.get_path_name()
  settings=editor.get_nanite_settings(mesh);settings.set_editor_property('enabled',True);settings.set_editor_property('shape_preservation',unreal.NaniteShapePreservation.PRESERVE_AREA)
  editor.set_nanite_settings(mesh,settings,True)
  saved=unreal.EditorAssetLibrary.save_loaded_asset(mesh,only_if_is_dirty=False)
  result=unreal.EditorLoadingAndSavingUtils.reload_packages([mesh.get_outer()],unreal.ReloadPackagesInteractionMode.ASSUME_POSITIVE)
  mesh=unreal.load_asset(target);settings=editor.get_nanite_settings(mesh)
  assert saved and result[0] and settings.get_editor_property('enabled') and settings.get_editor_property('shape_preservation')==unreal.NaniteShapePreservation.PRESERVE_AREA
  current=editor.get_nanite_settings(original);after=snapshot()
  assert all(current.get_editor_property(k)==original_settings.get_editor_property(k) for k in ('enabled','shape_preservation')),'Original settings changed'
  assert after==before,'Actor snapshot changed: '+str([k for k in set(before)|set(after) if before.get(k)!=after.get(k)])
  out={'candidate':mesh.get_path_name(),'saved_only_candidate':bool(saved),'reload_succeeded':bool(result[0]),'shape_preservation':str(settings.get_editor_property('shape_preservation')),'nanite_enabled':True,'material_slots':[str(s.material_slot_name) for s in mesh.static_materials],'original_settings_unchanged':True,'original_static_actors_exact':True,'actor_count':len(before),'scene_saved':False,'provenance':library['provenance']}
  (R/'candidate-persistence.json').write_text(json.dumps(out,indent=2));return out
 if action in ('preserve_area','restore_shape'):
  library=json.loads((ci.city_world._root()/'Saved/DynaCity/library-assets-active.json').read_text())['street']['tree_broadleaf']
  mesh=unreal.load_asset(library['mesh']);editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
  settings=editor.get_nanite_settings(mesh);path=R/'shape-backup.json'
  if action=='preserve_area':
   current=settings.get_editor_property('shape_preservation')
   shape_name=next(name for name in ('NONE','PRESERVE_AREA','VOXELIZE') if getattr(unreal.NaniteShapePreservation,name)==current)
   path.write_text(json.dumps({'shape':shape_name,'enabled':settings.get_editor_property('enabled')}))
   shape=unreal.NaniteShapePreservation.PRESERVE_AREA
  else:
   if not path.exists():return {'unchanged':True,'reason':'No backup created; candidate setting was never applied','asset_saved':False}
   backup=json.loads(path.read_text());shape=getattr(unreal.NaniteShapePreservation,backup['shape']);settings.set_editor_property('enabled',backup['enabled'])
  settings.set_editor_property('shape_preservation',shape);editor.set_nanite_settings(mesh,settings,True)
  actual=editor.get_nanite_settings(mesh);assert actual.get_editor_property('shape_preservation')==shape
  return {'shape_preservation':str(shape),'nanite_enabled':actual.get_editor_property('enabled'),'asset_saved':False}
 if action in ('foliage_nanite_off','foliage_nanite_restore'):
  library=json.loads((ci.city_world._root()/'Saved/DynaCity/library-assets-active.json').read_text())['street']['tree_broadleaf']
  mesh=unreal.load_asset(library['mesh']);editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
  settings=editor.get_nanite_settings(mesh);path=R/'foliage-nanite-backup.json'
  if action=='foliage_nanite_off':
   path.write_text(json.dumps({'enabled':settings.get_editor_property('enabled')}));enabled=False
  else:enabled=json.loads(path.read_text())['enabled']
  settings.set_editor_property('enabled',enabled);editor.set_nanite_settings(mesh,settings,True)
  actual=editor.get_nanite_settings(mesh).get_editor_property('enabled');assert actual==enabled
  return {'nanite_enabled':actual,'asset_saved':False}
 if action=='audit':
  pilot.preflight();before=snapshot()
  library=json.loads((ci.city_world._root()/'Saved/DynaCity/library-assets-active.json').read_text())['street']['tree_broadleaf']
  mesh=unreal.load_asset(library['mesh']);editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
  rows=[]
  for i,slot in enumerate(mesh.static_materials):
   mat=unreal.load_asset(library['materials'][str(slot.material_slot_name)])
   row={'index':i,'slot':str(slot.material_slot_name),'material':mat.get_path_name(),'two_sided':mat.get_editor_property('two_sided'),'blend_mode':str(mat.get_editor_property('blend_mode')),'clip':mat.get_editor_property('opacity_mask_clip_value')}
   for key,prop in [('base_color',unreal.MaterialProperty.MP_BASE_COLOR),('opacity_mask',unreal.MaterialProperty.MP_OPACITY_MASK)]:
    try:
     node=unreal.MaterialEditingLibrary.get_material_property_input_node(mat,prop)
     row[key]=node.get_class().get_name() if node else None
     if node and 'TextureSample' in node.get_class().get_name():row[key+'_texture']=node.get_editor_property('texture').get_path_name()
    except Exception as error:row[key+'_read_error']=str(error)
   rows.append(row)
  settings=editor.get_nanite_settings(mesh)
  out={'mesh':mesh.get_path_name(),'nanite_enabled':settings.get_editor_property('enabled'),'shape_preservation':str(settings.get_editor_property('shape_preservation')),'slots':rows,'source_prepared':library['prepared'],'original_static_actors_exact':snapshot()==before,'scene_saved':False}
  (R/'foliage-audit.json').write_text(json.dumps(out,indent=2));return out
 if action=='apply':
  pilot.preflight();assert not any(a.get_actor_label().startswith(PREFIX) for a in ci.city_world._actors())
  before=snapshot();(R/'backup.json').write_text(json.dumps(before,indent=2))
  library=json.loads((ci.city_world._root()/'Saved/DynaCity/library-assets-active.json').read_text())['street']['tree_broadleaf']
  mesh=unreal.load_asset(MESH_OVERRIDE or library['mesh']);assert isinstance(mesh,unreal.StaticMesh)
  box=mesh.get_bounding_box();assert max(box.max.x-box.min.x,box.max.y-box.min.y)<600 and box.max.z-box.min.z<800
  ground=ci.ground_heights([[x*100,-y*100] for x,y in POINTS]);assert all(g['hit'] for g in ground)
  sub=unreal.get_editor_subsystem(unreal.EditorActorSubsystem);spawned=[]
  try:
   for i,((x,y),g) in enumerate(zip(POINTS,ground)):
    a=sub.spawn_actor_from_class(unreal.StaticMeshActor,unreal.Vector(x*100,-y*100,g['z_cm']-box.min.z));a.set_actor_label(PREFIX+str(i+1));spawned.append(a)
    a.tags=['DYNACITY_CANOPY_PREVIEW','ESTIMATED_NOT_SURVEYED','NO_SCENE_SAVE'];comp=ci._component(a);comp.set_static_mesh(mesh)
    for slot in mesh.static_materials:
     name=str(slot.material_slot_name)
     if name not in library['materials']:raise RuntimeError('Missing library material '+name)
    for n,slot in enumerate(mesh.static_materials):comp.set_material(n,unreal.load_asset(library['materials'][str(slot.material_slot_name)]))
    ci.cb._disable_visual_collision(a,comp)
   assert snapshot()==before
   out={'trees':[ci._state(a) for a in spawned],'ground_hits':ground,'mesh':mesh.get_path_name(),'provenance':library['provenance'],
        'positions_status':'Estimated visual hypothesis; ground traced; not mapped/surveyed trees. Source-building AABB and lane-distance broadphase clearance only.',
        'original_static_actors_exact':True,'scene_saved':False,'existing_assets_modified':False}
   (R/'apply.json').write_text(json.dumps(out,indent=2));return out
  except Exception:
   for a in spawned:sub.destroy_actor(a)
   raise
 if action=='rollback':
  sub=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
  for a in ci.city_world._actors():
   if a.get_actor_label().startswith(PREFIX):sub.destroy_actor(a)
  before=json.loads((R/'backup.json').read_text());after=snapshot();out={'original_static_actors_exact':before==after,'actor_count':len(after),'preview_absent':not any(a.get_actor_label().startswith(PREFIX) for a in ci.city_world._actors()),'scene_saved':False}
  (R/'rollback.json').write_text(json.dumps(out,indent=2));assert out['original_static_actors_exact'] and out['preview_absent'];return out
 raise ValueError(action)
