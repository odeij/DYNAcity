"""Hide only legacy bollard duplicates after validating visible finished replacements."""
import json,re,traceback,unreal,city_world
root=city_world._root()/'Saved/DynaCity';out=root/'bollard-duplicate-cleanup-20261028.json';backup=root/'bollard-duplicate-cleanup-before-20261028.json'
try:
 city_world._current();idx=json.loads((root/'street-instances.json').read_text());groups=[g for g in idx['groups'] if g['kind']=='bollard'];rows=[r for r in idx['instances'] if r['kind']=='bollard']; labels={'Street_'+re.sub('[^A-Za-z0-9]+','_',g['area'])+'_bollard' for g in groups};ds=[d for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs() if str(d.label) in labels or str(d.label).startswith('Street_FinishedBollard_')];guids=[d.guid for d in ds];unreal.WorldPartitionBlueprintLibrary.load_actors(guids);actors=city_world._actors();old=[a for a in actors if a.get_actor_label() in labels];new=[a for a in actors if a.get_actor_label().startswith('Street_FinishedBollard_')]
 expected={r['instance_id'] for r in rows};oldids=[];newids=[]
 for a in old:oldids.extend(list(a.get_editor_property('source_ids')))
 for a in new:
  c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent);ids=list(a.get_editor_property('source_ids'))
  if not c or c.get_instance_count()!=len(ids) or not c.is_visible() or c.get_editor_property('hidden_in_game') or a.get_editor_property('hidden'):raise RuntimeError('Replacement is not visible: '+a.get_actor_label())
  newids.extend(ids)
 if len(oldids)!=len(expected) or len(newids)!=len(expected) or set(oldids)!=expected or set(newids)!=expected:raise RuntimeError('Source IDs do not match exactly')
 if backup.exists():raise RuntimeError('Backup exists; inspect before retry')
 saved=[]
 for a in old:
  c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent);saved.append({'label':a.get_actor_label(),'path':a.get_path_name(),'tags':[str(t) for t in a.tags],'visible':c.is_visible(),'hidden_in_game':c.get_editor_property('hidden_in_game'),'actor_hidden':a.get_editor_property('hidden')})
 backup.write_text(json.dumps(saved,indent=2))
 for a in old:
  c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent);a.modify();c.modify();a.tags=[t for t in a.tags if str(t)!='DYNACITY_USER_VISIBLE'];c.set_visibility(False);c.set_hidden_in_game(True)
 if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in old}),True):raise RuntimeError('Save failed')
 unreal.WorldPartitionBlueprintLibrary.unload_actors(guids);unreal.WorldPartitionBlueprintLibrary.load_actors(guids);reloaded=city_world._actors();old2=[a for a in reloaded if a.get_actor_label() in labels];new2=[a for a in reloaded if a.get_actor_label().startswith('Street_FinishedBollard_')]
 for a in old2:
  c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
  if c.is_visible() or not c.get_editor_property('hidden_in_game'):raise RuntimeError('Legacy group still visible after reload')
 ids=[]
 for a in new2:
  c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
  if not c.is_visible() or c.get_editor_property('hidden_in_game') or a.get_editor_property('hidden'):raise RuntimeError('Replacement hidden after reload')
  ids.extend(list(a.get_editor_property('source_ids')))
 if len(old2)!=len(old) or len(ids)!=len(expected) or set(ids)!=expected:raise RuntimeError('Reload counts differ')
 out.write_text(json.dumps({'success':True,'mapped_bollards_visible':len(ids),'duplicate_placeholders_hidden':len(oldids),'source_ids':sorted(expected),'reloaded_external_actors':True},indent=2))
except Exception:out.write_text(json.dumps({'success':False,'error':traceback.format_exc()},indent=2));raise
