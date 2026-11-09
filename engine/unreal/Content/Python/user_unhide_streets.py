"""Restore user-requested visibility for mapped street groups and finished bus stops."""
import json,re,traceback
import unreal,city_world
root=city_world._root()/'Saved/DynaCity'; output=root/'user-unhide-street-20261027.json'; backup=root/'user-unhide-street-before-20261027.json'
try:
 city_world._current(); idx=json.loads((root/'street-instances.json').read_text()); excluded={'bus_stop','tree_broadleaf','tree_conifer','bicycle_parking'}
 wanted={g['actor']:g for g in idx['groups'] if g['kind'] not in excluded}; labels={'Street_'+re.sub('[^A-Za-z0-9]+','_',g['area'])+'_'+g['kind'] for g in wanted.values()}
 descriptors=[d for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs() if str(d.label) in labels or str(d.label).startswith(('Street_BusStopPole_','Street_MappedUtility_'))]
 unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descriptors]); actors=[a for a in city_world._actors() if a.get_path_name() in wanted or a.get_actor_label().startswith(('Street_BusStopPole_','Street_MappedUtility_'))]
 records=[];selected=[]
 for a in actors:
  c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
  if not c:continue
  hidden=bool(a.get_editor_property('hidden')) or bool(c.get_editor_property('hidden_in_game')) or not c.is_visible()
  if not hidden:continue
  records.append({'path':a.get_path_name(),'label':a.get_actor_label(),'tags':[str(t) for t in a.tags],'actor_hidden':bool(a.get_editor_property('hidden')),'component_hidden':bool(c.get_editor_property('hidden_in_game')),'component_visible':c.is_visible(),'count':c.get_instance_count(),'source_ids':list(a.get_editor_property('source_ids'))});selected.append((a,c))
 if backup.exists():raise RuntimeError('Backup already exists; inspect prior run')
 backup.write_text(json.dumps(records,indent=2))
 for a,c in selected:
  a.modify();c.modify(); tags=list(a.tags)
  if 'DYNACITY_USER_VISIBLE' not in [str(t) for t in tags]:a.tags=[*tags,'DYNACITY_USER_VISIBLE']
  a.set_actor_hidden_in_game(False);a.set_is_temporarily_hidden_in_editor(False);c.set_visibility(True);c.set_hidden_in_game(False)
 if selected and not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a,c in selected}),True):raise RuntimeError('Failed to save actor packages')
 output.write_text(json.dumps({'success':True,'groups':records,'total_instances':sum(r['count'] for r in records),'notice':'Visible placeholders are illustrative; geometry and positions may be inaccurate. Original bus stop placeholders remain hidden to avoid overlapping finished stop poles.'},indent=2))
except Exception:
 output.write_text(json.dumps({'success':False,'error':traceback.format_exc()},indent=2));raise
