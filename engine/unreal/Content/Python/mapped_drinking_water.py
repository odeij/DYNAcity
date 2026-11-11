"""Install illustrative drinking fountains for mapped, unnamed drinking-water points."""
import json, math, re, traceback
from pathlib import Path
import unreal,city_world,explore_city,library_assets
root=city_world._root()/'Saved/DynaCity'; out=root/'mapped-drinking-water-import-20261027.json'; prefix='Street_MappedDrinkingWater_'
try:
 city_world._current(); context=Path(r'C:/private/dynacity-data/engine/greenery-refresh/street-context-fresh.json'); d=json.loads(context.read_text()); points=[p for p in d['points'] if p['kind']=='drinking_water' and not p['tags'].get('name:en')]
 if len(points)!=2 or len({p['id'] for p in points})!=len(points):raise RuntimeError('Unexpected mapped point set')
 if any(str(x.label).startswith(prefix) for x in unreal.WorldPartitionBlueprintLibrary.get_actor_descs()): raise RuntimeError('Existing drinking-water layer; no duplicates created')
 rec=library_assets.import_prepared(Path(r'C:/private/dynacity-data/engine/asset-quality-pass/prepared/drinking_fountain'),'drinking_fountain'); mesh=unreal.load_asset(rec['mesh'])
 cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances'); sub=unreal.get_editor_subsystem(unreal.EditorActorSubsystem); _,east,north,_=explore_city._basis(); created=[]
 try:
  a=sub.spawn_actor_from_class(cls,unreal.Vector());created.append(a);a.set_actor_label(prefix+'Mazraa');a.tags=['DYNACITY_MAPPED_DRINKING_WATER'];a.set_folder_path('DynaCity/Mapped drinking water');a.set_editor_property('is_spatially_loaded',True);a.set_editor_property('layer_kind','drinking_water');a.set_editor_property('source_manifest',str(context));a.set_street_mesh(mesh);a.set_street_collision(False);a.set_street_cull_distance(25000,70000)
  for p in points:
   h=math.radians(p.get('heading_degrees') or 0);rot=unreal.MathLibrary.make_rot_from_x(east*math.cos(h)+north*math.sin(h));pos=explore_city._world(p['position_local_m'])
   if a.add_street_instance(unreal.Transform(location=pos,rotation=rot,scale=unreal.Vector(1,1,1)),p['id'])<0:raise RuntimeError('Could not place '+p['id'])
  if not unreal.EditorLoadingAndSavingUtils.save_packages([a.get_outermost()],True):raise RuntimeError('Save failed')
 except Exception:
  for a in created:sub.destroy_actor(a)
  raise
 out.write_text(json.dumps({'success':True,'source_ids':[p['id'] for p in points],'asset':rec,'note':'Generic visual; location, orientation and height not surveyed. Named water sales location excluded.'},indent=2))
except Exception:
 out.write_text(json.dumps({'success':False,'error':traceback.format_exc()},indent=2));raise
