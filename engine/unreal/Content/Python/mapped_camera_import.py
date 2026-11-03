"""Place illustrative camera-on-pole models at explicitly tagged OSM nodes."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import unreal

TAG='DYNACITY_MAPPED_CAMERA'

def apply(filename):
    import city_world, explore_city, library_assets
    from library_asset_core import read_prepared
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before importing cameras')
    path=Path(filename).resolve();data=json.loads(path.read_text(encoding='utf8'))
    if data.get('schema')!='dynacity-mapped-cameras/1':raise ValueError('Invalid camera manifest')
    if data['origin_utm_m']!=json.loads(city_world._checkpoint().read_text(encoding='utf8'))['origin_utm_m']:
        raise ValueError('City origin mismatch')
    if hashlib.sha256(Path(data['context_path']).read_bytes()).hexdigest()!=data['context_sha256']:
        raise ValueError('Source context changed')
    asset=read_prepared(Path(data['prepared_path']))
    if (asset['fbx_sha256']!=data['asset_fbx_sha256'] or asset['provenance']!=data['asset_provenance'] or
            asset['provenance'].get('id')!='camera_pole_innerscene'):
        raise ValueError('Prepared camera asset differs from manifest')
    if any(TAG in a.tags for a in city_world._actors()):raise RuntimeError('Camera layer already exists')
    rows=data['placements'];seen=set()
    for row in rows:
        ident=row.get('source_id','');point=row.get('position_local_m');bearing=row.get('camera_bearing_degrees_clockwise_from_north')
        if (not ident.startswith('node/') or ident in seen or not isinstance(point,list) or len(point)!=3 or
                any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) for x in [*point,bearing]) or not 0<=bearing<360):
            raise ValueError('Invalid mapped camera placement')
        seen.add(ident)
    if not rows:raise ValueError('No camera positions')
    city_world.load_editor_geometry()
    imported=library_assets.import_prepared(Path(data['prepared_path']),'camera_pole_innerscene')
    mesh=unreal.load_asset(imported['mesh']);_,east,north,_=explore_city._basis();groups=defaultdict(list)
    for row in rows:groups[row.get('production_area') or 'Unassigned'].append(row)
    cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances')
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem);created=[]
    try:
        for area,items in sorted(groups.items()):
            actor=subsystem.spawn_actor_from_class(cls,unreal.Vector());created.append(actor)
            actor.set_actor_label('Street_MappedCamera_'+re.sub('[^A-Za-z0-9]+','_',area));actor.tags=[TAG]
            actor.set_folder_path('DynaCity/Mapped cameras/'+area);actor.set_editor_property('is_spatially_loaded',True)
            actor.set_editor_property('layer_kind','surveillance');actor.set_editor_property('production_area',area)
            actor.set_editor_property('source_manifest',str(path));actor.set_street_mesh(mesh)
            actor.set_street_collision(False);actor.set_street_cull_distance(25000,70000)
            for row in items:
                bearing=math.radians(row['camera_bearing_degrees_clockwise_from_north'])
                # Source lens faces Blender -Y; the FBX conversion maps it to Unreal +Y.
                rotation=unreal.MathLibrary.make_rot_from_y(east*math.sin(bearing)+north*math.cos(bearing))
                transform=unreal.Transform(location=explore_city._world(row['position_local_m']),rotation=rotation,scale=unreal.Vector(1,1,1))
                if actor.add_street_instance(transform,row['source_id'])<0:raise RuntimeError('Camera placement failed')
            if actor.get_street_instance_count()!=len(items):raise RuntimeError('Instance count mismatch')
        if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in created}),True):
            raise RuntimeError('Actor package save failed')
    except Exception:
        for actor in created:subsystem.destroy_actor(actor)
        raise
    report={'schema':'dynacity-mapped-camera-import/1','manifest':str(path),'asset':imported,
            'actors':[a.get_path_name() for a in created],'source_ids':sorted(seen),'count':len(rows),
            'notice':'Generic appearance and height; imported model facing requires native visual verification.'}
    (city_world._root()/'Saved/DynaCity/mapped-camera-import.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return report
