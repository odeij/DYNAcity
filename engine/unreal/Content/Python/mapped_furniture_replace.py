import json,re,traceback
from pathlib import Path
import unreal,city_world,library_assets
root=city_world._root()/ 'Saved/DynaCity'
out=root/'mapped-conifer-replacement-20261027.json'
try:
 city_world._current(); library_assets.NANITE_KEYS=library_assets.NANITE_KEYS+('conifer_detailed',)
 index=json.loads((root/'street-instances.json').read_text())
 configs={'tree_conifer':('conifer_detailed',None)}
 configs={k:v for k,v in configs.items() if (Path('C:/private/dynacity-data/engine/asset-quality-pass/prepared')/v[0]/'prepared.json').exists()}
 labels={'Street_'+re.sub('[^A-Za-z0-9]+','_',g['area'])+'_'+g['kind'] for g in index['groups'] if g['kind'] in configs}
 unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs() if str(d.label) in labels])
 actors={a.get_path_name():a for a in city_world._actors()}
 wanted={g['actor'] for g in index['groups'] if g['kind'] in configs}; selected=[r for r in index['instances'] if r['actor'] in wanted]
 for r in selected:
  a=actors.get(r['actor'])
  if not a or list(a.get_editor_property('source_ids'))[int(r['instance'])]!=r['instance_id']: raise RuntimeError('Source identity mismatch '+r['source_id'])
 active_path=root/'library-assets-active.json'; active=json.loads(active_path.read_text()); backup=root/'mapped-conifer-before-20261027.json'
 if backup.exists(): raise RuntimeError('Backup exists; inspect before retry')
 old=[]
 for g in index['groups']:
  if g['kind'] not in configs: continue
  a=actors[g['actor']];c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
  old.append({'actor':g['actor'],'mesh':c.static_mesh.get_path_name(),'tags':[str(t) for t in a.tags],'hidden':a.get_editor_property('hidden'),'transforms':[str(c.get_instance_transform(i,world_space=True)) for i in range(c.get_instance_count())]})
 backup.write_text(json.dumps({'actors':old,'active':active},indent=2))
 report={'groups':[],'assets':{},'source_ids':[r['source_id'] for r in selected]}; touched=[]
 for kind,(key,scale) in configs.items():
  rec=library_assets.import_prepared(Path('C:/private/dynacity-data/engine/asset-quality-pass/prepared')/key,key); new=unreal.load_asset(rec['mesh']); report['assets'][kind]=rec; active['street'][kind]=rec
  for g in index['groups']:
   if g['kind']!=kind: continue
   a=actors[g['actor']]; c=a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent); count=c.get_instance_count()
   if count!=g['instances']: raise RuntimeError('Instance count mismatch')
   transforms=[c.get_instance_transform(i,world_space=True) for i in range(count)]
   oldbox=c.static_mesh.get_bounding_box(); newbox=new.get_bounding_box(); factor=(oldbox.max.z-oldbox.min.z)/(newbox.max.z-newbox.min.z) if scale is None else scale
   a.modify();c.modify();a.set_street_mesh(new)
   for i,t in enumerate(transforms):
    t.scale3d=unreal.Vector(t.scale3d.x*factor,t.scale3d.y*factor,t.scale3d.z*factor) if scale is None else unreal.Vector(scale,scale,scale)
    c.update_instance_transform(i,t,world_space=True,mark_render_state_dirty=i==count-1,teleport=True)
   a.tags=[t for t in a.tags if str(t)!='DYNACITY_RETIRED_PLACEHOLDER'];a.set_actor_hidden_in_game(False);a.set_is_temporarily_hidden_in_editor(False);c.set_visibility(True);c.set_hidden_in_game(False)
   report['groups'].append({'actor':g['actor'],'kind':kind,'count':count});touched.append(a)
 if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in touched}),True): raise RuntimeError('Actor save failed')
 active_path.write_text(json.dumps(active,indent=2));report['success']=True;out.write_text(json.dumps(report,indent=2))
except Exception:
 out.write_text(json.dumps({'success':False,'error':traceback.format_exc()},indent=2)); raise



