"""Open-Meteo weather for the Unreal sky: request, cache, fallback and the weather -> sky-parameter mapping (no Unreal).

Open-Meteo is free and needs no key.  Forecast: https://api.open-meteo.com/v1/forecast ; past hours:
https://archive-api.open-meteo.com/v1/archive (ERA5 reanalysis).  Values are MODEL weather on a grid of roughly
10 km (forecast) to 25 km (archive); they say nothing about a single Beirut street.  The feature is OFF by default:
nothing here runs unless the project config enables it, and every use records the attribution string.

The mapping to sky parameters is illustrative and uncalibrated against any photograph.  It only drives the stock
volumetric-cloud material parameters the project already sets (``Cloud_GlobalCoverage``, ``Cloud_GlobalDensity``),
the cloud layer altitude/thickness and, for haze, an exponential-height-fog density.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Beirut city centre (Martyrs' Square area).  Open-Meteo snaps to its own grid and reports the grid point it used.
BEIRUT_LAT, BEIRUT_LON = 33.8938, 35.5018
FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'
ARCHIVE_URL = 'https://archive-api.open-meteo.com/v1/archive'
HOURLY = ('cloud_cover', 'cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high', 'precipitation',
          'wind_speed_10m', 'wind_direction_10m', 'visibility', 'relative_humidity_2m')
FORECAST_TTL_S = 30 * 60
ARCHIVE_TTL_S = 30 * 24 * 3600
TIMEOUT_S = 12

# The illustrative presets the project already uses (ambient_effects.configure_cloud_layer).
ILLUSTRATIVE_PRESET = {'cloud_global_coverage': -0.12, 'cloud_global_density': 0.012, 'layer_bottom_km': 2.0,
                       'layer_height_km': 1.8, 'fog_density': None, 'source': 'illustrative preset'}


def hour_floor(when: datetime) -> datetime:
    when = when.astimezone(timezone.utc)
    return when.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def parse_when(text: str | None, now: datetime | None = None) -> datetime:
    """UTC hour from an ISO string ('2026-11-10T14:00' or with a Z/offset) or the current hour when empty."""
    if not text:
        return hour_floor(now or datetime.now(timezone.utc))
    dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return hour_floor(dt)


def build_url(mode: str, when: datetime, lat: float = BEIRUT_LAT, lon: float = BEIRUT_LON) -> str:
    """Request URL for one UTC hour: 'forecast' (now or the next days) or 'archive' (a past hour)."""
    if mode not in ('forecast', 'archive'):
        raise ValueError(mode)
    day = hour_floor(when).strftime('%Y-%m-%d')
    q = {'latitude': '%.4f' % lat, 'longitude': '%.4f' % lon, 'hourly': ','.join(HOURLY), 'timezone': 'UTC', 'wind_speed_unit': 'ms'}
    if mode == 'forecast':
        q['start_date'] = day
        q['end_date'] = day
        return FORECAST_URL + '?' + urllib.parse.urlencode(q, safe=',')
    q['start_date'] = day
    q['end_date'] = day
    return ARCHIVE_URL + '?' + urllib.parse.urlencode(q, safe=',')


def select_hour(payload: dict, when: datetime) -> dict:
    """Values of the requested UTC hour from an Open-Meteo hourly response (KeyError if the hour is absent)."""
    hourly = payload['hourly']
    stamp = hour_floor(when).strftime('%Y-%m-%dT%H:00')
    i = hourly['time'].index(stamp)
    values = {name: hourly[name][i] for name in HOURLY if name in hourly}
    values['time_utc'] = stamp
    values['grid_latitude'] = payload.get('latitude')
    values['grid_longitude'] = payload.get('longitude')
    values['units'] = payload.get('hourly_units', {})
    return values


def attribution(values: dict) -> str:
    """The text shown/recorded wherever the weather is applied."""
    t = values.get('time_utc', '?').replace('T', ' ')
    return 'Open-Meteo, model weather at ~km scale, %s UTC' % t


def load_cache(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_cache(path, cache: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, indent=1), encoding='utf-8')


def http_get(url: str, timeout: float = TIMEOUT_S, proxy: str | None = None) -> tuple[dict, float]:
    """GET one JSON document. ``proxy`` (test aid) routes the request through that proxy, e.g. a closed local port."""
    started = time.monotonic()
    request = urllib.request.Request(url, headers={'User-Agent': 'dynacity-unreal-weather/1 (open-meteo free API client)'})
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
        response = opener.open(request, timeout=timeout)
    else:
        response = urllib.request.urlopen(request, timeout=timeout)
    with response:
        payload = json.loads(response.read().decode('utf-8'))
    return payload, time.monotonic() - started


def fetch_weather(mode: str, when: datetime, cache_path, now: datetime | None = None, getter=http_get, use_cache: bool = True) -> dict:
    """Weather for one UTC hour: fresh cache, else network, else the illustrative preset (never raises on network errors).

    The result always names its ``source`` ('open-meteo', 'open-meteo (cache)' or 'illustrative preset (fallback)'),
    the request URL, when it was fetched and, for the live case, the response time.
    """
    now = now or datetime.now(timezone.utc)
    url = build_url(mode, when)
    ttl = ARCHIVE_TTL_S if mode == 'archive' else FORECAST_TTL_S
    cache = load_cache(cache_path)
    entry = cache.get(url)
    if use_cache and entry:
        age = (now - datetime.fromisoformat(entry['fetched_at_utc'])).total_seconds()
        if 0 <= age <= ttl:
            values = select_hour(entry['payload'], when)
            return {'source': 'open-meteo (cache)', 'url': url, 'fetched_at_utc': entry['fetched_at_utc'], 'cache_age_s': round(age, 1),
                    'response_s': entry.get('response_s'), 'values': values, 'attribution': attribution(values), 'error': None}
    try:
        payload, seconds = getter(url)
        values = select_hour(payload, when)
    except Exception as error:  # network down, HTTP error, malformed body, missing hour: all fall back the same way
        values = {'time_utc': hour_floor(when).strftime('%Y-%m-%dT%H:00')}
        return {'source': 'illustrative preset (fallback)', 'url': url, 'fetched_at_utc': None, 'response_s': None, 'values': None,
                'attribution': 'Illustrative weather preset (Open-Meteo unavailable: %s)' % type(error).__name__, 'error': repr(error)[:300],
                'fallback_for_hour_utc': values['time_utc']}
    fetched = now.astimezone(timezone.utc).isoformat()
    cache[url] = {'fetched_at_utc': fetched, 'response_s': round(seconds, 3), 'payload': payload}
    save_cache(cache_path, cache)
    return {'source': 'open-meteo', 'url': url, 'fetched_at_utc': fetched, 'response_s': round(seconds, 3), 'values': values,
            'attribution': attribution(values), 'error': None}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def map_to_sky(values: dict | None) -> dict:
    """Illustrative mapping from one hour of Open-Meteo values to the stock cloud/haze parameters.

    cloud_cover (%)   -> Cloud_GlobalCoverage, linear from -0.45 (clear) through -0.12 (the project's fair-weather preset,
                         at about 40 %) to +0.30 (overcast); high cloud alone counts for half.
    precipitation     -> Cloud_GlobalDensity up from 0.012 (0 mm/h) to 0.030 (>= 3 mm/h): wetter air, darker/denser deck.
    low + mid cloud   -> layer thickness 1.8 km up to 3.0 km; the base lowers from 2.0 km to 1.2 km with low cloud.
    visibility (m)    -> exponential-height-fog density = 0.1 / visibility_km (clamped 0.0015 .. 0.05; 5 km -> 0.02, 40 km ->
                         0.0025).  None when the source has no visibility (the archive API returns null).
    wind              -> recorded and NOT mapped: the stock cloud material has no wind parameter the project uses.
    """
    if not values:
        return dict(ILLUSTRATIVE_PRESET)
    cover = values.get('cloud_cover')
    if cover is None:
        return dict(ILLUSTRATIVE_PRESET)
    high_only = (values.get('cloud_cover_low') or 0) + (values.get('cloud_cover_mid') or 0) == 0
    effective = cover * (0.5 if high_only else 1.0)
    coverage = -0.45 + (effective / 100.0) * 0.75
    precip = values.get('precipitation') or 0.0
    density = 0.012 + _clamp(precip / 3.0, 0.0, 1.0) * (0.030 - 0.012)
    low = (values.get('cloud_cover_low') or 0) / 100.0
    mid = (values.get('cloud_cover_mid') or 0) / 100.0
    height = 1.8 + 1.2 * _clamp(low + 0.5 * mid, 0.0, 1.0)
    bottom = 2.0 - 0.8 * _clamp(low, 0.0, 1.0)
    vis = values.get('visibility')
    fog = None
    if vis:
        # Extinction is proportional to 1 / visibility (Koschmieder); the constant 0.1 was set by eye in the editor so that
        # 5 km reads as clear haze (density 0.02) and 40 km as almost none (0.0025).  Clamped to 0.0015 .. 0.05.
        fog = round(_clamp(0.1 / (float(vis) / 1000.0), 0.0015, 0.05), 5)
    return {'cloud_global_coverage': round(coverage, 4), 'cloud_global_density': round(density, 5), 'layer_bottom_km': round(bottom, 3),
            'layer_height_km': round(height, 3), 'fog_density': fog, 'source': 'open-meteo mapping',
            'inputs_used': {k: values.get(k) for k in ('cloud_cover', 'cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high', 'precipitation', 'visibility')},
            'wind_recorded_not_mapped': {'speed_ms': values.get('wind_speed_10m'), 'direction_deg': values.get('wind_direction_10m')}}


def config_enabled(config_path) -> bool:
    """True only when the config file says so; a missing or unreadable file means OFF."""
    p = Path(config_path)
    try:
        return bool(json.loads(p.read_text(encoding='utf-8')).get('enabled', False))
    except (OSError, ValueError, AttributeError):
        return False


def write_config(config_path, enabled: bool) -> dict:
    p = Path(config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {'enabled': bool(enabled), 'updated_utc': datetime.now(timezone.utc).isoformat(),
            'note': 'Open-Meteo weather for the Unreal sky. Off by default; model weather at ~km scale, not street-level.'}
    p.write_text(json.dumps(data, indent=1), encoding='utf-8')
    return data
