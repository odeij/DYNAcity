"""Editor/Play adapter that drives the DynaAmbientSky cloud and haze settings from Open-Meteo (default OFF).

Runs through ``open_data_tools.OpenDataTools.weather``.  Nothing here changes the sky unless
``Saved/DynaCity/weather-config.json`` says ``{"enabled": true}`` (use the ``set_enabled`` action).  With weather off the
project keeps its illustrative presets exactly as before.  When Open-Meteo cannot be reached the adapter falls back to
those presets without raising.  Every application records ``Open-Meteo, model weather at ~km scale, <time>`` in
``Saved/DynaCity/weather-applied.json`` and on the preview actor's label.

In the editor the effect is a TRANSIENT preview: a ``DynaAmbientSky`` actor (its volumetric-cloud component) plus an
exponential-height-fog actor, never saved; ``clear_preview`` removes them.  In Play, ``ambient_effects.configure_cloud_layer``
calls ``play_parameters`` and applies the same mapping to the driven pawn's sky.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import unreal

import importlib

import city_world
import open_meteo_core as core

importlib.reload(core)  # the dispatcher reloads this module on every call; keep the pure core in step with it

STOCK = '/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst'
SKY_CLASS = '/Script/DynaCityDrive.DynaAmbientSky'
TAG = 'DYNACITY_WEATHER_PREVIEW'
LABEL = 'Weather preview (not saved)'


def _dir():
    return city_world._root() / 'Saved' / 'DynaCity'


def _paths():
    d = _dir()
    return d / 'weather-config.json', d / 'open-meteo-cache.json', d / 'weather-applied.json'


def status():
    config, cache, applied = _paths()
    entries = core.load_cache(cache)
    return {'enabled': core.config_enabled(config), 'config_path': str(config), 'cache_entries': {u: {'fetched_at_utc': e['fetched_at_utc'], 'response_s': e.get('response_s')} for u, e in entries.items()},
            'preview_actors': [a.get_actor_label() for a in city_world._actors() if TAG in [str(t) for t in a.tags]],
            'last_applied': json.loads(applied.read_text()) if applied.exists() else None}


def set_enabled(enabled):
    config, _, _ = _paths()
    return core.write_config(config, enabled)


def _closed_port_getter(url):
    """Test aid: the real urllib request path, sent through a proxy on a closed local port (a genuine connection failure)."""
    return core.http_get(url, timeout=4, proxy='http://127.0.0.1:9')


def _fetch(mode, when, use_cache, simulate_unreachable):
    _, cache, _ = _paths()
    if simulate_unreachable:
        return core.fetch_weather(mode, core.parse_when(when), cache, use_cache=False, getter=_closed_port_getter)
    return core.fetch_weather(mode, core.parse_when(when), cache, use_cache=use_cache)


def fetch(mode='forecast', when=None, use_cache=True, simulate_unreachable=False):
    result = _fetch(mode, when, use_cache, simulate_unreachable)
    result['mapped'] = core.map_to_sky(result['values'])
    result['simulated_unreachable'] = bool(simulate_unreachable)
    return result


def _destroy_preview():
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    n = 0
    for a in city_world._actors():
        if TAG in [str(t) for t in a.tags]:
            sub.destroy_actor(a)
            n += 1
    return n


def _spawn_preview(params, attribution_text):
    """Transient cloud + fog actors with the mapped parameters. Returns what was set."""
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play first; in Play the pawn applies the weather itself')
    _destroy_preview()
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    cls = unreal.load_class(None, SKY_CLASS)
    sky = sub.spawn_actor_from_class(cls, unreal.Vector(0, 0, 0))
    sky.set_actor_label('%s - %s' % (LABEL, attribution_text))
    sky.tags = [TAG, 'NOT_SAVED', 'OPEN_METEO_WEATHER']
    sky.set_folder_path('DynaCity/Weather preview')
    volume = sky.get_editor_property('volumetric')
    volume.set_absolute(False, False, True)
    volume.set_world_scale3d(unreal.Vector(1, 1, 1))
    volume.set_layer_bottom_altitude(float(params['layer_bottom_km']))
    volume.set_layer_height(float(params['layer_height_km']))
    volume.set_view_sample_count_scale(1.5)
    volume.set_sky_light_cloud_bottom_occlusion(.25)
    stock = unreal.load_asset(STOCK)
    material = unreal.MaterialLibrary.create_dynamic_material_instance(sky, stock)
    material.set_scalar_parameter_value('Cloud_GlobalCoverage', float(params['cloud_global_coverage']))
    material.set_scalar_parameter_value('Cloud_GlobalDensity', float(params['cloud_global_density']))
    volume.set_material(material)
    root = sky.get_editor_property('clouds')
    if root:
        root.set_visibility(False)                      # the cheap dome stays off; volumetric clouds only
    out = {'sky_actor': sky.get_path_name(), 'applied': {k: params[k] for k in ('cloud_global_coverage', 'cloud_global_density', 'layer_bottom_km', 'layer_height_km')}}
    fog_density = params.get('fog_density')
    if fog_density:
        fog = sub.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0))
        fog.set_actor_label('%s - haze - %s' % (LABEL, attribution_text))
        fog.tags = [TAG, 'NOT_SAVED', 'OPEN_METEO_WEATHER']
        fog.set_folder_path('DynaCity/Weather preview')
        comp = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
        comp.set_fog_density(float(fog_density))
        comp.set_fog_height_falloff(0.2)
        comp.set_start_distance(0.0)
        comp.set_volumetric_fog(False)
        out['fog_actor'] = fog.get_path_name()
        out['applied']['fog_density'] = fog_density
    return out


def apply_preview(mode='forecast', when=None, use_cache=True, simulate_unreachable=False):
    """Fetch (or reuse the cache), map and show the weather as a transient preview. Refuses while weather is disabled."""
    config, cache, applied = _paths()
    if not core.config_enabled(config):
        return {'applied': False, 'reason': 'weather is OFF (default); run set_enabled first', 'sky_changed': False}
    result = _fetch(mode, when, use_cache, simulate_unreachable)
    params = core.map_to_sky(result['values'])
    note = result['attribution']
    placed = _spawn_preview(params, note)
    record = {'applied_at_utc': datetime.now(timezone.utc).isoformat(), 'where': 'editor transient preview (not saved)', 'attribution': note,
              'simulated_unreachable': bool(simulate_unreachable),
              'source': result['source'], 'url': result['url'], 'fetched_at_utc': result['fetched_at_utc'], 'response_s': result['response_s'],
              'error': result['error'], 'values': result['values'], 'mapped': params, 'placed': placed}
    applied.write_text(json.dumps(record, indent=1), encoding='utf-8')
    return record


def clear_preview():
    removed = _destroy_preview()
    return {'removed_actors': removed, 'remaining': status()['preview_actors']}


def play_parameters(mode='forecast', when=None):
    """Cloud/haze parameters for the Play world: the weather mapping when enabled, else the illustrative preset unchanged."""
    config, cache, applied = _paths()
    if not core.config_enabled(config):
        return dict(core.ILLUSTRATIVE_PRESET, attribution='Illustrative weather preset')
    result = core.fetch_weather(mode, core.parse_when(when), cache)
    params = core.map_to_sky(result['values'])
    params['attribution'] = result['attribution']
    applied.write_text(json.dumps({'applied_at_utc': datetime.now(timezone.utc).isoformat(), 'where': 'Play world sky', 'attribution': result['attribution'],
                                   'source': result['source'], 'url': result['url'], 'fetched_at_utc': result['fetched_at_utc'], 'values': result['values'], 'mapped': params}, indent=1),
                       encoding='utf-8')
    return params


def calibrate_preview(cloud_global_coverage, cloud_global_density, layer_bottom_km=2.0, layer_height_km=1.8, fog_density=None):
    """Development aid: show explicit values as the preview (no network, no config check). Used to calibrate the mapping."""
    params = {'cloud_global_coverage': cloud_global_coverage, 'cloud_global_density': cloud_global_density,
              'layer_bottom_km': layer_bottom_km, 'layer_height_km': layer_height_km, 'fog_density': fog_density}
    return _spawn_preview(params, 'calibration values, not weather')


ACTIONS = {'status': status, 'set_enabled': set_enabled, 'fetch': fetch, 'apply_preview': apply_preview, 'clear_preview': clear_preview,
           'play_parameters': play_parameters, 'calibrate_preview': calibrate_preview}
