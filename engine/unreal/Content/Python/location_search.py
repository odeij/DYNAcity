"""Native Unreal editor/PIE search backed by downloaded OSM names and map links."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import unreal
import location_core as core

_panel = globals().get('_panel')
_index = None
_results = []
_last_navigation = None
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='BeirutMapLink')
_future = None
_tick = None
_generation = 0


def _data():
    global _index
    if _index is None:
        path = Path(unreal.Paths.project_saved_dir())/'DynaCity'/'location-index.json'
        _index = json.loads(path.read_text(encoding='utf-8'))
        if _index.get('schema') != 'dynacity-locations/1': raise ValueError('Unsupported location index.')
    return _index


def _status(message):
    if _panel is not None: _panel.set_status(message)
    unreal.log('Beirut location search: '+message)


class _MapRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if (url.scheme != 'https' or not core.google_host(url.hostname or '')
                or url.port not in (None,443) or url.username or url.password):
            raise ValueError('The map short link redirected outside supported Google Maps hosts.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _expand(link):
    # Only the redirect URL is used. No Google map imagery or HTML is imported.
    with build_opener(_MapRedirects()).open(Request(link, headers={'User-Agent': 'DYNAcity-Beirut/1.0'}), timeout=10) as response:
        expanded = response.geturl()
    parsed = core.parse_location(expanded)
    if 'short_link' in parsed:
        raise ValueError('Could not expand this link. Paste the full Maps URL or coordinates.')
    return parsed


def _show_results(parsed):
    global _results
    _results = core.search(_data(), parsed)
    for row in _results:
        kind = {'road_attributes':'Street', 'poi':'Place', 'coordinates':'Coordinates'}.get(
            row.get('kind'), row.get('kind','place').replace('_',' ').title())
        row['detail'] = f"{kind} · {row['lat']:.6f}, {row['lon']:.6f}"
    if _panel is not None: _panel.set_results(json.dumps(_results, ensure_ascii=False))
    _status(f'{len(_results)} result(s). Select a place to go there.' if _results else
            'No downloaded OSM name matches. Try another spelling or paste coordinates / a pin link.')
    return _results


def _poll(delta):
    global _future, _tick
    if _future is None or not _future[1].done(): return
    generation, future = _future
    _future = None
    unreal.unregister_slate_post_tick_callback(_tick); _tick = None
    if generation != _generation: return
    try: _show_results(future.result())
    except Exception as error: _status(str(error))


def search(query):
    global _generation, _future, _tick, _results
    _generation += 1
    _results = []
    if _panel is not None: _panel.set_results('[]')
    try:
        parsed = core.parse_location(query)
        if 'short_link' in parsed:
            if _future is not None and not _future[1].done():
                _status('A map link is still resolving. Please try again when it finishes.'); return []
            _future = (_generation, _executor.submit(_expand, parsed['short_link']))
            if _tick is None: _tick = unreal.register_slate_post_tick_callback(_poll)
            _status('Resolving the Google Maps share link…'); return []
        return _show_results(parsed)
    except Exception as error:
        _status(str(error)); return []


def go_to(index):
    global _last_navigation
    try:
        if not 0 <= index < len(_results): raise ValueError('Search again and select a current result.')
        import city_world, explore_city
        worlds = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        game = worlds.get_game_world()
        if game:
            # UnrealEditorSubsystem intentionally has no editor world during PIE.
            import re
            package = game.get_path_name().split('.')[0].rsplit('/', 1)[-1]
            if re.sub(r'^UEDPIE_\d+_', '', package) != city_world.MAP.rsplit('/', 1)[-1]:
                raise ValueError('Open the Beirut city world before navigating.')
        else:
            city_world._current()
        row = _results[index]; data = _data()
        checkpoint = json.loads(city_world._checkpoint().read_text(encoding='utf-8'))
        if data['origin_utm_m'] != checkpoint['origin_utm_m']:
            raise ValueError('The location index uses a different city coordinate origin. Rebuild it first.')
        lat, lon = row['lat'], row['lon']
        if not (32 < lat < 35 and 34 < lon < 37):
            raise ValueError('This location is outside the available Beirut model. Your position was kept.')
        x, y = core.lonlat_to_utm(lon, lat)
        local = [x-data['origin_utm_m'][0], y-data['origin_utm_m'][1]]
        candidates = core.nearest_roads(data, local)
        if not candidates:
            raise ValueError('No modeled road within 200 m of this place. Your position was kept.')
        pawn = unreal.GameplayStatics.get_player_pawn(game, 0) if game else None
        mode = 'editor'
        if game:
            if pawn is None or not hasattr(pawn, 'try_location'):
                raise ValueError('The current player does not support location navigation.')
            markers = {}
            for actor in unreal.GameplayStatics.get_all_actors_of_class(game, unreal.Actor):
                label = actor.get_actor_label()
                for name in ('ORIGIN','EAST','NORTH','UP'):
                    if label.startswith('DYNACITY_BASIS_'+name): markers[name] = actor.get_actor_location()
            if len(markers) != 4: raise ValueError('The driving world is missing coordinate markers.')
            origin = markers['ORIGIN']
            east, north, up = [(markers[name]-origin)/10 for name in ('EAST','NORTH','UP')]
            if any(not 99 < axis.length() < 101 for axis in (east,north,up)):
                raise ValueError('Unexpected world coordinate scale.')
            placed = None
            for candidate in candidates:
                position = list(candidate['position']); position[2] += 1.2
                direction = east*candidate['direction'][0]+north*candidate['direction'][1]
                yaw = unreal.MathLibrary.make_rot_from_x(direction).yaw
                world_position = origin+east*position[0]+north*position[1]+up*position[2]
                if pawn.try_location(world_position, yaw, row['name']):
                    placed = candidate; break
            if placed is None:
                raise ValueError('Nearby roads have no safe loaded vehicle surface. Your position was kept; try the editor view.')
            nearest = placed; mode = 'driving'
        else:
            city_world.load_editor_geometry()
            nearest = candidates[0]
            # Exact requested horizontal coordinates, observed nearby road elevation.
            # High camera clearance avoids placing the viewer inside a building.
            target = explore_city._world([*local, nearest['position'][2]])
            origin, east, north, up = explore_city._basis()
            explore_city.stop_following()
            explore_city._look_at(target-north*90+up*110, target+up*10)
        _last_navigation = {'name': row['name'], 'lat': lat, 'lon': lon, 'mode': mode,
                            'road_id': nearest['road_id'], 'road_distance_m': nearest['distance_m']}
        _status(f"At {row['name']}." + (f" Vehicle placed on a safe road {nearest['distance_m']:.0f} m away. Close this window; Tab resumes driving." if game else
                 ' Camera focused on the selected coordinates.'))
        return _last_navigation
    except Exception as error:
        _status(str(error)); return {'error': str(error)}


def initialize():
    global _panel
    if _panel is None:
        cls = unreal.load_class(None, '/Script/DynaCityDrive.DynaLocationPanel')
        if cls is None: raise RuntimeError('Build the updated DynaCityDrive plugin and restart Unreal.')
        _panel = unreal.new_object(cls)
        _panel.set_status('Search ready.')
    _panel.query_requested.clear()
    _panel.result_selected.clear()
    _panel.query_requested.add_callable(search)
    _panel.result_selected.add_callable(go_to)
    return _panel


def open_search():
    try:
        import control_panel
        control_panel.open_panel('search')
    except Exception as error: unreal.log_warning('Location search: '+str(error))


def status():
    return {'places': len(_data()['places']), 'results': _results, 'last_navigation': _last_navigation}
