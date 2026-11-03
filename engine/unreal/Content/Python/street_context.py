"""Import and instance source-identifiable Beirut street context.

Mapped positions and inferred fixtures remain separately described in the
source manifest. This layer does not provide observed/live signal phases.
"""
from collections import Counter,defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import unreal

ROOT='/Game/Beirut/Streets'
TAG='DYNACITY_STREET_CONTEXT'


def _root():
    import city_world
    return city_world._root()


def import_props(filename):
    data=json.loads(Path(filename).read_text())
    if data['schema']!='dynacity-street-props/1':raise ValueError('Unexpected prop schema')
    records={}
    for key,asset in data['assets'].items():
        version=asset.get('sha256_fbx','')[:10]
        imported_name=key+'_'+version if version else key
        path=ROOT+'/Props/'+imported_name
        if not unreal.EditorAssetLibrary.does_asset_exist(path):
            task=unreal.AssetImportTask()
            task.filename=asset['fbx'];task.destination_path=ROOT+'/Props';task.destination_name=imported_name
            task.automated=True;task.replace_existing=False;task.save=True
            options=unreal.FbxImportUI()
            options.import_mesh=True;options.import_materials=True;options.import_textures=False
            options.import_as_skeletal=False;options.static_mesh_import_data.combine_meshes=True
            options.static_mesh_import_data.generate_lightmap_u_vs=False
            task.options=options
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        mesh=unreal.load_asset(path)
        if mesh is None:raise RuntimeError('Missing imported prop: '+path)
        if asset.get('materials'):
            import street_foliage
            street_foliage.apply(mesh,asset['materials'])
        box=mesh.get_bounding_box();size=box.max-box.min
        if not (.1<size.z<5 if key=='crossing_marking' else 30<size.z<2500):raise RuntimeError('Unexpected prop centimetre scale: '+key+' '+str(size))
        records[key]={'asset':path,'height_cm':size.z,'bounds_cm':{'min':[box.min.x,box.min.y,box.min.z],
            'max':[box.max.x,box.max.y,box.max.z]},'source':asset}
    target=_root()/'Saved'/'DynaCity'/'street-prop-import.json'
    target.write_text(json.dumps(records,indent=2))
    return records


def _asset_key(point):
    kind=point['kind'];tags=point.get('tags',{})
    if kind=='tree':
        species=' '.join(str(tags.get(k,'')) for k in ('species','genus','taxon','species:en')).lower()
        if any(s in species for s in ('palm','phoenix','washingtonia')):return 'tree_palm'
        if point.get('leaf_type')=='needleleaved' or tags.get('leaf_type')=='needleleaved':return 'tree_conifer'
        return 'tree_broadleaf'
    if kind=='traffic_signals':
        arrow=point.get('arrow')
        return 'traffic_signal_'+arrow if arrow in ('left','right') else 'traffic_signal'
    if kind=='crossing':
        from mapped_crossings import is_painted
        return 'crossing_marking' if is_painted(point) else None
    return {name:name for name in ('street_lamp','bench','bus_stop','bollard','gate','lift_gate',
                                  'block','fountain','shelter','bicycle_parking','waste_basket')}.get(kind)


def place_points(filename):
    import city_world,explore_city
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before updating mapped street instances')
    cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances')
    if cls is None:raise RuntimeError('Load the compiled street-instancing module first')
    path=Path(filename).resolve();data=json.loads(path.read_text(encoding='utf-8'))
    if data['schema']!='dynacity-engine-street-context/1':raise ValueError('Unexpected street source schema')
    city=json.loads(city_world._checkpoint().read_text())
    if data['origin_utm_m']!=city['origin_utm_m']:raise ValueError('Source origin differs from city')
    props=json.loads((_root()/'Saved'/'DynaCity'/'street-prop-import.json').read_text())
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    load_editor_geometry()
    existing_actors=[a for a in city_world._actors() if TAG in a.tags]
    existing={a.get_actor_label():a for a in existing_actors}
    groups=defaultdict(list);skipped=Counter()
    for point in data['points']:
        key=_asset_key(point)
        if key is None:skipped[point['kind']]+=1;continue
        if key not in props:raise RuntimeError('Missing prop '+key)
        groups[(point.get('production_area','Unassigned'),key)].append(point)
    _,east,north,_=explore_city._basis();changed=[];index=[]
    for (area,key),points in sorted(groups.items()):
        label='Street_'+re.sub('[^A-Za-z0-9]+','_',area)+'_'+key
        actor=existing.get(label)
        if actor is None:actor=subsystem.spawn_actor_from_class(cls,unreal.Vector())
        elif actor.get_class()!=cls:raise RuntimeError('Unexpected actor type at '+label)
        actor.modify()
        actor.set_actor_label(label);actor.set_folder_path('DynaCity/Mapped streets/'+area)
        actor.tags=[TAG,'street_kind:'+key]
        actor.set_editor_property('is_spatially_loaded',True)
        actor.set_editor_property('layer_kind',key);actor.set_editor_property('production_area',area)
        actor.set_editor_property('source_manifest',str(path))
        mesh=unreal.load_asset(props[key]['asset']);actor.set_street_mesh(mesh)
        actor.clear_street_instances();actor.set_street_collision(False)
        actor.set_street_cull_distance(18000 if key.startswith('tree') else 25000,
                                       50000 if key.startswith('tree') else 70000)
        for point in points:
            position=point['position_local_m']
            if len(position)!=3 or not all(math.isfinite(v) for v in position):raise ValueError('Invalid source position')
            world=explore_city._world(position)
            heading=math.radians(point.get('heading_degrees') or 0)
            if key.startswith('tree_'):
                # Tree crown orientation is presentation only; keep its mapped location and height.
                seed=int.from_bytes(hashlib.sha256(point.get('instance_id',point['id']).encode()).digest()[:4],'little')
                heading=seed/2**32*math.tau
            direction=east*math.cos(heading)+north*math.sin(heading)
            rotation=unreal.MathLibrary.make_rot_from_x(direction)
            desired=point.get('height_m') if key!='crossing_marking' else None
            scale=desired*100/props[key]['height_cm'] if desired else 1.
            if not .2<=scale<=5:raise ValueError('Unreasonable fixture scale for '+point['id'])
            scaling=unreal.Vector(scale,scale,scale)
            if key=='crossing_marking':
                width=point.get('road_width_m') or point.get('width_m') or 6.
                bounds=props[key]['bounds_cm']
                authored_width=(bounds['max'][1]-bounds['min'][1])/100
                scaling=unreal.Vector(1,max(.4,min(4,width/authored_width)),1)
                world+=unreal.Vector(0,0,3)
            transform=unreal.Transform(location=world,rotation=rotation,scale=scaling)
            instance=actor.add_street_instance(transform,point.get('instance_id',point['id']))
            if instance<0:raise RuntimeError('Could not add mapped instance')
            index.append({'actor':actor.get_path_name(),'instance':instance,'source_id':point['id'],
                          'instance_id':point.get('instance_id',point['id']),'kind':point['kind']})
        if actor.get_street_instance_count()!=len(points):raise RuntimeError('Instance count differs from source')
        changed.append({'actor':actor.get_path_name(),'kind':key,'area':area,'instances':len(points)})
    # Remove only stale generated groups after their replacements are complete.
    wanted={row['actor'] for row in changed}
    for actor in existing_actors:
        if actor.get_path_name() not in wanted:subsystem.destroy_actor(actor)
    collisions=_place_trunk_collisions(data,props,cls,path)
    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():raise RuntimeError('Could not save street instances')
    if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True):raise RuntimeError('Could not save external street actors')
    report={'schema':'dynacity-street-instance-import/1','source_manifest':str(path),'groups':changed,
            'instances':index,'unsupported_point_kinds':dict(skipped),'trunk_and_pole_collision':collisions,
            'limits':['OSM is incomplete. Untagged hardware shapes and dimensions are illustrative.',
                      'Signal phases are not live observations.',
                      'Tree trunks and poles use illustrative narrow cylinder collision. Other small furniture remains decorative.']}
    target=_root()/'Saved'/'DynaCity'/'street-instances.json';target.write_text(json.dumps(report,indent=2))
    (_root()/'Saved'/'DynaCity'/'street-layer.json').write_text(json.dumps({'manifest':str(path)}))
    return {'groups':len(changed),'instances':len(index),'counts':dict(Counter(row['kind'] for row in index)),
            'report':str(target),'unsupported':dict(skipped)}


def _place_trunk_collisions(data,props,cls,path):
    import city_world,explore_city
    tag='DYNACITY_STREET_COLLISION';subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    previous=[a for a in city_world._actors() if tag in a.tags]
    existing={a.get_actor_label():a for a in previous}
    groups=defaultdict(list)
    for point in data['points']:
        key=_asset_key(point)
        if key and (key.startswith('tree_') or key.startswith('traffic_signal') or key=='street_lamp'):
            groups[point.get('production_area','Unassigned')].append((point,key))
    cylinder=unreal.load_asset('/Engine/BasicShapes/Cylinder.Cylinder');records=[]
    for area,points in groups.items():
        label='StreetCollision_'+re.sub('[^A-Za-z0-9]+','_',area)
        actor=existing.get(label) or subsystem.spawn_actor_from_class(cls,unreal.Vector())
        actor.modify()
        actor.set_actor_label(label);actor.tags=[tag];actor.set_folder_path('DynaCity/Street collision/'+area)
        actor.set_editor_property('is_spatially_loaded',True)
        actor.set_editor_property('source_manifest',str(path));actor.set_street_mesh(cylinder)
        actor.clear_street_instances();actor.set_street_collision(True)
        component=actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        component.set_visibility(False);component.set_hidden_in_game(True);component.set_cast_shadow(False)
        for point,key in points:
            prop=props[key];authored=prop['source'];scale=point.get('height_m')*100/prop['height_cm'] if point.get('height_m') else 1
            height=authored.get('trunk_height_m',3.1 if key.startswith('traffic_signal') else 6)*scale
            radius=authored.get('trunk_radius_m',.075)*scale
            position=explore_city._world(point['position_local_m'])+unreal.Vector(0,0,height*50)
            # Engine Cylinder is 100cm tall and has radius 50cm.
            transform=unreal.Transform(location=position,rotation=unreal.Rotator(),scale=unreal.Vector(radius*2,radius*2,height))
            actor.add_street_instance(transform,point.get('instance_id',point['id']))
        records.append({'actor':actor.get_path_name(),'instances':len(points)})
    wanted={r['actor'] for r in records}
    for actor in previous:
        if actor.get_path_name() not in wanted:subsystem.destroy_actor(actor)
    return records


def status():
    actors=[a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors() if TAG in a.tags]
    return {'loaded_groups':len(actors),'loaded_instances':sum(a.get_street_instance_count() for a in actors)}


def prepare_covers(filename):
    import city_world
    city_world._current()
    data=json.loads(Path(filename).read_text())
    if data['schema']!='dynacity-street-cover-assets/1':raise ValueError('Unexpected cover asset schema')
    if data['origin_utm_m']!=json.loads(city_world._checkpoint().read_text())['origin_utm_m']:
        raise ValueError('Cover origin differs from city')
    path=_root()/'Saved'/'DynaCity'/'street-covers.json'
    if path.exists():
        previous=json.loads(path.read_text())
        if previous['source_sha256']!=data['source_sha256']:
            if previous.get('pending') or len(previous['completed'])!=len(previous['packages']):
                raise RuntimeError('Finish the earlier cover import before refreshing it')
            load_editor_geometry()
            data['replaced_actors']=[r['actor'] for r in previous['completed'].values()]
            backup=path.with_name('street-covers-'+previous['source_sha256'][:12]+'.json')
            if not backup.exists():backup.write_text(json.dumps(previous,indent=2))
            data.update(completed={},pending=None)
            path.write_text(json.dumps(data,indent=2));return data
        return previous
    data.update(completed={},pending=None)
    path.write_text(json.dumps(data,indent=2));return data


def advance_covers():
    import city_world
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        return {'busy':True,'reason':'End Play before importing mapped surfaces'}
    manager=unreal.InterchangeManager.get_interchange_manager_scripted()
    if manager.is_interchange_active():return {'busy':True}
    path=_root()/'Saved'/'DynaCity'/'street-covers.json';data=json.loads(path.read_text())
    pending=data['pending'];subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if pending:
        actors=city_world._actors();meshes=[a for a in actors if a.get_actor_label()==pending['name']]
        if len(meshes)!=1:raise RuntimeError('Expected one mapped cover mesh: '+pending['name'])
        actor=meshes[0]
        actor.modify()
        actor.detach_from_actor(unreal.DetachmentRule.KEEP_WORLD,unreal.DetachmentRule.KEEP_WORLD,unreal.DetachmentRule.KEEP_WORLD)
        actor.tags=list(actor.tags)+['DYNACITY_STREET_COVER','cover_kind:'+pending['kind']]
        actor.set_folder_path('DynaCity/Mapped ground/'+pending['production_area'])
        actor.set_editor_property('is_spatially_loaded',True)
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        component.set_cast_shadow(pending['kind'] in ('wall','fence','jersey_barrier'))
        for candidate in actors:
            if candidate.get_path_name() not in pending['before'] and candidate.get_class().get_name()=='InterchangeSceneActor':
                subsystem.destroy_actor(candidate)
        data['completed'][pending['key']]={'actor':actor.get_path_name(),'source_objects':len(pending['objects'])}
        data['pending']=None
        if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():raise RuntimeError('Cover map save failed')
        path.write_text(json.dumps(data,indent=2))
    package=next((p for p in data['packages'] if p['key'] not in data['completed']),None)
    if package is not None:
        data['pending']={**package,'before':[a.get_path_name() for a in city_world._actors()]}
        path.write_text(json.dumps(data,indent=2))
        params=unreal.ImportAssetParameters();params.is_automated=True;params.replace_existing=False
        source=unreal.InterchangeManager.create_source_data(package['asset'])
        manager.scripted_import_scene_async(ROOT+'/Cover/'+data['source_sha256'][:10]+'/'+package['key'],source,params)
    elif data.get('replaced_actors'):
        wanted=set(data['replaced_actors'])
        for actor in city_world._actors():
            if actor.get_path_name() in wanted:subsystem.destroy_actor(actor)
        data['replaced_actors']=[]
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True)
        path.write_text(json.dumps(data,indent=2))
    return {'completed':len(data['completed']),'total':len(data['packages']),
            'pending':data['pending']['key'] if data['pending'] else None}


def extend_covers(filename):
    path=_root()/'Saved'/'DynaCity'/'street-covers.json'
    data=json.loads(path.read_text());extra=json.loads(Path(filename).read_text())
    if data.get('pending'):raise RuntimeError('Finish the pending import first')
    if extra['source_sha256']!=data['source_sha256'] or extra['origin_utm_m']!=data['origin_utm_m']:
        raise ValueError('Additional street assets use a different source')
    existing={p['key']:p for p in data['packages']}
    for package in extra['packages']:
        if package['key'] in existing and existing[package['key']]!=package:
            raise ValueError('Cannot silently replace existing street package '+package['key'])
        if package['key'] not in existing:data['packages'].append(package)
    path.write_text(json.dumps(data,indent=2));return {'total':len(data['packages'])}


def focus_reference(filename):
    import explore_city,city_world
    city_world._current()
    data=json.loads(Path(filename).read_text())
    reference=data.get('reference_street') or {}
    position=reference.get('position_local_m') or reference.get('centre_local_m')
    if position is None:raise RuntimeError('Reference street position unavailable')
    city_world.load_editor_geometry()
    load_editor_geometry()
    centre=explore_city._world(position)
    _,east,north,_=explore_city._basis()
    heading=math.radians(reference.get('heading_degrees',0))
    forward=(east*math.cos(heading)+north*math.sin(heading))/100
    camera=centre-forward*800+unreal.Vector(0,0,180)
    explore_city._look_at(camera,centre+forward*2000+unreal.Vector(0,0,180))
    return reference


def load_editor_geometry():
    descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    wanted=[d.guid for d in descs if str(d.label).startswith(('Street_','StreetCover_','StreetCollision_','StreetLinear_'))]
    unreal.WorldPartitionBlueprintLibrary.load_actors(wanted)
    return len(wanted)


def open_reference():
    import city_world
    path=_root()/'Saved'/'DynaCity'/'street-layer.json'
    if not path.exists():raise RuntimeError('Mapped streets have not been prepared')
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if world.get_path_name().split('.')[0]!=city_world.MAP:city_world.open_city()
    return focus_reference(json.loads(path.read_text())['manifest'])


def prepare_reference_drive():
    import setup_driving,city_world,explore_city
    path=_root()/'Saved'/'DynaCity'/'street-layer.json'
    source=json.loads(Path(json.loads(path.read_text())['manifest']).read_text())
    ref=source['reference_street'];position=list(ref['position_local_m']);position[2]+=1.2
    candidate={'schema':'dynacity-drive-starts/1','origin_utm_m':source['origin_utm_m'],
               'starts':[{'area':'Charles Malek Avenue','road_name':ref['road_name'],
                          'road_id':ref['road_id'],'local_position_m':position,
                          'yaw_degrees':-ref['heading_degrees'],'spawn_height_m':1.2}]}
    target=_root()/'Saved'/'DynaCity'/'street-drive-candidate.json';target.write_text(json.dumps(candidate))
    check=setup_driving.check_candidates(str(target))
    if not check['all_passed']:raise RuntimeError('Reference road failed wheel clearance: '+str(check))
    cls,_=setup_driving._classes();subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    existing=[a for a in city_world._actors() if 'DYNACITY_STREET_DRIVE_START' in a.tags]
    if len(existing)>1:raise RuntimeError('Duplicate reference drive start')
    actor=existing[0] if existing else subsystem.spawn_actor_from_class(cls,explore_city._world(position))
    actor.modify();actor.tags=[setup_driving.TAG,'DYNACITY_STREET_DRIVE_START']
    actor.set_actor_label('Drive start - Charles Malek Avenue');actor.set_editor_property('is_spatially_loaded',False)
    actor.set_actor_location(explore_city._world(position),False,False)
    _,east,north,_=explore_city._basis();angle=math.radians(ref['heading_degrees'])
    actor.set_actor_rotation(unreal.MathLibrary.make_rot_from_x(east*math.cos(angle)+north*math.sin(angle)),False)
    for name,value in [('area','Charles Malek Avenue'),('road_name',ref['road_name']),('road_id',int(ref['road_id'])),
                       ('vehicle_mesh',__import__('library_assets').vehicle_mesh()),('preferred',True)]:
        actor.set_editor_property(name,value)
    for other in city_world._actors():
        if other!=actor and setup_driving.TAG in other.tags:
            other.modify();other.set_editor_property('preferred',False)
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True)
    return check


def open_tree_reference():
    import explore_city
    open_reference()
    config=json.loads((_root()/'Saved'/'DynaCity'/'street-layer.json').read_text())
    source=json.loads(Path(config['manifest']).read_text());reference=source['reference_street']['position_local_m']
    point=min((p for p in source['points'] if p['kind']=='tree'),
              key=lambda p:math.dist(p['position_local_m'][:2],reference[:2]))
    centre=explore_city._world(point['position_local_m']);_,east,north,up=explore_city._basis()
    explore_city._look_at(centre-east*11-north*8+up*2,centre+up*3.8)
    return {'source_id':point['id'],'position_source':point.get('position_source')}


def register_menu():
    owner='DynaCityMappedStreets';menus=unreal.ToolMenus.get();menus.unregister_owner_by_name(owner)
    menu=menus.extend_menu('LevelEditor.MainMenu.Tools.DynaCityExplore')
    entry=unreal.ToolMenuEntry(name='MappedStreetReference',type=unreal.MultiBlockType.MENU_ENTRY)
    entry.set_label('Charles Malek Avenue — street view')
    entry.set_tool_tip('Open the mapped street reference at eye height. Hardware appearance is illustrative.')
    entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'','import street_context; street_context.open_reference()')
    menu.add_menu_entry('Mapped streets',entry);menus.refresh_all_widgets()
    entry=unreal.ToolMenuEntry(name='MappedTreeReference',type=unreal.MultiBlockType.MENU_ENTRY)
    entry.set_label('Mapped trees — close-up')
    entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'','import street_context; street_context.open_tree_reference()')
    menu.add_menu_entry('Mapped streets',entry);menus.refresh_all_widgets()
    entry=unreal.ToolMenuEntry(name='MappedConiferReference',type=unreal.MultiBlockType.MENU_ENTRY)
    entry.set_label('Mapped conifers — close-up')
    entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'','import greenery_verification; greenery_verification.focus_conifer()')
    menu.add_menu_entry('Mapped streets',entry);menus.refresh_all_widgets()
