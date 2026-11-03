"""Install finished speed bumps at OSM nodes with checked road-surface hits."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import unreal

TAG = 'DYNACITY_MAPPED_SPEED_BUMP'


def apply(filename):
    import city_world, explore_city, library_assets
    from library_asset_core import read_prepared
    world = city_world._current()
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if levels.is_in_play_in_editor(): raise RuntimeError('End Play before importing')
    path = Path(filename).resolve(); data = json.loads(path.read_text(encoding='utf8'))
    if data.get('schema') != 'dynacity-mapped-speed-bumps/1': raise ValueError('Invalid manifest')
    if data['origin_utm_m'] != json.loads(city_world._checkpoint().read_text(encoding='utf8'))['origin_utm_m']:
        raise ValueError('City origin mismatch')
    if hashlib.sha256(Path(data['context_path']).read_bytes()).hexdigest() != data['context_sha256']:
        raise ValueError('Source context changed')
    asset = read_prepared(Path(data['prepared_path']))
    if asset['fbx_sha256'] != data['asset_fbx_sha256'] or asset['provenance']['id'] != 'speed_bump_innerscene':
        raise ValueError('Asset mismatch')
    city_world.load_editor_geometry()
    if any(TAG in a.tags for a in city_world._actors()): raise RuntimeError('Already imported')
    imported = library_assets.import_prepared(Path(data['prepared_path']), 'speed_bump_innerscene')
    mesh = unreal.load_asset(imported['mesh']); bounds = mesh.get_bounding_box(); center = (bounds.min+bounds.max)*.5
    _,east,north,_ = explore_city._basis(); valid=[]; skipped=[]; seen=set()
    for row in data['placements']:
        ident=row['source_id']; point=row['position_local_m']; angle=row['heading_degrees']; scale=row['scale_x']
        if ident in seen or not ident.startswith('node/') or len(point)!=3 or not all(math.isfinite(v) for v in [*point,angle,scale]) or not 0 < scale <= 2:
            raise ValueError('Invalid source placement')
        seen.add(ident); where=explore_city._world(point)
        hit=unreal.SystemLibrary.line_trace_single(world,where+unreal.Vector(0,0,200),where-unreal.Vector(0,0,200),
            unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,False,[],unreal.DrawDebugTrace.NONE,True)
        if hit is None:
            skipped.append({'source_id':ident,'reason':'no loaded surface hit'});continue
        info=hit.to_dict(); actor=info.get('hit_actor'); label=actor.get_actor_label() if actor else ''
        if 'road' not in label.lower() or info['impact_normal'].z < .7 or abs(info['impact_point'].z-where.z)>150:
            skipped.append({'source_id':ident,'reason':'no suitable road surface at node','hit_actor':label});continue
        angle=math.radians(angle); rotation=unreal.MathLibrary.make_rot_from_x(east*math.cos(angle)+north*math.sin(angle))
        offset=unreal.MathLibrary.get_forward_vector(rotation)*center.x*scale+unreal.MathLibrary.get_right_vector(rotation)*center.y
        valid.append((row, info['impact_point']-offset+unreal.Vector(0,0,.5),rotation,info['impact_point'].z-where.z,label))
    if not valid: raise RuntimeError('No mapped bump nodes hit a suitable loaded road surface')
    cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances'); subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    groups=defaultdict(list)
    for entry in valid: groups[entry[0]['production_area']].append(entry)
    created=[]
    try:
        for area,items in sorted(groups.items()):
            actor=subsystem.spawn_actor_from_class(cls,unreal.Vector());created.append(actor)
            actor.set_actor_label('Street_MappedSpeedBump_'+re.sub('[^A-Za-z0-9]+','_',area));actor.tags=[TAG]
            actor.set_folder_path('DynaCity/Mapped speed bumps/'+area);actor.set_editor_property('is_spatially_loaded',True)
            actor.set_editor_property('layer_kind','traffic_calming');actor.set_editor_property('production_area',area)
            actor.set_editor_property('source_manifest',str(path));actor.set_street_mesh(mesh);actor.set_street_collision(False)
            actor.set_street_cull_distance(25000,70000)
            for row,position,rotation,_,_ in items:
                if actor.add_street_instance(unreal.Transform(location=position,rotation=rotation,scale=unreal.Vector(row['scale_x'],1,1)),row['source_id'])<0:
                    raise RuntimeError('Could not place source node')
            if actor.get_street_instance_count()!=len(items):raise RuntimeError('Instance count mismatch')
        # Save only packages containing new actors, not the pre-existing dirty packages.
        packages=list({a.get_outermost() for a in created})
        if not unreal.EditorLoadingAndSavingUtils.save_packages(packages,True):raise RuntimeError('Could not save new actor packages')
    except Exception:
        for actor in created:subsystem.destroy_actor(actor)
        raise
    report={'schema':'dynacity-mapped-speed-bump-import/1','manifest':str(path),'asset':imported,
            'actors':[a.get_path_name() for a in created], 'count':len(valid),'skipped':skipped,
            'placements':[{'source_id':r['source_id'],'road_actor':label,'road_height_adjustment_cm':dz} for r,_,_,dz,label in valid]}
    (city_world._root()/'Saved/DynaCity/mapped-speed-bump-import.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return report
