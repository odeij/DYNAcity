"""Open-Meteo request, cache, fallback and sky mapping (synthetic responses; no network)."""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / 'engine' / 'unreal' / 'Content' / 'Python' / 'open_meteo_core.py'
_spec = importlib.util.spec_from_file_location('open_meteo_core_under_test', _PATH)
core = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = core
_spec.loader.exec_module(core)

NOW = datetime(2026, 11, 21, 6, 20, tzinfo=timezone.utc)


def payload(day='2026-11-21', cloud=60, low=10, mid=20, high=55, precip=0.0, visibility=30000.0):
    times = ['%sT%02d:00' % (day, h) for h in range(24)]
    return {'latitude': 33.88798, 'longitude': 35.485966, 'hourly_units': {'cloud_cover': '%', 'visibility': 'm'},
            'hourly': {'time': times, 'cloud_cover': [cloud] * 24, 'cloud_cover_low': [low] * 24, 'cloud_cover_mid': [mid] * 24,
                       'cloud_cover_high': [high] * 24, 'precipitation': [precip] * 24, 'wind_speed_10m': [3.5] * 24,
                       'wind_direction_10m': [200] * 24, 'visibility': [visibility] * 24, 'relative_humidity_2m': [60] * 24}}


def test_forecast_and_archive_urls_target_the_documented_endpoints_with_one_utc_day():
    when = core.parse_when('2026-11-10T14:35:00Z')
    assert when == datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)
    forecast = core.build_url('forecast', when)
    archive = core.build_url('archive', when)
    assert forecast.startswith('https://api.open-meteo.com/v1/forecast?')
    assert archive.startswith('https://archive-api.open-meteo.com/v1/archive?')
    for url in (forecast, archive):
        assert 'latitude=33.8938' in url and 'longitude=35.5018' in url
        assert 'start_date=2026-11-10' in url and 'end_date=2026-11-10' in url
        assert 'cloud_cover' in url and 'precipitation' in url and 'wind_speed_10m' in url and 'visibility' in url
        assert 'timezone=UTC' in url
    with pytest.raises(ValueError):
        core.build_url('nowcast', when)


def test_select_hour_returns_the_requested_utc_hour_and_the_grid_point():
    values = core.select_hour(payload(cloud=42), datetime(2026, 11, 21, 6, 59, tzinfo=timezone.utc))
    assert values['time_utc'] == '2026-11-21T06:00' and values['cloud_cover'] == 42
    assert values['grid_latitude'] == pytest.approx(33.88798)
    with pytest.raises(ValueError):
        core.select_hour(payload(day='2026-11-20'), NOW)


def test_attribution_names_the_source_scale_and_time():
    text = core.attribution({'time_utc': '2026-11-21T06:00'})
    assert text == 'Open-Meteo, model weather at ~km scale, 2026-11-21 06:00 UTC'


def test_fetch_caches_with_its_timestamp_and_reuses_it_until_the_ttl(tmp_path):
    calls = []

    def getter(url):
        calls.append(url)
        return payload(), 0.25

    cache = tmp_path / 'cache.json'
    first = core.fetch_weather('forecast', NOW, cache, now=NOW, getter=getter)
    assert first['source'] == 'open-meteo' and first['response_s'] == 0.25 and first['error'] is None
    assert first['fetched_at_utc'] == NOW.isoformat()
    stored = json.loads(cache.read_text())
    assert stored[first['url']]['fetched_at_utc'] == NOW.isoformat()
    second = core.fetch_weather('forecast', NOW, cache, now=NOW + timedelta(minutes=10), getter=getter)
    assert second['source'] == 'open-meteo (cache)' and second['cache_age_s'] == 600.0 and len(calls) == 1
    third = core.fetch_weather('forecast', NOW, cache, now=NOW + timedelta(minutes=45), getter=getter)
    assert third['source'] == 'open-meteo' and len(calls) == 2           # forecast cache expires after 30 minutes
    refreshed = core.fetch_weather('forecast', NOW, cache, now=NOW + timedelta(minutes=46), getter=getter, use_cache=False)
    assert refreshed['source'] == 'open-meteo' and len(calls) == 3


def test_archive_cache_is_long_lived(tmp_path):
    calls = []
    cache = tmp_path / 'cache.json'
    when = datetime(2026, 10, 27, 10, tzinfo=timezone.utc)

    def getter(url):
        calls.append(url)
        return payload(day='2026-10-27', cloud=97, low=95, mid=12, precip=1.2, visibility=None), 0.4

    core.fetch_weather('archive', when, cache, now=NOW, getter=getter)
    again = core.fetch_weather('archive', when, cache, now=NOW + timedelta(days=5), getter=getter)
    assert again['source'] == 'open-meteo (cache)' and len(calls) == 1 and again['values']['visibility'] is None


def test_network_failure_falls_back_to_the_illustrative_preset_without_raising(tmp_path):
    def broken(url):
        raise OSError('network unreachable')

    result = core.fetch_weather('forecast', NOW, tmp_path / 'cache.json', now=NOW, getter=broken)
    assert result['source'] == 'illustrative preset (fallback)' and result['values'] is None
    assert 'Open-Meteo unavailable' in result['attribution'] and 'OSError' in result['attribution']
    assert not (tmp_path / 'cache.json').exists()                          # nothing cached from a failure
    assert core.map_to_sky(result['values']) == core.ILLUSTRATIVE_PRESET


def test_real_urllib_failure_through_a_closed_local_port_falls_back(tmp_path):
    getter = lambda url: core.http_get(url, timeout=3, proxy='http://127.0.0.1:9')
    with pytest.raises(Exception):
        getter('https://api.open-meteo.com/v1/forecast')
    result = core.fetch_weather('forecast', NOW, tmp_path / 'cache.json', now=NOW, getter=getter, use_cache=False)
    assert result['source'] == 'illustrative preset (fallback)' and result['error']


def test_malformed_response_and_missing_hour_also_fall_back(tmp_path):
    for body in ({'unexpected': True}, payload(day='2026-11-20')):
        result = core.fetch_weather('forecast', NOW, tmp_path / 'c.json', now=NOW, getter=lambda url, b=body: (b, 0.1))
        assert result['source'] == 'illustrative preset (fallback)'


def test_mapping_is_monotone_in_cloud_cover_and_matches_the_project_preset_near_forty_percent():
    clear = core.map_to_sky({'cloud_cover': 0, 'cloud_cover_low': 0, 'cloud_cover_mid': 0, 'cloud_cover_high': 0})
    fair = core.map_to_sky({'cloud_cover': 40, 'cloud_cover_low': 30, 'cloud_cover_mid': 10, 'cloud_cover_high': 0})
    over = core.map_to_sky({'cloud_cover': 100, 'cloud_cover_low': 100, 'cloud_cover_mid': 50, 'cloud_cover_high': 50})
    assert clear['cloud_global_coverage'] == pytest.approx(-0.45)
    assert fair['cloud_global_coverage'] == pytest.approx(-0.15)            # close to the illustrative -0.12 preset
    assert over['cloud_global_coverage'] == pytest.approx(0.30)
    assert clear['cloud_global_coverage'] < fair['cloud_global_coverage'] < over['cloud_global_coverage']
    assert clear['source'] == 'open-meteo mapping'


def test_high_cloud_alone_counts_for_half():
    high = core.map_to_sky({'cloud_cover': 80, 'cloud_cover_low': 0, 'cloud_cover_mid': 0, 'cloud_cover_high': 80})
    low = core.map_to_sky({'cloud_cover': 80, 'cloud_cover_low': 80, 'cloud_cover_mid': 0, 'cloud_cover_high': 0})
    assert high['cloud_global_coverage'] == pytest.approx(-0.45 + 0.4 * 0.75)
    assert low['cloud_global_coverage'] == pytest.approx(-0.45 + 0.8 * 0.75)


def test_rain_raises_density_and_low_cloud_lowers_and_thickens_the_layer():
    dry = core.map_to_sky({'cloud_cover': 90, 'cloud_cover_low': 0, 'cloud_cover_mid': 90, 'precipitation': 0.0})
    wet = core.map_to_sky({'cloud_cover': 90, 'cloud_cover_low': 95, 'cloud_cover_mid': 10, 'precipitation': 1.2})
    storm = core.map_to_sky({'cloud_cover': 100, 'cloud_cover_low': 100, 'cloud_cover_mid': 100, 'precipitation': 9.0})
    assert dry['cloud_global_density'] == pytest.approx(0.012)
    assert wet['cloud_global_density'] == pytest.approx(0.012 + 0.4 * 0.018)
    assert storm['cloud_global_density'] == pytest.approx(0.030)            # capped at 3 mm/h
    assert wet['layer_bottom_km'] < dry['layer_bottom_km'] and storm['layer_height_km'] == pytest.approx(3.0)


def test_visibility_maps_to_haze_and_missing_visibility_leaves_haze_alone():
    clear = core.map_to_sky({'cloud_cover': 10, 'visibility': 40000.0})
    hazy = core.map_to_sky({'cloud_cover': 10, 'visibility': 5000.0})
    murky = core.map_to_sky({'cloud_cover': 10, 'visibility': 800.0})
    assert clear['fog_density'] == pytest.approx(0.0025) and hazy['fog_density'] == pytest.approx(0.02)
    assert murky['fog_density'] == 0.05                                     # clamped
    assert core.map_to_sky({'cloud_cover': 10, 'visibility': None})['fog_density'] is None


def test_wind_is_recorded_not_mapped_and_missing_cloud_cover_gives_the_preset():
    mapped = core.map_to_sky({'cloud_cover': 20, 'wind_speed_10m': 6.5, 'wind_direction_10m': 230})
    assert mapped['wind_recorded_not_mapped'] == {'speed_ms': 6.5, 'direction_deg': 230}
    assert core.map_to_sky({'visibility': 1000.0}) == core.ILLUSTRATIVE_PRESET
    assert core.map_to_sky(None) == core.ILLUSTRATIVE_PRESET


def test_weather_is_off_unless_the_config_says_so(tmp_path):
    config = tmp_path / 'Saved' / 'weather-config.json'
    assert core.config_enabled(config) is False                             # no file: off
    config.parent.mkdir(parents=True)
    config.write_text('not json')
    assert core.config_enabled(config) is False                             # unreadable: off
    core.write_config(config, True)
    assert core.config_enabled(config) is True
    core.write_config(config, False)
    assert core.config_enabled(config) is False
    assert 'Off by default' in json.loads(config.read_text())['note']
