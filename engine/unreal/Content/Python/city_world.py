"""Build a separate partitioned city world from private prepared packages.

Import one package at a time, then finalize it after Interchange finishes.
Persistent checkpoints prevent retries from silently duplicating geometry.
"""
import json
from pathlib import Path
import struct
import unreal

MAP='/Game/Maps/BeirutCityBatched'
TAG='DYNACITY_CITY'


def _root():
    # Development tools may import this module from the repository rather than
    # the private project's Content/Python copy. Checkpoints belong to the
    # currently open Unreal project in either case.
    return Path(unreal.Paths.project_dir()).resolve()


def _checkpoint():
    return _root()/'Saved'/'DynaCity'/('city-import-'+MAP.rsplit('/',1)[1]+'.json')


def _write(data):
    path=_checkpoint();path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.partial');temporary.write_text(json.dumps(data,indent=2))
    temporary.replace(path)


def _actors():
    return list(unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors())


def _current():
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world.get_path_name().split('.')[0]!=MAP:
        raise RuntimeError('Open the separate BeirutCity map first')
    return world


def create(build_manifest,surface_manifest):
    """Save the current level, then create an empty World Partition city map."""
    if unreal.EditorAssetLibrary.does_asset_exist(MAP):
        raise RuntimeError('City map already exists; open/resume it instead')
    buildings=json.loads(Path(build_manifest).read_text())
    surfaces=json.loads(Path(surface_manifest).read_text())
    if buildings['origin_utm_m']!=surfaces['origin_utm_m']:
        raise RuntimeError('City packages use different coordinate origins')
    import preview_traffic,explore_city
    explore_city.stop_following();preview_traffic.stop()
    levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not levels.save_current_level():raise RuntimeError('Could not save current map')
    if not levels.new_level(MAP,True):raise RuntimeError('Could not create partitioned map')
    actors=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    sun=actors.spawn_actor_from_class(unreal.DirectionalLight,unreal.Vector(0,0,50000))
    sun.set_actor_rotation(unreal.Rotator(-35,-30,0),False)
    light=sun.get_component_by_class(unreal.DirectionalLightComponent)
    light.set_mobility(unreal.ComponentMobility.MOVABLE);light.set_intensity(10)
    light.set_editor_property('atmosphere_sun_light',True)
    actors.spawn_actor_from_class(unreal.SkyAtmosphere,unreal.Vector())
    sky=actors.spawn_actor_from_class(unreal.SkyLight,unreal.Vector(0,0,10000))
    component=sky.get_component_by_class(unreal.SkyLightComponent)
    component.set_mobility(unreal.ComponentMobility.MOVABLE)
    component.set_editor_property('real_time_capture',True)
    pp=actors.spawn_actor_from_class(unreal.PostProcessVolume,unreal.Vector())
    pp.set_editor_property('unbound',True)
    settings=pp.get_editor_property('settings')
    settings.set_editor_property('override_auto_exposure_method',True)
    settings.set_editor_property('auto_exposure_method',unreal.AutoExposureMethod.AEM_MANUAL)
    settings.set_editor_property('override_auto_exposure_bias',True)
    settings.set_editor_property('auto_exposure_bias',-1)
    settings.set_editor_property('override_auto_exposure_apply_physical_camera_exposure',True)
    settings.set_editor_property('auto_exposure_apply_physical_camera_exposure',False)
    pp.set_editor_property('settings',settings)
    for actor in _actors():actor.set_editor_property('is_spatially_loaded',False)
    if 'jobs' in buildings:
        packages=[{'key':'shared-'+p['name'],**p} for p in surfaces['layers'] if p['name']!='roads']
        packages += [{'key':'batch-'+p['name'],**p} for p in buildings['jobs']]
    else:
        packages=[{'key':'shared-'+p['name'],**p} for p in surfaces['layers']]
        packages += [{'key':'district-'+p['name'],**p} for p in buildings['packages']]
    _write({'schema':'dynacity-city-import/1','map':MAP,'origin_utm_m':buildings['origin_utm_m'],
            'roads_metadata':surfaces['roads_metadata'],
            'packages':packages,'completed':{},'pending':None})
    if not levels.save_current_level():raise RuntimeError('City map save failed')
    return status()


def _names(path):
    with Path(path).open('rb') as stream:
        magic,version,total=struct.unpack('<4sII',stream.read(12))
        size,kind=struct.unpack('<I4s',stream.read(8))
        if magic!=b'glTF' or version!=2 or kind!=b'JSON' or size>64*1024*1024:
            raise ValueError('Unsupported GLB package')
        doc=json.loads(stream.read(size))
    return [n['name'] for n in doc['nodes'] if 'mesh' in n]


def start_next(key=None):
    """Begin exactly one import; finalize before starting another package."""
    _current();data=json.loads(_checkpoint().read_text())
    manager=unreal.InterchangeManager.get_interchange_manager_scripted()
    if data['pending'] or manager.is_interchange_active():raise RuntimeError('Finalize pending import first')
    remaining=[p for p in data['packages'] if p['key'] not in data['completed'] and (key is None or p['key']==key)]
    if not remaining:return {'complete':True}
    package=remaining[0];path=Path(package['asset'])
    if not path.is_file():raise FileNotFoundError(path)
    names=_names(path)
    # A partial failed import must be repaired, never silently duplicated.
    existing={a.get_actor_label() for a in _actors()}
    if existing.intersection(names):raise RuntimeError('Package actor labels already exist; inspect checkpoint')
    data['pending']={'key':package['key'],'names':names,'before':[a.get_path_name() for a in _actors()]}
    _write(data)
    # Only this generated, private package folder is targeted. Explicit reuse
    # avoids Interchange override dialogs on an interrupted import retry.
    params=unreal.ImportAssetParameters();params.is_automated=True;params.replace_existing=True
    source=unreal.InterchangeManager.create_source_data(str(path))
    destination='/Game/'+MAP.rsplit('/',1)[1]+'/'+package['key'].replace(' ','_').replace('-','_')
    manager.scripted_import_scene_async(destination,source,params)
    return {'started':package['key'],'expected_actors':len(names)}


def replace_roads(build_manifest,surface_manifest):
    """Remove the imported road batches and queue a rebuilt set in their place.

    Continue with start_next/finalize (or CityTools.advance) as for a first import.
    """
    _current();data=json.loads(_checkpoint().read_text())
    if data['pending'] or unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
        raise RuntimeError('Finalize pending import first')
    batches=json.loads(Path(build_manifest).read_text());surfaces=json.loads(Path(surface_manifest).read_text())
    if batches['origin_utm_m']!=data['origin_utm_m'] or surfaces['origin_utm_m']!=data['origin_utm_m']:
        raise RuntimeError('Rebuilt roads use a different coordinate origin')
    new=[{'key':'batch-'+j['name'],**j} for j in batches['jobs'] if j['kind']=='roads']
    if not new:raise RuntimeError('No road batches in the build manifest')
    old=[p for p in data['packages'] if p['key'].startswith('batch-roads-')]
    # External actors must be loaded before they can be removed and saved.
    load_editor_geometry()
    labels={name for p in old if Path(p['asset']).is_file() for name in _names(p['asset'])}
    labels|={a.get_actor_label() for a in _actors() if a.get_actor_label().startswith('roads_') and TAG in a.tags}
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    removed=[a.get_actor_label() for a in _actors() if a.get_actor_label() in labels]
    for actor in [a for a in _actors() if a.get_actor_label() in labels]:subsystem.destroy_actor(actor)
    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():
        raise RuntimeError('City save failed after removing old roads')
    if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(False,True):
        raise RuntimeError('Asset save failed after removing old roads')
    for p in old:data['completed'].pop(p['key'],None)
    data['packages']=[p for p in data['packages'] if p not in old]+new
    data['roads_metadata']=surfaces['roads_metadata']
    data.setdefault('road_replacements',[]).append({'removed_actors':len(removed),'packages':[p['key'] for p in new]})
    _write(data)
    return {'removed_actors':len(removed),'queued':[p['key'] for p in new]}


def resume_empty_pending():
    """Recover after restart only when no pending source actors exist in the map."""
    _current();data=json.loads(_checkpoint().read_text());pending=data['pending']
    if not pending:return status()
    if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
        raise RuntimeError('Import is still running')
    expected=set(pending['names']);present={a.get_actor_label() for a in _actors()}
    if expected.intersection(present):raise RuntimeError('Pending actors exist; finalize or inspect partial import')
    data.setdefault('recovered_empty_imports',[]).append(pending['key'])
    data['pending']=None;_write(data)
    return status()


def finalize():
    """Validate source actor counts, detach importer hierarchy and save checkpoint."""
    _current();data=json.loads(_checkpoint().read_text());pending=data['pending']
    if not pending:return status()
    if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
        return {'importing':True,'pending':pending['key']}
    before=set(pending['before']);new=[a for a in _actors() if a.get_path_name() not in before]
    meshes=[a for a in new if a.get_component_by_class(unreal.StaticMeshComponent)]
    expected=set(pending['names']);found={a.get_actor_label() for a in meshes}
    if expected!=found:
        raise RuntimeError('Import mismatch: '+str({'missing':sorted(expected-found)[:12],'extra':sorted(found-expected)[:12]}))
    for actor in meshes:
        actor.detach_from_actor(unreal.DetachmentRule.KEEP_WORLD,unreal.DetachmentRule.KEEP_WORLD,unreal.DetachmentRule.KEEP_WORLD)
        actor.tags=list(actor.tags)+[TAG,'city_package:'+pending['key']]
        basis=actor.get_actor_label().startswith('DYNACITY_BASIS_')
        actor.set_editor_property('is_spatially_loaded',not basis)
        if basis:
            actor.set_actor_hidden_in_game(True);actor.set_is_temporarily_hidden_in_editor(True)
            component=actor.get_component_by_class(unreal.StaticMeshComponent)
            component.set_visibility(False,True)
            component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    # Imported scene roots retain references to all imported actors. Removing
    # them prevents one root from forcing an entire package to stream together.
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    roots=[a for a in new if a not in meshes]
    for actor in roots:
        if actor.get_class().get_name()=='InterchangeSceneActor':subsystem.destroy_actor(actor)
    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():
        raise RuntimeError('City save failed; checkpoint remains pending')
    if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(False,True):
        raise RuntimeError('Asset save failed; checkpoint remains pending')
    data['completed'][pending['key']]={'actors':len(meshes),'asset':next(p['asset'] for p in data['packages'] if p['key']==pending['key'])}
    data['pending']=None;_write(data)
    return status()


def status():
    data=json.loads(_checkpoint().read_text()) if _checkpoint().exists() else {}
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    return {'map':world.get_path_name(),'completed':data.get('completed',{}),
            'pending':data.get('pending',{}),'total_packages':len(data.get('packages',[])),
            'importing':unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active(),
            'city_actors':sum(TAG in a.tags for a in _actors())}


def open_city():
    import explore_city,preview_traffic
    explore_city.stop_following();preview_traffic.stop()
    levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not levels.save_current_level():raise RuntimeError('Could not save current map')
    if not levels.load_level(MAP):raise RuntimeError('City map has not been built yet')
    load_editor_geometry()
    overview()


def load_editor_geometry(area=None):
    """Load saved external actors for editor viewing; runtime remains spatial."""
    _current()
    descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs()
    if descs is None:raise RuntimeError('World Partition descriptors unavailable')
    # A saved visual replacement accompanies the hidden source batch. Resolve it
    # by installation-owned paths, before making any loading changes.
    import load_reference_facade
    reference_guids=load_reference_facade.installed_guids(descs,area)
    wanted=None
    if area is not None:
        data=json.loads(_checkpoint().read_text())
        wanted={name for p in data['packages'] if p.get('production_group')==area for name in _names(p['asset'])}
    matches=[]
    for desc in descs:
        label=str(desc.label)
        street=label.startswith(('StreetCover_','StreetLinear_','Street_','StreetCollision_'))
        source=street or label.startswith(('buildings_','roads_','source-ground','beirut001_transport'))
        if source and (wanted is None or label in wanted or street or label.startswith(('source-ground','beirut001_transport'))):
            matches.append(desc.guid)
    matches.extend(guid for guid in reference_guids if guid not in matches)
    unreal.WorldPartitionBlueprintLibrary.load_actors(matches)
    match_ground_finish()
    return len(matches)


def match_ground_finish():
    """Use the retained terrain finish on its extension, without changing heights."""
    actors=_actors()
    source=next((a for a in actors if a.get_actor_label().startswith('source-ground')),None)
    if source is None:return 0
    component=source.get_component_by_class(unreal.StaticMeshComponent)
    material=component.get_material(0) if component else None
    if material is None:return 0
    changed=0
    for actor in actors:
        if actor.get_actor_label() not in ('StreetCover_TerrainExtension','StreetCover_TerrainGapRepair'):continue
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        if not component:continue
        for slot in range(component.get_num_materials()):
            if component.get_material(slot)!=material:
                actor.modify();component.modify()
                component.set_material(slot,material);changed+=1
    return changed


def overview():
    import explore_city
    explore_city.stop_following()
    # Editor region selections do not survive every reopen. Always load the
    # complete visible layers, including the ground extension, before framing.
    load_editor_geometry()
    actors=[a for a in _actors() if a.get_actor_label().startswith(('buildings_','roads_'))]
    if not actors:
        load_editor_geometry()
        actors=[a for a in _actors() if a.get_actor_label().startswith(('buildings_','roads_'))]
    if not actors:raise RuntimeError('City geometry has not been imported yet')
    bounds=[a.get_actor_bounds(False) for a in actors]
    low=[min(getattr(c,k)-getattr(e,k) for c,e in bounds) for k in ('x','y','z')]
    high=[max(getattr(c,k)+getattr(e,k) for c,e in bounds) for k in ('x','y','z')]
    centre=unreal.Vector(*[(a+b)/2 for a,b in zip(low,high)])
    distance=max(high[0]-low[0],high[1]-low[1])*.6
    explore_city._look_at(centre+unreal.Vector(distance*.5,distance*.85,distance*.85),centre)


def focus_area(name):
    import explore_city
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world.get_path_name().split('.')[0]!=MAP:open_city()
    _current();explore_city.stop_following()
    load_editor_geometry(name)
    data=json.loads(_checkpoint().read_text())
    package=next(p for p in data['packages'] if p.get('production_group')==name and p.get('kind')=='buildings')
    actors=[a for a in _actors() if 'city_package:'+package['key'] in a.tags]
    if not actors:raise RuntimeError('Area is not loaded: '+name)
    bounds=[a.get_actor_bounds(False) for a in actors]
    low=[min(getattr(c,k)-getattr(e,k) for c,e in bounds) for k in ('x','y','z')]
    high=[max(getattr(c,k)+getattr(e,k) for c,e in bounds) for k in ('x','y','z')]
    centre=unreal.Vector(*[(a+b)/2 for a,b in zip(low,high)])
    distance=max(high[0]-low[0],high[1]-low[1],10000)*.85
    explore_city._look_at(centre+unreal.Vector(distance,distance,distance*.8),centre)


def street_view():
    """Use the nearest source road to the selected batch or current camera."""
    import explore_city
    _current();explore_city.stop_following()
    selected=unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors()
    target=selected[0].get_actor_bounds(False)[0] if selected else unreal.EditorLevelLibrary.get_level_viewport_camera_info()[0]
    local=explore_city._local(target)
    data=json.loads(_checkpoint().read_text())
    metadata=data.get('roads_metadata')
    if not metadata:raise RuntimeError('Configure roads_metadata in the city checkpoint')
    roads=json.loads(Path(metadata).read_text())['roads']
    segments=[(a,b) for road in roads for a,b in zip(road['path'],road['path'][1:])]
    a,b=min(segments,key=lambda pair:(pair[0][0]-local[0])**2+(pair[0][1]-local[1])**2)
    camera=explore_city._world([a[0],a[1],a[2]+1.7])
    target=explore_city._world([b[0],b[1],b[2]+1.7])
    explore_city._look_at(camera,target)


def open_pilot():
    import explore_city,preview_traffic
    explore_city.stop_following();preview_traffic.stop()
    levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not levels.save_current_level():raise RuntimeError('Could not save current map')
    if not levels.load_level('/Game/Maps/BeirutPrototype'):raise RuntimeError('Pilot map unavailable')
    explore_city.overview()
