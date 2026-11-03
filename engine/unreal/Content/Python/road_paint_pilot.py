"""Import the road-surface-clipped, illustrative Charles Malek lane paint pilot."""
import hashlib,json,traceback
from pathlib import Path
import unreal,city_world
source=Path(r'C:/private/dynacity-data/engine/road-visual-pilot-20261027/charles-malek-lane-paint-clipped-v3.glb')
root=city_world._root()/'Saved/DynaCity';report=root/'road-paint-pilot-import.json';prefix='DynaRoadPaint_'
try:
 city_world._current()
 expected={x for x in city_world._names(source) if x.startswith('marking_')}
 if len(expected)!=42:raise RuntimeError('Unexpected source mesh count')
 if any(str(d.label).startswith(prefix) for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs()):raise RuntimeError('Pilot already exists; inspect before retrying')
 before={a.get_path_name() for a in city_world._actors()}
 manager=unreal.InterchangeManager.get_interchange_manager_scripted()
 if manager.is_interchange_active():raise RuntimeError('An unrelated import is active')
 params=unreal.ImportAssetParameters();params.is_automated=True;params.replace_existing=False
 if not manager.import_scene('/Game/Beirut/RoadPaintPilot',unreal.InterchangeManager.create_source_data(str(source)),params):raise RuntimeError('Scene import failed')
 new=[a for a in city_world._actors() if a.get_path_name() not in before];meshes=[a for a in new if a.get_component_by_class(unreal.StaticMeshComponent)];found={a.get_actor_label() for a in meshes}
 if found!=expected:raise RuntimeError('Unexpected imported objects: '+str({'missing':sorted(expected-found),'extra':sorted(found-expected)}))
 rows=[]
 for a in meshes:
  label=a.get_actor_label();c=a.get_component_by_class(unreal.StaticMeshComponent)
  a.detach_from_actor(unreal.DetachmentRule.KEEP_WORLD,unreal.DetachmentRule.KEEP_WORLD,unreal.DetachmentRule.KEEP_WORLD)
  a.set_actor_label(prefix+label);a.set_folder_path('DynaCity/Road paint pilot');a.tags=['DYNACITY_ROAD_PAINT_PILOT','road_id:'+label.split('_',1)[1]];a.set_editor_property('is_spatially_loaded',True)
  c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION);c.set_visibility(True);c.set_hidden_in_game(False)
  rows.append({'label':a.get_actor_label(),'road_id':label.split('_',1)[1],'mesh':c.static_mesh.get_path_name(),'location':[a.get_actor_location().x,a.get_actor_location().y,a.get_actor_location().z]})
 sub=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
 for a in new:
  if a not in meshes and a.get_class().get_name()=='InterchangeSceneActor':sub.destroy_actor(a)
 if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in meshes}),True):raise RuntimeError('Actor save failed')
 if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(False,True):raise RuntimeError('Asset save failed')
 report.write_text(json.dumps({'success':True,'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'actors':rows,'limits':'Illustrative lane paint; not surveyed. Needs rendered appearance and height verification.'},indent=2))
except Exception:
 report.write_text(json.dumps({'success':False,'error':traceback.format_exc()},indent=2));raise
