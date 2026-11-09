"""Move two illustrative street lamps clear of modeled building footprints."""
import json,math,traceback,unreal,city_world,explore_city,location_search,location_core
root=city_world._root()/'Saved/DynaCity';out=root/'reference-lamp-clearance-20261028.json';backup=root/'reference-lamp-clearance-before-20261028.json';ref=root/'reference-preview.json'
try:
 city_world._current(); report=json.loads(ref.read_text());before_report=report.copy(); descriptors=[d for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs() if str(d.label).startswith(('Reference preview - CC0 lamps','Reference lamp illumination'))];guids=[d.guid for d in descriptors];unreal.WorldPartitionBlueprintLibrary.load_actors(guids);aa=city_world._actors();groups=[a for a in aa if a.get_actor_label()=='Reference preview - CC0 lamps'];lights=[a for a in aa if a.get_actor_label().startswith('Reference lamp illumination')]
 if len(groups)!=1 or len(lights)!=9:raise RuntimeError('Expected one lamp group and nine lights')
 actor=groups[0];c=actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent);ids=list(actor.get_editor_property('source_ids'))
 if c.get_instance_count()!=9 or len(report['placements'])!=9:raise RuntimeError('Lamp count differs')
 roads=[r for r in location_search._data()['roads'] if r['id'] in {10086,8415,8416,10167,10168}];changes=[]
 for i,offset,shift in [(6,48,-8.0),(8,96,-6.0)]:
  if ids[i]!='illustrative/lamp/'+str(offset):raise RuntimeError('Lamp identity mismatch '+str(ids[i]))
  old=report['placements'][i]['position_local_m'];t=c.get_instance_transform(i,world_space=True);old_world=explore_city._world(old)
  if math.hypot(t.translation.x-old_world.x,t.translation.y-old_world.y)>100:raise RuntimeError('Lamp position mismatch')
  near=location_core.nearest_roads({'roads':roads},[2423.6417+offset+shift,1491.691],max_distance=8,limit=1)
  if not near:raise RuntimeError('No road near candidate')
  row=near[0];p=row['position'];dx,dy=row['direction'];length=math.hypot(dx,dy);new=[p[0]+dy/length*6.15,p[1]-dx/length*6.15,p[2]];delta=explore_city._world(new)-old_world
  near_lights=sorted(lights,key=lambda a:math.hypot(a.get_actor_location().x-old_world.x,a.get_actor_location().y-old_world.y));light=near_lights[0];position=light.get_actor_location()
  if math.hypot(position.x-old_world.x,position.y-old_world.y)>100:raise RuntimeError('Matching light missing')
  if any(x['light']==light.get_path_name() for x in changes):raise RuntimeError('Duplicate matched light')
  changes.append({'index':i,'source_id':ids[i],'old_local_m':old,'new_local_m':new,'old_mesh_world':[t.translation.x,t.translation.y,t.translation.z],'light':light.get_path_name(),'old_light_world':[position.x,position.y,position.z],'delta_world':[delta.x,delta.y,delta.z]})
 if backup.exists():raise RuntimeError('Backup exists; inspect earlier attempt')
 backup.write_text(json.dumps({'original_report':report,'changes':changes},indent=2))
 actor.modify();c.modify();touched=[actor]
 for change in changes:
  i=change['index'];t=c.get_instance_transform(i,world_space=True);delta=unreal.Vector(*change['delta_world']);t.translation=t.translation+delta
  if not c.update_instance_transform(i,t,world_space=True,mark_render_state_dirty=True,teleport=True):raise RuntimeError('Instance update failed')
  light=next(a for a in lights if a.get_path_name()==change['light']);light.modify();light.set_actor_location(light.get_actor_location()+delta,False,False);touched.append(light);report['placements'][i]['position_local_m']=change['new_local_m'];report['placements'][i]['source']='Illustrative road placement adjusted to clear modeled building footprint'
 if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in touched}),True):raise RuntimeError('Saving failed')
 ref.write_text(json.dumps(report,indent=2));unreal.WorldPartitionBlueprintLibrary.unload_actors(guids);unreal.WorldPartitionBlueprintLibrary.load_actors(guids);aa=city_world._actors();group=next(a for a in aa if a.get_actor_label()=='Reference preview - CC0 lamps');component=group.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent);paths={a.get_path_name():a for a in aa}
 for change in changes:
  t=component.get_instance_transform(change['index'],world_space=True);expected=unreal.Vector(*change['old_mesh_world'])+unreal.Vector(*change['delta_world']);light=paths[change['light']];expected_light=unreal.Vector(*change['old_light_world'])+unreal.Vector(*change['delta_world'])
  if (t.translation-expected).length()>1 or (light.get_actor_location()-expected_light).length()>1:raise RuntimeError('Reload location mismatch')
 out.write_text(json.dumps({'success':True,'changes':changes,'reloaded_external_actors':True,'limits':'Illustrative placement; clearance checked in projected source building meshes, not surveyed field measurements.'},indent=2))
except Exception:out.write_text(json.dumps({'success':False,'error':traceback.format_exc()},indent=2));raise
