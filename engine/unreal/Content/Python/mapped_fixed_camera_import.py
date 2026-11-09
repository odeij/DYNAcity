"""Import illustrative finished cameras at OSM nodes without altering other actors."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import unreal

TAG = 'DYNACITY_MAPPED_FIXED_CAMERA'

def apply(filename):
    import city_world, explore_city, library_assets
    from library_asset_core import read_prepared
    world = city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before importing fixed cameras')
    path = Path(filename).resolve(); data = json.loads(path.read_text(encoding='utf8'))
    if data.get('schema') != 'dynacity-mapped-fixed-cameras/1': raise ValueError('Invalid camera manifest')
    if data['origin_utm_m'] != json.loads(city_world._checkpoint().read_text(encoding='utf8'))['origin_utm_m']:
        raise ValueError('City origin mismatch')
    if hashlib.sha256(Path(data['context_path']).read_bytes()).hexdigest() != data['context_sha256']:
        raise ValueError('Source context changed')
    asset = read_prepared(Path(data['prepared_path']))
    if asset['fbx_sha256'] != data['asset_fbx_sha256'] or asset['provenance'] != data['asset_provenance'] or asset['provenance'].get('id') != 'fixed_camera_pole_3dassets':
        raise ValueError('Prepared asset differs from manifest')
    if any(TAG in a.tags for a in city_world._actors()): raise RuntimeError('Fixed camera layer already exists')
    rows=data['placements']; seen=set()
    for row in rows:
        ident=row.get('source_id',''); point=row.get('position_local_m'); heading=row.get('heading_degrees')
        if (not ident.startswith('node/') or ident in seen or not isinstance(point,list) or len(point)!=3 or
                any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in [*point,heading])):
            raise ValueError('Invalid source-backed camera position')
        seen.add(ident)
    if not rows: raise ValueError('No mapped camera positions')
    city_world.load_editor_geometry()
    imported=library_assets.import_prepared(Path(data['prepared_path']), asset['provenance']['id'])
    mesh=unreal.load_asset(imported['mesh']); bounds=mesh.get_bounding_box(); center=(bounds.min+bounds.max)*.5
    _,east,north,_=explore_city._basis(); grouped=defaultdict(list)
    for row in rows: grouped[row.get('production_area') or 'Unassigned'].append(row)
    cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances')
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem); created=[]
    try:
        for area,items in sorted(grouped.items()):
            actor=subsystem.spawn_actor_from_class(cls,unreal.Vector());created.append(actor)
            actor.set_actor_label('Street_MappedFixedCamera_'+re.sub('[^A-Za-z0-9]+','_',area))
            actor.tags=[TAG];actor.set_folder_path('DynaCity/Mapped fixed cameras/'+area)
            actor.set_editor_property('is_spatially_loaded',True)
            actor.set_editor_property('layer_kind','fixed_camera');actor.set_editor_property('production_area',area)
            actor.set_editor_property('source_manifest',str(path));actor.set_street_mesh(mesh)
            actor.set_street_collision(False);actor.set_street_cull_distance(25000,70000)
            for row in items:
                heading=math.radians(row['heading_degrees'])
                rotation=unreal.MathLibrary.make_rot_from_y(east*math.cos(heading)+north*math.sin(heading))
                # The authored pole base is at local (0,0,0); bounds centre is offset by the camera arm.
                transform=unreal.Transform(location=explore_city._world(row['position_local_m']),rotation=rotation,scale=unreal.Vector(1,1,1))
                if actor.add_street_instance(transform,row['source_id'])<0: raise RuntimeError('Placement failed')
            if actor.get_street_instance_count()!=len(items): raise RuntimeError('Count mismatch')
        packages=list({actor.get_outermost() for actor in created})
        if not unreal.EditorLoadingAndSavingUtils.save_packages(packages,True): raise RuntimeError('Save failed')
    except Exception:
        for actor in created: subsystem.destroy_actor(actor)
        raise
    report={'schema':'dynacity-mapped-fixed-camera-import/1','manifest':str(path),'asset':imported,
            'actors':[a.get_path_name() for a in created],'source_ids':sorted(seen),'count':len(rows),
            'notice':'Generic fixed camera and pole; imported facing and source terrain unverified.'}
    (city_world._root()/'Saved/DynaCity/mapped-fixed-camera-import.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return report
