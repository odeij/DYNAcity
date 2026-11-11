"""Clear five illustrative reference props from modeled building footprints."""
import json,math,traceback,unreal,city_world,explore_city,location_core,location_search
root=city_world._root()/'Saved/DynaCity';out=root/'reference-furniture-clearance-20261028.json';backup=root/'reference-furniture-clearance-before-20261028.json'
try:
 city_world._current();plan=json.loads((root/'reference-furniture-clearance-candidates-20261028.json').read_text()); candidates=plan['candidates'];labels={'Reference preview - library benches','Reference preview - library bins'};guids=[d.guid for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs() if str(d.label) in labels];unreal.WorldPartitionBlueprintLibrary.load_actors(guids);aa={a.get_path_name():a for a in city_world._actors()};roads=[r for r in location_search._data()['roads'] if r['id'] in {10086,8415,8416,10167,10168}]
 if len(candidates)!=5 or any(c['delta_east_m'] is None for c in candidates):raise RuntimeError('Expected five usable clearance candidates')
 changes=[]
 for r in candidates:
  a=aa.get(r['actor']);
  if not a:raise RuntimeError('Missing actor '+r['actor'])
  c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent);index=r['index'];ids=list(a.get_editor_property('source_ids'))
  if ids[index]!=r['source_id']:raise RuntimeError('Source identity mismatch')
  t=c.get_instance_transform(index,world_space=True);old=explore_city._local(t.translation);expected=r['original_local_m']
  if math.dist(old,expected)>.03:raise RuntimeError('Position changed since audit')
  dx=r['delta_east_m'];oldroad=location_core.nearest_roads({'roads':roads},old[:2],max_distance=12,limit=1);newroad=location_core.nearest_roads({'roads':roads},[old[0]+dx,old[1]],max_distance=12,limit=1)
  if not oldroad or not newroad:raise RuntimeError('Road reference unavailable')
  dz=newroad[0]['position'][2]-oldroad[0]['position'][2];delta=explore_city._world([dx,0,dz])-explore_city._world([0,0,0]);changes.append({'actor':r['actor'],'index':index,'source_id':r['source_id'],'old_world':[t.translation.x,t.translation.y,t.translation.z],'delta_world':[delta.x,delta.y,delta.z],'delta_local_m':[dx,0,dz]})
 if backup.exists():raise RuntimeError('Backup exists; inspect prior run')
 backup.write_text(json.dumps(changes,indent=2));touched=[]
 for r in changes:
  a=aa[r['actor']];c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent);a.modify();c.modify();t=c.get_instance_transform(r['index'],world_space=True);t.translation=t.translation+unreal.Vector(*r['delta_world'])
  if not c.update_instance_transform(r['index'],t,world_space=True,mark_render_state_dirty=True,teleport=True):raise RuntimeError('Update failed')
  touched.append(a)
 if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in touched}),True):raise RuntimeError('Save failed')
 unreal.WorldPartitionBlueprintLibrary.unload_actors(guids);unreal.WorldPartitionBlueprintLibrary.load_actors(guids);aa={a.get_path_name():a for a in city_world._actors()}
 for r in changes:
  a=aa[r['actor']];c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent);t=c.get_instance_transform(r['index'],world_space=True);expected=unreal.Vector(*r['old_world'])+unreal.Vector(*r['delta_world'])
  if (t.translation-expected).length()>1 or list(a.get_editor_property('source_ids'))[r['index']]!=r['source_id']:raise RuntimeError('Reload verification failed')
 out.write_text(json.dumps({'success':True,'changes':changes,'reloaded_external_actors':True,'limits':'Illustrative props; approximate footprint clearance, not surveyed furniture positions.'},indent=2))
except Exception:out.write_text(json.dumps({'success':False,'error':traceback.format_exc()},indent=2));raise
