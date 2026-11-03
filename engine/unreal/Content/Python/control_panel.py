"""Allowlisted actions for the bundled Bootstrap GUI in the Unreal editor prototype."""
import json
from pathlib import Path
import time
import unreal

_panel = None
_tick = None
_last_update = 0
_search_status = 'Select a result to go there.'
_layers = {'trees':True,'greenery':True,'references':True,'preview':True}
_last_world = None
_pending_start = False
_entry_handled = False
_was_playing = False
_session = None   # identity of the running Play session (pawn, world clock) so a restarted Play re-enters the menu
_coverage = None
_menu = 'title'   # in-viewport overlay: 'title' when Play begins, 'pause' once the player has driven


def _show_menu(page):
    """Full-viewport title/pause menu inside the Play window; the older panel window if the native module predates it."""
    if hasattr(_panel,'show_overlay'):_panel.show_overlay(page)
    else:_panel.show('search' if page=='search' else 'explore')


def _game():
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    return world, unreal.GameplayStatics.get_player_pawn(world,0) if world else None


def _map_preferences_path():
    return Path(unreal.Paths.project_saved_dir()) / 'DynaCity' / 'map-preferences.json'


def map_settings(pawn=None):
    """Native keyboard changes and the panel read the same authoritative values."""
    import map_preferences
    native = getattr(getattr(pawn, 'mobility', None), 'minimap', None)
    if native is not None:
        status = json.loads(native.status())
        return ({'version': 1, 'mode': status['mode'], 'heading_up': status['heading_up']},
                status.get('preferences_error', ''))
    try:
        return map_preferences.load(_map_preferences_path()), ''
    except (OSError, ValueError) as error:
        return map_preferences.defaults(), 'Could not load saved map settings: ' + str(error)


def set_map_settings(patch, pawn=None):
    import map_preferences
    current, _ = map_settings(pawn)
    selected = map_preferences.merge(current, patch)
    native = getattr(getattr(pawn, 'mobility', None), 'minimap', None)
    if native is not None:
        if not native.set_preferences(selected['mode'], selected['heading_up']):
            raise RuntimeError(json.loads(native.status()).get('preferences_error') or 'Could not save map settings.')
    else:
        map_preferences.save(_map_preferences_path(), selected)
    return selected


def _mode(world):
    """city_view (editor camera), driving_menu (Play, car held, menu has input) or driving."""
    if world is None:return 'city_view'
    try:
        pc=unreal.GameplayStatics.get_player_controller(world,0)
        return 'driving_menu' if pc is not None and pc.get_editor_property('show_mouse_cursor') else 'driving'
    except Exception:return 'driving'


_page_request={'id':0,'page':'explore'}
_PAGES=('explore','map','search','controls','layers')


def request_page(page,zoom=None):
    """Ask the open panel to show one of its own pages (the native Show() only forwards some of them)."""
    if page not in _PAGES:raise ValueError(f'Unknown panel page: {page}')
    _page_request['id']+=1;_page_request['page']=page;_page_request.pop('zoom',None)
    if zoom is not None:_page_request['zoom']=float(zoom)
    if _panel is not None:_panel.set_state(json.dumps(state(),ensure_ascii=False))


def state():
    global _search_status,_coverage
    import location_search
    world,pawn=_game()
    drive=json.loads(pawn.get_drive_status()) if pawn and hasattr(pawn,'get_drive_status') else {}
    try:results=location_search.status()['results']
    except (OSError,ValueError):results=[]
    if _search_status.startswith('Resolving') and location_search._future is None:
        _search_status=f"{len(results)} result(s). Select one to go there."
    import mobility_runtime
    map_position=None
    if pawn and mobility_runtime._basis_values:
        origin,*axes=mobility_runtime._basis_values;delta=pawn.get_actor_location()-origin
        map_position=[float(delta.dot(axis)/axis.dot(axis)) for axis in axes[:2]]
    if _coverage is None:
        path=Path(unreal.Paths.project_saved_dir())/'DynaCity'/'mobility-coverage.json'
        _coverage=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    preferences, preferences_error = map_settings(pawn)
    return {'playing':world is not None,'mode':_mode(world),'menu':_menu,'area':drive.get('start_area','Beirut'),
            'map_settings': preferences, 'map_settings_error': preferences_error,
            'page_request':dict(_page_request),
            'coverage':_coverage,
            'map_position':map_position,
            'night':drive.get('night_mode',False),'traffic':mobility_runtime.status(),
            'time_of_day':drive.get('time_of_day',14),
            'clouds_enabled':drive.get('clouds_enabled',True),
            'surface_status':drive.get('surface_status'),'layers':_layers,
            'results':results,'search_status':_search_status}


def _update(delta=0):
    global _last_update,_last_world,_pending_start,_entry_handled,_was_playing,_menu
    now=time.monotonic()
    if now-_last_update<.5:return
    _last_update=now
    try:
        world,pawn=_game()
        import mobility_runtime
        mobility_runtime.tick(pawn if pawn and hasattr(pawn,'create_mobility_world') else None)
        playing=world is not None
        if playing and _entry_handled and _new_session(world,pawn):_entry_handled=False  # Play restarted without a gap
        if playing and pawn and hasattr(pawn,'open_controls') and not _entry_handled:
            _entry_handled=True;_new_session(world,pawn);_menu='title'
            if _pending_start:pawn.resume_driving();_pending_start=False;_menu='pause'   # chose Start driving: go
            elif _panel is not None:pawn.open_controls();_show_menu('title')  # Play button: title menu over the city
        elif not playing:
            _entry_handled=False;_session_reset()
            if _was_playing and _panel is not None:_panel.show('explore')  # Play ended: back to the menu
        _was_playing=playing
        key=world.get_path_name() if world else 'editor'
        if key!=_last_world:
            _last_world=key
            for layer,enabled in _layers.items():
                if not enabled:_toggle_layer(layer,enabled)
        if _panel is not None:_panel.set_state(json.dumps(state(),ensure_ascii=False))
    except Exception as error:unreal.log_warning('Beirut controls state: '+str(error))


def _session_reset():
    global _session
    _session=None


def _new_session(world,pawn):
    """True when this is a different Play session than the one last recorded (and records it).
    A Play restarted inside one 0.5 s tick never shows `playing=False`, so the boolean alone would
    leave the new session driving with no menu; a new pawn or a world clock that went backwards says so."""
    global _session
    try:clock=float(unreal.GameplayStatics.get_time_seconds(world));ident=(pawn.get_path_name()+'#'+str(hash(pawn))) if pawn else ''
    except Exception:return False
    previous=_session;_session=(ident,clock)
    return previous is not None and (previous[0]!=ident or clock+.25<previous[1])


def _toggle_layer(layer,enabled):
    if layer not in _layers or not isinstance(enabled,bool):raise ValueError('Unknown map layer.')
    import city_world,street_context
    world,_=_game()
    if world:
        actors=unreal.GameplayStatics.get_all_actors_of_class(world,unreal.Actor)
    else:
        city_world._current();street_context.load_editor_geometry();actors=city_world._actors()
    green={'wood','scrub','grass','park','garden','orchard','allotments','farmland','village_green',
           'heath','shrubbery','flowerbed','vineyard','plant_nursery','wetland','reedbed','moss',
           'greenfield','greenhouse_horticulture','green_roof','grass_paver'}
    changed=0
    for actor in actors:
        tags={str(t) for t in actor.tags}
        matches=('DYNACITY_REFERENCE_PREVIEW' in tags if layer=='preview' else
                 any(t.startswith('street_kind:tree_') for t in tags) if layer=='trees' else
                 'cover_kind:greenery_reference' in tags if layer=='references' else
                 bool(tags.intersection({'cover_kind:'+k for k in green})))
        if not matches:continue
        if world:actor.set_actor_hidden_in_game(not enabled)
        else:actor.set_is_temporarily_hidden_in_editor(not enabled)
        changed+=1
    _layers[layer]=enabled
    return {'message':f'{layer.title()} {"shown" if enabled else "hidden"} ({changed} groups).'}


def execute(request):
    """The browser cannot execute arbitrary Python, paths, URLs or console commands."""
    global _search_status,_pending_start,_menu
    import explore_city,city_world,location_search,setup_driving
    data=json.loads(request)
    if not isinstance(data,dict):raise ValueError('Invalid control request.')
    action=data.get('action');world,pawn=_game()
    if action in ('morning','afternoon','sunset','clouds'):
        if not pawn:raise ValueError('Start driving to use atmosphere controls.')
        if action=='clouds':pawn.set_clouds_enabled(not pawn.clouds_enabled)
        else:pawn.set_time_of_day({'morning':8.,'afternoon':14.,'sunset':17.5}[action])
        return {'message':'Illustrative atmosphere updated; SUMO demand and clock are unchanged.'}
    if action=='map_settings':
        selected = set_map_settings({key: value for key, value in data.items() if key != 'action'}, pawn)
        return {'map_settings': selected, 'message': 'Map settings saved for the atlas and driving minimap.'}
    if action=='map_data':
        from mobility_core import map_payload
        return {'map':map_payload(location_search._data())}
    if action=='map_pick':
        import math,location_core
        x,y=data.get('x'),data.get('y')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>100000 for v in (x,y)):
            raise ValueError('Choose a point on the Beirut road map.')
        index=location_search._data();origin=index['origin_utm_m']
        lon,lat=location_core.utm_to_lonlat(origin[0]+x,origin[1]+y)
        rows=location_search.search(f'{lat},{lon}')
        if rows:rows[0]['name']='Selected map location'
        _search_status='Choose the selected location below. Driving still checks for a safe nearby road.'
        return {'message':_search_status}
    if action in ('close','resume','fly_resume') or action=='drive' and world:
        _panel.close()
        if world:_menu='pause'
        if pawn and hasattr(pawn,'resume_driving'):
            pawn.resume_driving()
            if action=='fly_resume' and not json.loads(pawn.get_drive_status()).get('fly_mode'):pawn.toggle_fly_mode()
        return {'message':'Driving resumed.' if world else 'Menu closed.'}
    if action=='drive':
        result=setup_driving.play();_pending_start=True;_panel.close();return result
    if action=='end_drive':
        _panel.close()
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_end_play()
        return {'message':'Driving ended. Choose a city view or start again.'}
    if action=='search':
        query=data.get('query')
        if not isinstance(query,str):raise ValueError('Enter a place name or map location.')
        rows=location_search.search(query)
        if location_search._future is not None:_search_status='Resolving the map linkâ€¦'
        else:_search_status=f'{len(rows)} result(s). Select one to go there.' if rows else 'No matching downloaded name. Try another spelling or a coordinate link.'
        return {'message':_search_status}
    if action=='go_to':
        index=data.get('index')
        if isinstance(index,bool) or not isinstance(index,int):raise ValueError('Select a current result.')
        result=location_search.go_to(index)
        if result.get('error'):return result
        if world:
            _panel.close();pawn.resume_driving();_menu='pause'
        return {'message':'At '+result['name']+'. Close the panel to see the location.'}
    if action=='layer':return _toggle_layer(data.get('layer'),data.get('enabled'))
    if action in ('night','sumo_start','sumo_stop'):
        if not pawn:raise ValueError('Start driving to use lighting and SUMO controls.')
        import mobility_runtime
        if action=='night':pawn.toggle_night()
        elif action=='sumo_start':mobility_runtime.start()
        else:mobility_runtime.stop()
        return {'message':{'night':'Lighting changed. Press L while driving to toggle it.',
                           'sumo_start':'Connecting to local SUMOâ€¦','sumo_stop':'SUMO disconnected.'}[action]}
    if action in ('camera','reset','next_area','fly','recover'):
        if not pawn:raise ValueError('Start driving to use vehicle controls.')
        getattr(pawn,{'camera':'cycle_camera','reset':'reset_to_start','next_area':'next_area',
                      'fly':'toggle_fly_mode','recover':'recover_vehicle'}[action])()
        return {'message':{'camera':'Camera changed.','reset':'Car reset.','next_area':'Moved to the next area.',
                           'fly':'Flight mode toggled. Leaving flight returns to the parked car at departure, not the flight location.',
                           'recover':'Car recovered to a recent safe ground checkpoint nearby, or start if none is available.'}[action]}
    if world:raise ValueError('End driving before choosing an editor camera view.')
    if action=='overview':city_world.overview()
    elif action in ('street','trees_closeup','conifers_closeup'):
        import street_context
        if action=='street':street_context.open_reference()
        elif action=='trees_closeup':street_context.open_tree_reference()
        else:
            import greenery_verification
            greenery_verification.focus_conifer()
    else:raise ValueError('Unknown control action.')
    _panel.close();return {'message':'Camera view changed.'}


def _request(request):
    try:response=execute(request)
    except Exception as error:response={'error':str(error)}
    _panel.set_response(json.dumps(response,ensure_ascii=False))
    # Commit state before resolving the browser promise. A throttled tick can
    # otherwise briefly restore the previous checkbox state after a click.
    _panel.set_state(json.dumps(state(),ensure_ascii=False))


def open_panel(page='explore'):
    initialize();_panel.set_state(json.dumps(state(),ensure_ascii=False));_panel.show(page)


def initialize():
    global _panel,_tick
    if _panel is None:
        cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaControlPanel')
        if cls is None:raise RuntimeError('Install the updated native controls module and restart Unreal.')
        _panel=unreal.new_object(cls);_panel.request_received.add_callable(_request)
        _panel.set_state(json.dumps(state(),ensure_ascii=False))
    if _tick is None:_tick=unreal.register_slate_post_tick_callback(_update)


def register():
    initialize()
    menus=unreal.ToolMenus.get()
    menu=menus.extend_menu('LevelEditor.MainMenu.Tools.DynaCityExplore')
    entry=unreal.ToolMenuEntry(name='BeirutControlPanel',type=unreal.MultiBlockType.MENU_ENTRY)
    entry.set_label('Open Explore Beirut control panel')
    entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'','import control_panel; control_panel.open_panel()')
    menu.add_menu_entry('Start here',entry);menus.refresh_all_widgets()
    # Open after startup settles; this callback performs no imports or saving.
    started=time.monotonic()
    def welcome(delta):
        if time.monotonic()-started<3:return
        unreal.unregister_slate_post_tick_callback(welcome_tick)
        import city_world
        world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        if world and world.get_path_name().split('.')[0]==city_world.MAP:
            city_world.overview()
        try:
            import library_assets
            library_assets.enforce_scene_policy()
        except RuntimeError as error:unreal.log_warning(str(error))
        open_panel()
    welcome_tick=unreal.register_slate_post_tick_callback(welcome)
