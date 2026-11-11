"""Editor exploration controls for the private Beirut prototype.

These controls move the viewport and inspect source records. They do not create
driving physics, change traffic, or turn the editor replay into a live simulation.
"""
import json
from pathlib import Path
import re
import time
import unreal

# importlib.reload must not leave an earlier camera callback running.
if globals().get('_follow_callback') is not None:
    unreal.unregister_slate_post_tick_callback(_follow_callback)
_follow_callback=None
_follow_id=None
_last_update=0.0
_source_cache=None
_source_stamp=None
_owner='DynaCityExplore'
_basis_cache=None
_basis_world=None


def _basis():
    global _basis_cache,_basis_world
    import dynacity_tools
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if _basis_cache is None or world!=_basis_world:
        _basis_cache=dynacity_tools._basis();_basis_world=world
    return _basis_cache


def _world(point):
    origin,east,north,up=_basis()
    return origin+east*point[0]+north*point[1]+up*point[2]


def _local(position):
    origin,*axes=_basis()
    delta=position-origin
    return [float(delta.dot(axis)/axis.dot(axis)) for axis in axes]


def _sources():
    global _source_cache,_source_stamp
    config_path=Path(__file__).resolve().parents[2]/'prototype_config.json'
    config=json.loads(config_path.read_text(encoding='utf-8'))
    path=Path(config['scene_metadata']) if config.get('scene_metadata') else Path(config['scene_glb']).parent.parent/'scene.json'
    if not path.is_file():
        raise RuntimeError('Source scene metadata is unavailable. Configure scene_metadata in prototype_config.json.')
    stamp=(str(path.resolve()),path.stat().st_mtime_ns,path.stat().st_size)
    if stamp!=_source_stamp:
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('schema')!='dynacity-engine-scene/1':raise RuntimeError('Unsupported source scene metadata schema')
        _source_cache={
            'buildings':{m['id']:m.get('metadata',{}) for m in data['meshes'] if m['kind']=='building'},
            'roads':{str(r['id']):r for r in data['roads']},
            'coordinates':data['coordinates'],
        }
        _source_stamp=stamp
    return _source_cache


def _selected(required=True):
    actors=list(unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors())
    if len(actors)>1:raise RuntimeError('Select one road, building or traffic vehicle first.')
    if not actors:
        if required:raise RuntimeError('Select a road, building or traffic vehicle in the viewport or Outliner first.')
        return None
    return actors[0]


def _look_at(camera,target):
    if (target-camera).length()<1:raise RuntimeError('Camera target is too close')
    unreal.EditorLevelLibrary.set_level_viewport_camera_info(camera,unreal.MathLibrary.make_rot_from_x(target-camera))
    # The stored camera can move while a non-realtime viewport keeps its old
    # frame on screen. Navigation must visibly update the active scene.
    editor=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    editor.editor_set_viewport_realtime(True)
    editor.editor_invalidate_viewports()
    actual,_=unreal.EditorLevelLibrary.get_level_viewport_camera_info()
    if (actual-camera).length()>100:
        raise RuntimeError('Choose Perspective in the viewport camera menu, then use this camera control again.')


def stop_following():
    global _follow_callback,_follow_id
    if _follow_callback is not None:
        unreal.unregister_slate_post_tick_callback(_follow_callback)
    _follow_callback=None;_follow_id=None


def overview():
    import city_world
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_path_name().split('.')[0]==city_world.MAP:
        return city_world.overview()
    stop_following()
    origin,east,north,up=_basis()
    _look_at(origin+east*450-north*550+up*450,origin+up*15)


def focus_selection():
    actor=_selected();stop_following()
    centre,extent=actor.get_actor_bounds(False)
    distance=max(extent.x,extent.y,extent.z,400)*2.8
    _look_at(centre+unreal.Vector(distance,distance,distance*.8),centre)


def _road_id(label):
    match=re.match(r'^road_(\d+)(?:$|_)',label)
    return match.group(1) if match else None


def _vehicle_id(actor):
    import preview_traffic
    return next((identifier for identifier,item in preview_traffic._cars.items() if item==actor),None)


def street_view():
    """Place the camera 1.7 m above a source road segment, without inventing ground."""
    import city_world
    if unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_path_name().split('.')[0]==city_world.MAP:
        return city_world.street_view()
    actor=_selected(required=False)
    if actor is not None and _vehicle_id(actor) is not None:
        stop_following()
        forward=actor.get_actor_forward_vector()
        # Outside the opaque illustrative cabin, so windows do not hide the view.
        eye=actor.get_actor_location()+forward*400+unreal.Vector(0,0,170)
        _look_at(eye,eye+forward*3000)
        return
    data=_sources()
    identifier=_road_id(actor.get_actor_label()) if actor is not None else None
    if identifier is not None and identifier not in data['roads']:
        raise RuntimeError('The selected road has no matching source record')
    if identifier is not None:
        roads=[data['roads'][identifier]]
        point=roads[0]['path'][len(roads[0]['path'])//2]
    else:
        roads=list(data['roads'].values())
        point=_local(actor.get_actor_location()) if actor is not None else [0,0,0]
    candidates=[]
    for road in roads:
        for first,second in zip(road['path'],road['path'][1:]):
            middle=[(a+b)*.5 for a,b in zip(first,second)]
            direction=_world(second)-_world(first)
            if direction.length()<1:continue
            distance=(middle[0]-point[0])**2+(middle[1]-point[1])**2
            candidates.append((distance,middle,direction))
    if not candidates:raise RuntimeError('No valid exported road segment is available for street view')
    _,middle,direction=min(candidates,key=lambda item:item[0])
    stop_following()
    eye=_world(middle)+unreal.Vector(0,0,170)
    _look_at(eye,eye+direction*20)


def _follow_tick(delta):
    global _last_update
    now=time.monotonic()
    if now-_last_update<1/30:return
    _last_update=now
    import preview_traffic
    actor=preview_traffic._cars.get(_follow_id)
    if actor is None:
        stop_following()
        unreal.log('Camera follow stopped: the selected vehicle left the recorded replay.')
        return
    try:
        position=actor.get_actor_location()
        forward=actor.get_actor_forward_vector()
        _look_at(position-forward*1500+unreal.Vector(0,0,650),position+forward*800+unreal.Vector(0,0,100))
    except Exception as error:
        stop_following()
        unreal.log_warning('Camera follow stopped: '+str(error))


def _start_follow(identifier):
    global _follow_callback,_follow_id,_last_update
    stop_following()
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_viewport_realtime(True)
    _follow_id=identifier;_last_update=0
    _follow_tick(0)
    if _follow_id is not None:_follow_callback=unreal.register_slate_post_tick_callback(_follow_tick)


def follow_selected_vehicle():
    actor=_selected();identifier=_vehicle_id(actor)
    if identifier is None:raise RuntimeError('Select a moving replay vehicle, rather than a building, road or signal.')
    _start_follow(identifier)


def follow_traffic_vehicle():
    """Convenience action: select and follow a current replay car near the origin."""
    import preview_traffic
    if not preview_traffic._cars:raise RuntimeError('Start the recorded traffic replay before following a vehicle.')
    origin=_basis()[0]
    identifier,actor=min(preview_traffic._cars.items(),key=lambda item:(item[1].get_actor_location()-origin).length())
    unreal.get_editor_subsystem(unreal.EditorActorSubsystem).set_selected_level_actors([actor])
    _start_follow(identifier)


def inspect_selected(show_dialog=True):
    """Return source-aware data and write a readable private report; no inference of missing fields."""
    actor=_selected();label=actor.get_actor_label()
    vehicle=_vehicle_id(actor)
    report={'label':label,'source_record':{},'presentation':{},'limitations':[]}
    if vehicle is not None:
        report['kind']='vehicle';report['source_record']={'vehicle_id':vehicle,'motion':'Recorded SUMO replay'}
        report['presentation']={'current_local_position_m':_local(actor.get_actor_location()),'asset':'Illustrative car model and paint'}
        report['limitations']=['This is recorded simulated traffic, not an observed live vehicle.','Camera follow has no building avoidance or vehicle physics.']
    elif 'DYNACITY_POINT_CLOUD' in [str(t) for t in actor.tags]:
        import point_cloud_layer
        identifier=next(str(t).split(':',1)[1] for t in actor.tags if str(t).startswith('capture:'))
        source=next((s for s in point_cloud_layer._sources() if s['id']==identifier),{})
        points=actor.get_point_cloud().get_num_points()
        kind=source.get('display_kind','all source points')
        report['kind']='point_cloud'
        report['source_record']={'capture':identifier,'original_points':source.get('source_point_count',points),
                                 'acquisition_date':source.get('acquisition_date') or 'Not verified'}
        report['presentation']={'display_points':points,'display_kind':kind,'colours':'Source RGB with inspection exposure'}
        report['limitations']=['Source alignment and vertical datum remain unverified.']
        if kind=='sampled preview':report['limitations'].append('This viewing subset omits source samples; it does not reconstruct missing surfaces.')
    elif label.startswith(('buildings_','roads_')):
        import city_world
        data=json.loads(city_world._checkpoint().read_text())
        package=next(p for p in data['packages'] if 'city_package:'+p['key'] in actor.tags)
        index=json.loads(Path(package['metadata']).read_text())
        objects=[record for record in index['objects'] if record['batch']==label]
        report['kind']='source_batch'
        report['source_record']={'production_area':package['production_group'],'layer':package['kind'],
            'source_objects':len(objects),'source_ids_preview':[o['source_id'] for o in objects[:5]],
            'object_index':package['metadata']}
        report['presentation']={'facade':'Window patterns and colours are illustrative.'} if package['kind']=='buildings' else {'width':'Lane-derived; not a curb survey.'}
        report['limitations']=['This selection contains several source objects batched for performance.',
            'Individual source IDs remain in the companion index. Imported Unreal face identity is not yet verified.']
    elif label.startswith('beirut001_'):
        report['kind']='building';identifier='beirut001:'+label[len('beirut001_'):]
        report['source_record']={'source_id':identifier}
        try:metadata=_sources()['buildings'].get(identifier)
        except RuntimeError as error:
            metadata=None;report['limitations'].append(str(error))
        if metadata is not None:
            for key in ['source','source_index','alignment','height_m','roof_levels','bbed_objectid']:
                if key in metadata:report['source_record'][key]=metadata[key]
            if report['source_record'].get('bbed_objectid',-1)<0:report['source_record'].pop('bbed_objectid',None)
        else:report['limitations'].append('No matching source geometry record was found; no height or age is inferred.')
        report['presentation']={'facade':'Paint, windows, sill shading and weathering are illustrative where applied.'}
        report['limitations'].append('Source model heights and inferred alignment are not a new survey or confirmation of present-day conditions.')
    elif _road_id(label) is not None:
        report['kind']='road';identifier=_road_id(label)
        report['source_record']={'road_id':int(identifier)}
        try:road=_sources()['roads'].get(identifier)
        except RuntimeError as error:
            road=None;report['limitations'].append(str(error))
        if road is not None:
            report['source_record'].update({key:road[key] for key in ['name','source_way','level','height_source'] if key in road})
            report['presentation']['width_m']=road.get('width_m')
        else:report['limitations'].append('No matching source road record was found.')
        report['limitations'].append('Display width, lane paint and bridge supports may include illustrative assumptions.')
    else:
        report['kind']='unmatched'
        report['limitations']=['This actor is not a recognized source building, source road or current replay vehicle. No source properties are inferred.']
    report['coordinate_note']='Local east/north/up metres; source vertical datum unverified.'
    lines=[label,'', 'Source record']
    names={'source_id':'Building identity','source':'Source','source_index':'Source object index','alignment':'Alignment',
           'height_m':'Source model height (m)','roof_levels':'Roof levels in source','bbed_objectid':'Provisional BBED reference',
           'road_id':'Road identity','name':'Road name','source_way':'OSM way','level':'Network level','height_source':'Elevation source',
           'vehicle_id':'Vehicle identity','motion':'Motion source','facade':'Facade treatment','width_m':'Display width (m)',
           'current_local_position_m':'Rendered position (m)','asset':'Visual asset'}
    names.update(capture='Capture identity',original_points='Original point count',acquisition_date='Capture date',
                 display_points='Viewing point count',display_kind='Viewing representation',colours='Colour source')
    lines += [names.get(key,key)+': '+str(value) for key,value in report['source_record'].items()]
    if report['presentation']:
        lines+=['','Presentation']+[names.get(key,key)+': '+str(value) for key,value in report['presentation'].items()]
    lines+=['','Data limits']+report['limitations']+[report['coordinate_note']]
    folder=Path(unreal.Paths.project_saved_dir()).resolve()/'DynaCity'
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'last-inspection.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (folder/'last-inspection.txt').write_text('\n'.join(lines),encoding='utf-8')
    if show_dialog:unreal.EditorDialog.show_message('Beirut object inspection','\n'.join(lines),unreal.AppMsgType.OK)
    return report


def status():return {'following_vehicle':_follow_id,'follow_running':_follow_callback is not None}


def play():
    import preview_traffic
    if preview_traffic._paused_at is not None:preview_traffic.resume()
    elif preview_traffic._callback is None:preview_traffic.start_from_config()


def pause():
    import preview_traffic
    stop_following();preview_traffic.pause()


def restart():
    import preview_traffic
    stop_following();preview_traffic.stop();preview_traffic.start_from_config()


def run(action):
    """Menu entry point; keep missing selection/data errors readable in the editor log."""
    import city_world
    if action=='drive':
        try:
            import setup_driving
            return setup_driving.play()
        except Exception as error:unreal.log_warning('Explore Beirut: '+str(error));return
    if action.startswith('area:'):
        try:return city_world.focus_area(action[5:])
        except Exception as error:unreal.log_warning('Explore Beirut: '+str(error));return
    actions={'city':city_world.open_city,'pilot':city_world.open_pilot,'play':play,'pause':pause,'restart':restart,'overview':overview,'focus':focus_selection,'street':street_view,'follow':follow_selected_vehicle,
             'traffic':follow_traffic_vehicle,'stop':stop_following,'inspect':inspect_selected}
    if action not in actions:raise ValueError(action)
    try:return actions[action]()
    except Exception as error:unreal.log_warning('Explore Beirut: '+str(error))


def register_menu():
    menus=unreal.ToolMenus.get();menus.unregister_owner_by_name(_owner)
    menu=menus.extend_menu('LevelEditor.MainMenu.Tools').add_sub_menu(_owner,'DynaCity','DynaCityExplore','Explore Beirut','Camera and source inspection controls')
    for name,label,action,section in [
        ('CityWorld','Open full-city geometry','city','Worlds'),('PilotWorld','Open traffic and capture pilot','pilot','Worlds'),
        ('Play','Play / resume traffic replay','play','Playback'),('Pause','Pause traffic replay','pause','Playback'),
        ('Restart','Restart traffic replay','restart','Playback'),
        ('Overview','City overview','overview','Camera'),('Focus','Focus selected object','focus','Camera'),
        ('Street','Street view near selection','street','Camera'),('Traffic','Follow a traffic vehicle','traffic','Traffic'),
        ('Follow','Follow selected vehicle','follow','Traffic'),('Stop','Stop following','stop','Traffic'),
        ('Inspect','Inspect selected source object','inspect','Inspect')]:
        entry=unreal.ToolMenuEntry(name=name,type=unreal.MultiBlockType.MENU_ENTRY)
        entry.set_label(label)
        entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'',"import explore_city; explore_city.run('"+action+"')")
        menu.add_menu_entry(section,entry)
    search_entry=unreal.ToolMenuEntry(name='LocationSearch',type=unreal.MultiBlockType.MENU_ENTRY)
    search_entry.set_label('Search location…')
    search_entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'',"import location_search; location_search.open_search()")
    menu.add_menu_entry('Camera',search_entry)
    menus.refresh_all_widgets()
    if unreal.load_class(None,'/Script/DynaCityDrive.DynaDriveGameMode'):
        entry=unreal.ToolMenuEntry(name='Drive',type=unreal.MultiBlockType.MENU_ENTRY)
        entry.set_label('Start driving')
        entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'',"import explore_city; explore_city.run('drive')")
        menu.add_menu_entry('Driving',entry)
        menus.refresh_all_widgets()
    import city_world
    if city_world._checkpoint().is_file():
        data=json.loads(city_world._checkpoint().read_text())
        areas=sorted({p['production_group'] for p in data['packages'] if p.get('kind')=='buildings'})
        submenu=menu.add_sub_menu(_owner,'Worlds','DynaCityAreas','Go to area','Named Beirut production areas')
        for i,name in enumerate(areas):
            entry=unreal.ToolMenuEntry(name='Area'+str(i),type=unreal.MultiBlockType.MENU_ENTRY)
            entry.set_label(name)
            entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'',"import explore_city; explore_city.run("+repr('area:'+name)+")")
            submenu.add_menu_entry('Areas',entry)
        menus.refresh_all_widgets()
