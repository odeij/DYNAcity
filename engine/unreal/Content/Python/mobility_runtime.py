"""Editor-hosted driving adapter: bounded native map and buffered SUMO rendering."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import unreal

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='BeirutSUMO')
_future = None
_client = None
_renderer = None
_pawn = None
_closing = False
_enabled = False
_status = 'SUMO disconnected'
_last_poll = 0
_map = None
_night = None
_basis_values = None
_batches_received = 0
_last_view = None
_last_batch_at = 0


def player_view():
    """Capture Unreal position on the game thread; send only numbers to the worker."""
    import location_search
    from mobility_core import traffic_view
    origin,*axes=_basis_values
    delta=_pawn.get_actor_location()-origin
    point=[float(delta.dot(axis)/axis.dot(axis)) for axis in axes]
    import library_assets
    return traffic_view(location_search._data(),point,radius=library_assets.active().get('traffic_view_radius_m',350))


def world_basis():
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    if not world:
        import explore_city
        return explore_city._basis()
    markers={}
    for actor in unreal.GameplayStatics.get_all_actors_of_class(world,unreal.Actor):
        label=actor.get_actor_label()
        for name in ('ORIGIN','EAST','NORTH','UP'):
            if label.startswith('DYNACITY_BASIS_'+name):markers[name]=actor.get_actor_location()
    if len(markers)!=4:raise RuntimeError('The driving world is missing coordinate markers.')
    origin=markers['ORIGIN'];axes=[(markers[n]-origin)/10 for n in ('EAST','NORTH','UP')]
    if any(not 99<axis.length()<101 for axis in axes):raise RuntimeError('Unexpected map coordinate scale.')
    return origin,*axes


def attach(pawn):
    global _pawn, _renderer, _map, _basis_values
    import location_search, explore_city
    index = location_search._data()
    # Cache simplified cartographic geometry once. Keep original road paths for Z.
    if _map is None:
        from mobility_core import map_payload
        _map = json.dumps(map_payload(index), separators=(',', ':'))
    _basis_values=world_basis()
    origin, east, north, up = _basis_values
    _pawn = pawn
    import library_assets
    presentation=library_assets.configure_vehicle(pawn)
    # A background-specific asset can reduce rendering work while the driven
    # car retains its original full-detail mesh. Missing configuration keeps the
    # existing shared mesh; configured but invalid assets fail visibly.
    traffic_mesh = pawn.body.static_mesh
    traffic_mesh_path = presentation.get('traffic_default_mesh')
    if traffic_mesh_path is not None:
        if not isinstance(traffic_mesh_path, str) or not traffic_mesh_path.startswith('/Game/'):
            raise ValueError('traffic_default_mesh must be an imported /Game/ asset path.')
        traffic_mesh = unreal.load_asset(traffic_mesh_path)
        if not isinstance(traffic_mesh, unreal.StaticMesh):
            raise ValueError('Default traffic asset is missing or is not a static mesh: ' + traffic_mesh_path)
    _renderer = pawn.create_mobility_world()
    _renderer.configure(_map, origin, east, north, up, traffic_mesh)
    fleet_paths = presentation.get('traffic_fleet_meshes', [])
    if not isinstance(fleet_paths, list) or len(fleet_paths) > 15:
        raise ValueError('traffic_fleet_meshes must be a list of at most 15 asset paths.')
    if fleet_paths:
        if not hasattr(_renderer, 'configure_traffic_fleet'):
            raise RuntimeError('The driving plugin must be rebuilt before using a traffic fleet.')
        meshes = []
        for path in fleet_paths:
            if not isinstance(path, str) or not path.startswith('/Game/'):
                raise ValueError('Traffic fleet assets must be imported /Game/ asset paths.')
            mesh = unreal.load_asset(path)
            if not isinstance(mesh, unreal.StaticMesh):
                raise ValueError('Traffic fleet asset is missing or is not a static mesh: ' + path)
            meshes.append(mesh)
        _renderer.configure_traffic_fleet(meshes)
    type_groups = presentation.get('traffic_type_groups', {})
    if not isinstance(type_groups, dict) or any(
        not isinstance(key, str) or not key or isinstance(value, bool)
        or not isinstance(value, int) or not 0 <= value <= len(fleet_paths)
        for key, value in type_groups.items()
    ):
        raise ValueError('traffic_type_groups must map SUMO type IDs to configured mesh indexes.')
    if type_groups:
        if not hasattr(_renderer, 'configure_traffic_types'):
            raise RuntimeError('The driving plugin must be rebuilt before mapping traffic types.')
        _renderer.configure_traffic_types(type_groups)
    _renderer.set_editor_property('visibility_radius_m',presentation.get('traffic_visibility_radius_m',250))
    _renderer.set_editor_property('max_visible_vehicles',presentation.get('traffic_vehicle_limit',150))
    import ambient_effects
    ambient_effects.configure(pawn, up)
    import play_performance
    play_performance.set_playing(True)


_local = False   # True while the built-in ambient traffic (no SUMO) is feeding the renderer


def start_local():
    """Ambient traffic on modeled roads near the player when no SUMO server is connected."""
    global _client, _future, _enabled, _status, _batches_received, _last_view, _last_batch_at, _local
    import location_search, library_assets
    from traffic_local import LocalTrafficClient
    from traffic_appearance import validate
    if _renderer is None or _client is not None or _future is not None: return
    _last_view = player_view()
    _client = LocalTrafficClient(location_search._data(), _last_view)
    _client.appearance = validate(library_assets.active())
    _batches_received = 0; _last_batch_at = 0
    _renderer.clear_traffic(); _enabled = True; _local = True
    _status = 'Ambient traffic (built-in, not SUMO) · illustrative'
    _future = _executor.submit(_client.batch)


def start():
    global _client, _future, _enabled, _status, _batches_received, _last_view, _last_batch_at, _local
    import location_search, explore_city
    from traffic_client import TrafficClient
    if _renderer is None: raise RuntimeError('Start driving before connecting SUMO.')
    if _local and _future is None:   # SUMO replaces the built-in ambient traffic
        _client = None; _enabled = False; _local = False
    if _future is not None or _client is not None: raise RuntimeError('Wait for the previous SUMO request to finish.')
    config = json.loads((Path(__file__).resolve().parents[2]/'prototype_config.json').read_text())
    _last_view=player_view()
    import library_assets
    presentation = library_assets.active()
    mapping_path = presentation.get('traffic_road_mapping')
    mapping = json.loads(Path(mapping_path).read_text(encoding='utf-8')) if mapping_path else None
    _client = TrafficClient(location_search._data(), config.get('sumo_url','http://127.0.0.1:8014'),
                            _last_view, road_mapping=mapping)
    from traffic_appearance import validate
    _client.appearance = validate(presentation)
    mixed = presentation.get('traffic_mixed', False)
    if not isinstance(mixed, bool) or (mixed and not presentation.get('traffic_type_groups')):
        _client = None
        raise ValueError('Mixed traffic requires a configured traffic_type_groups mapping.')
    _client.request['mixed'] = mixed
    _batches_received=0;_last_batch_at=0
    _renderer.clear_traffic(); _enabled = True; _status = 'Connecting to local SUMO…'
    _future = _executor.submit(_client.batch)


def stop():
    global _enabled, _closing, _future, _status, _local
    _local = False
    _enabled = False; _closing = _client is not None or _future is not None; _status = 'SUMO disconnected'
    if _renderer is not None:
        try: _renderer.clear_traffic()
        except Exception: pass
    if _future is None and _client is not None: _future = _executor.submit(_client.close)


def tick(pawn):
    global _pawn, _renderer, _future, _client, _closing, _enabled, _status, _last_poll, _night
    global _batches_received, _last_view, _last_batch_at
    if pawn != _pawn:
        if _pawn is not None: stop()
        if pawn is None:
            import play_performance
            play_performance.set_playing(False)
        _pawn = None; _renderer = None
        _night = None
        if pawn is not None:
            attach(pawn)
            try: start_local()
            except Exception as error: unreal.log_warning('Ambient traffic unavailable: '+str(error))
    if _future is not None and _future.done():
        future, _future = _future, None
        try:
            payload = future.result()
            if _closing:
                if _client is not None and _client.session:
                    _future = _executor.submit(_client.close)
                else: _client = None; _closing = False
            elif _enabled and _renderer is not None:
                if not _renderer.append_frames(payload): raise ValueError('SUMO frames are not strictly increasing.')
                _batches_received+=1;_last_batch_at=time.monotonic()
                if _client.last_batch_received and not _client.last_batch_placed:
                    _status = 'SUMO vehicles could not be placed on city roads; check the traffic model and map.'
                else:
                    _status = ('Ambient traffic (built-in, not SUMO) · illustrative' if _local else
                               'SUMO connected · synthetic trips · traffic has no player collision')
        except Exception as error:
            _status = 'SUMO connection failed: '+str(error)
            _enabled = False; _closing = True
            if _renderer is not None:_renderer.clear_traffic()
            if _client is not None and _client.session: _future = _executor.submit(_client.close)
            else: _client = None; _closing = False
    now = time.monotonic()
    if _enabled and _future is None and _renderer is not None and now-_last_poll > .25:
        _last_poll = now
        if json.loads(_renderer.status())['buffer_s'] < 4:
            _last_view=player_view()
            _future = _executor.submit(_client.batch,_last_view)


def status():
    render=json.loads(_renderer.status()) if _renderer is not None else {}
    waiting=(_enabled and _batches_received and _future is not None and
             render.get('buffer_s',0)<.05 and time.monotonic()-_last_batch_at>3)
    return {'enabled': _enabled, 'message': 'Waiting for the next SUMO batch…' if waiting else _status,
            'render':render,'batches_received':_batches_received,'view_lonlat':_last_view,
            'last_batch':getattr(_client, 'last_batch_stats', {}) if _client is not None else {}}
