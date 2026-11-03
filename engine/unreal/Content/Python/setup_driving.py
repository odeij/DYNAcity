"""Install source-grounded driving bookmarks after the runtime plugin is compiled.

Does not enable an unbuilt plugin, rewrite the source data, or replace a map.
The editor replay remains separate from the player's vehicle and live SUMO.
"""
import json
import math
from pathlib import Path
import unreal

TAG='DYNACITY_DRIVE_START'


def _classes():
    start=unreal.load_class(None,'/Script/DynaCityDrive.DynaDriveStart')
    mode=unreal.load_class(None,'/Script/DynaCityDrive.DynaDriveGameMode')
    if start is None or mode is None:
        raise RuntimeError('Compile and enable DynaCityDrive with a supported C++ toolchain, then restart Unreal before preparing driving.')
    return start,mode


def prepare(filename,preferred='Minet el Hosn'):
    import city_world,explore_city
    city_world._current()
    data=json.loads(Path(filename).read_text(encoding='utf-8'))
    if data.get('schema')!='dynacity-drive-starts/1':raise ValueError('Unsupported driving starts schema')
    city=json.loads(city_world._checkpoint().read_text())
    if data['origin_utm_m']!=city['origin_utm_m']:raise ValueError('Driving starts use a different city origin')
    starts=data['starts'];areas=[row['area'] for row in starts]
    if not starts or len(areas)!=len(set(areas)):raise ValueError('Expected unique source areas')
    if preferred not in areas:raise ValueError('Preferred area has no screened source start')
    for row in starts:
        if len(row['local_position_m'])!=3 or not all(math.isfinite(v) for v in row['local_position_m']):
            raise ValueError('Invalid source spawn coordinates')
        if not math.isfinite(row['yaw_degrees']) or row.get('spawn_height_m')!=1.2:
            raise ValueError('Unexpected spawn heading or surface clearance')
    start_class,mode_class=_classes()
    import library_assets
    mesh=library_assets.vehicle_mesh()
    bounds=mesh.get_bounding_box();size=bounds.max-bounds.min
    if not (350<size.x<550 and 150<size.y<250 and 100<size.z<210):
        raise RuntimeError('Approved car must be X-forward, Z-up and in centimetres')
    city_world.load_editor_geometry()
    validation=check_candidates(filename)
    if not validation['all_passed']:
        failed=[row['area'] for row in validation['starts'] if not row['passed']]
        raise RuntimeError('Driving starts failed actual mesh clearance checks: '+', '.join(failed))
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    existing=[a for a in city_world._actors() if TAG in [str(t) for t in a.tags]]
    by_area={}
    for actor in existing:
        if actor.get_class()!=start_class:raise RuntimeError('Drive-start tag on unexpected actor class')
        area=actor.get_editor_property('area')
        if area in by_area:raise RuntimeError('Duplicate saved drive start: '+area)
        by_area[area]=actor
    if set(by_area)-set(areas):raise RuntimeError('Existing source starts were removed; inspect before rebuilding bookmarks')
    _,east,north,_=explore_city._basis()
    records=[]
    for row in starts:
        area=row['area'];location=explore_city._world(row['local_position_m'])
        yaw=math.radians(row['yaw_degrees'])
        direction=east*math.cos(yaw)-north*math.sin(yaw)
        rotation=unreal.MathLibrary.make_rot_from_x(direction)
        actor=by_area.get(area)
        if actor is None:actor=subsystem.spawn_actor_from_class(start_class,location,rotation)
        actor.set_actor_location(location,False,False);actor.set_actor_rotation(rotation,False)
        actor.tags=[TAG]
        actor.set_actor_label('Drive start - '+area)
        actor.set_folder_path('DynaCity/Drive starts')
        actor.set_editor_property('is_spatially_loaded',False)
        for name,value in [('area',area),('road_name',row['road_name']),('road_id',row['road_id']),('preferred',area==preferred),('vehicle_mesh',mesh)]:
            actor.set_editor_property(name,value)
        records.append({'area':area,'road_id':row['road_id'],'actor':actor.get_path_name()})
    world=city_world._current()
    world.get_world_settings().set_editor_property('default_game_mode',mode_class)
    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():
        raise RuntimeError('Could not save driving setup')
    result={'schema':'dynacity-driving-setup/1','map':city_world.MAP,'starts':records,'preferred':preferred,
            'runtime_verified':False,'model':'arcade exploration, not calibrated vehicle dynamics or live traffic'}
    path=city_world._root()/'Saved'/'DynaCity'/'driving-setup.json'
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def check_candidates(filename):
    """Probe the loaded source collision geometry without needing the driving plugin."""
    import city_world,explore_city
    world=city_world._current();city_world.load_editor_geometry()
    data=json.loads(Path(filename).read_text(encoding='utf-8'))
    city=json.loads(city_world._checkpoint().read_text())
    if data.get('schema')!='dynacity-drive-starts/1' or data['origin_utm_m']!=city['origin_utm_m']:
        raise ValueError('Driving candidate schema/origin does not match the city')
    _,east,north,_=explore_city._basis()
    records=[]
    for row in data['starts']:
        centre=explore_city._world(row['local_position_m'])
        yaw=math.radians(row['yaw_degrees'])
        direction=east*math.cos(yaw)-north*math.sin(yaw)
        length=math.hypot(direction.x,direction.y)
        forward=unreal.Vector(direction.x/length,direction.y/length,0)
        right=unreal.Vector(-forward.y,forward.x,0)
        points=[];normals=[];labels=[];reasons=[]
        for x in (-135,135):
            for y in (-70,70):
                probe=centre+forward*x+right*y
                hit=unreal.SystemLibrary.line_trace_single(world,probe+unreal.Vector(0,0,300),
                    probe-unreal.Vector(0,0,900),unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,False,[],unreal.DrawDebugTrace.NONE,True)
                if hit is None:reasons.append('missing wheel support');continue
                fields=hit.to_dict();point=fields['impact_point'];normal=fields['impact_normal']
                actor=fields['hit_actor'];label=actor.get_actor_label() if actor else ''
                points.append(point);normals.append(normal);labels.append(label)
                if normal.z<.7:reasons.append('surface too steep')
                if not label.startswith('roads_'):reasons.append('wheel probe did not hit source road')
        surface_z=None;body_blocked=None
        if len(points)==4:
            surface_z=sum(p.z for p in points)/4
            normal=unreal.Vector(sum(n.x for n in normals),sum(n.y for n in normals),sum(n.z for n in normals))
            size=normal.length();normal=normal/size
            rotation=unreal.MathLibrary.make_rot_from_xz(forward,normal)
            position=unreal.Vector(centre.x,centre.y,surface_z+75)
            blocked=unreal.SystemLibrary.box_trace_single(world,position,position,unreal.Vector(218,90,55),rotation,
                unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,False,[],unreal.DrawDebugTrace.NONE,True)
            body_blocked=blocked is not None
            if body_blocked:reasons.append('chassis overlaps source geometry')
            if max(p.z for p in points)-min(p.z for p in points)>150:reasons.append('wheel height spread exceeds limit')
            if abs(surface_z-(centre.z-120))>35:reasons.append('collision surface differs from source candidate elevation')
        records.append({'area':row['area'],'road_id':row['road_id'],'passed':not reasons,
            'wheel_supports':len(points),'hit_actors':sorted(set(labels)),
            'surface_world_z_cm':surface_z,
            'surface_height_error_cm':None if surface_z is None else surface_z-(centre.z-120),
            'chassis_blocked':body_blocked,'reasons':sorted(set(reasons))})
    result={'schema':'dynacity-drive-collision-check/1','starts':records,'all_passed':bool(records) and all(r['passed'] for r in records),
            'limits':['Static spawn checks only; runtime movement, streaming transitions and controls remain unverified.']}
    path=city_world._root()/'Saved'/'DynaCity'/'drive-candidate-collision.json'
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def status():
    worlds=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world=worlds.get_game_world()
    if world is None:return {'playing':False}
    pawn=unreal.GameplayStatics.get_player_pawn(world,0)
    if pawn is None:return {'playing':True,'pawn':None}
    return {'playing':True,'pawn':pawn.get_class().get_name(),
            'driving':json.loads(pawn.get_drive_status()) if hasattr(pawn,'get_drive_status') else None}


def play():
    import city_world,explore_city,preview_traffic
    _classes();city_world._current()
    import library_assets
    library_assets.vehicle_mesh()
    if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
        raise RuntimeError('Wait for the current asset import before starting driving')
    cover=city_world._root()/'Saved'/'DynaCity'/'street-covers.json'
    if cover.exists():
        data=json.loads(cover.read_text())
        if data.get('pending') or len(data.get('completed',{}))<len(data.get('packages',[])):
            raise RuntimeError('Finish the mapped street surface import before starting driving')
    setup=city_world._root()/'Saved'/'DynaCity'/'driving-setup.json'
    if not setup.is_file():raise RuntimeError('Prepare source drive starts before Play')
    city_world.load_editor_geometry()
    explore_city.stop_following();preview_traffic.stop()
    levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if levels.is_in_play_in_editor():return status()
    import ambient_effects
    ambient_effects.prepare_assets()
    if not levels.save_current_level():raise RuntimeError('Could not save city before Play')
    levels.editor_request_begin_play()
    return {'requested':True,'note':'Click the game viewport to capture keyboard input'}
