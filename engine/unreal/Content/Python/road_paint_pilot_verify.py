"""Reload and check the saved pilot actors and source-road identities."""
import json,traceback,unreal,city_world
root=city_world._root()/'Saved/DynaCity';out=root/'road-paint-pilot-reload.json'
try:
 city_world._current();report=json.loads((root/'road-paint-pilot-import.json').read_text());expected={r['label']:r for r in report['actors']};ds=[d for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs() if str(d.label) in expected]
 if len(ds)!=len(expected):raise RuntimeError('Saved descriptor count mismatch')
 unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in ds]); actors=[a for a in city_world._actors() if a.get_actor_label() in expected];rows=[]
 for a in actors:
  r=expected[a.get_actor_label()]; c=a.get_component_by_class(unreal.StaticMeshComponent); tags=[str(t) for t in a.tags]; loc=a.get_actor_location();pos=[loc.x,loc.y,loc.z]
  if c is None or c.static_mesh.get_path_name()!=r['mesh'] or 'road_id:'+r['road_id'] not in tags or not c.is_visible() or c.get_editor_property('hidden_in_game') or any(abs(x-y)>0.1 for x,y in zip(pos,r['location'])): raise RuntimeError('Reload mismatch '+a.get_actor_label())
  center,extent=a.get_actor_bounds(False);rows.append({'label':a.get_actor_label(),'road_id':r['road_id'],'center':[center.x,center.y,center.z],'extent':[extent.x,extent.y,extent.z]})
 if len(rows)!=len(expected):raise RuntimeError('Actor count mismatch')
 out.write_text(json.dumps({'success':True,'actor_count':len(rows),'actors':rows},indent=2))
except Exception:
 out.write_text(json.dumps({'success':False,'error':traceback.format_exc()},indent=2));raise
