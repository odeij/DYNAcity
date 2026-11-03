"""Verify the installed geographic minimap through Unreal's local MCP server.

Checks live tile rendering, projection consistency, modes, heading and movement.
This does not establish survey accuracy or a before/after FPS improvement.
"""
import argparse
import base64
import json
import math
from pathlib import Path
import sys
import time

from verify_unreal_mobility import invoke

DRIVE = 'driving_tools.DrivingTools'
MOBILITY = 'mobility_tools.MobilityTools'


def state():
    driving = invoke(DRIVE, 'status').get('driving') or {}
    return driving, json.loads(driving.get('mobility', '{}')).get('minimap', {})


def wait_for(predicate, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        driving, minimap = state()
        if driving and minimap and predicate(driving, minimap):
            return driving, minimap
        time.sleep(.5)
    raise RuntimeError('Native minimap did not settle: ' + json.dumps(minimap))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--use-current', action='store_true', help='Only for an agent-owned verification Play session')
    parser.add_argument('--leave-playing', action='store_true', help='Leave the verified car stationary with its original saved map settings')
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    checks, samples = {}, {}
    owns_play, error = False, None

    def capture(name):
        image = invoke('EditorToolset.EditorAppToolset', 'CaptureEditorImage')
        (out / (name + '.png')).write_bytes(base64.b64decode(image['data']))

    try:
        playing = invoke(DRIVE, 'status').get('playing')
        if playing and not args.use_current:
            raise RuntimeError('A Play session already exists; refusing to interrupt it.')
        if not playing:
            invoke(DRIVE, 'play')
        owns_play = True
        original_settings = invoke(MOBILITY, 'status')['controls']['map_settings']
        drive, first = wait_for(lambda d, m: d['surface_ready'] and m['georeferenced'] and m['initialized'])
        checks['source_surface'] = drive['surface_ready']
        checks['explicit_utm36n'] = first['crs'] == 'EPSG:32636' and first['crs_declared']
        for mode in ('street', 'satellite', 'hybrid'):
            confirmed = invoke(MOBILITY, 'map_settings', mode=mode, heading_up=True)
            checks[mode + '_panel_confirmed'] = confirmed['map_settings']['mode'] == mode
            drive, minimap = wait_for(lambda d, m: m['mode'] == mode and m['visible_tiles'] > 0 and
                                     m['missing_tiles'] == 0 and m['drawn_tiles'] == m['visible_tiles'] and m['pending'] == 0)
            samples[mode] = minimap
            checks[mode + '_tiles_rendered'] = minimap['visible_errors'] == 0
            checks[mode + '_bounded_cache'] = minimap['tile_entries'] <= minimap['memory_tile_limit']
            checks[mode + '_bounded_requests'] = minimap['pending'] <= minimap['max_concurrent_loads'] == 4
            checks[mode + '_preferences_saved'] = not minimap['preferences_error']
            if mode != 'street':
                checks[mode + '_no_imagery_overzoom'] = (not minimap['overzoom'] and
                                                       minimap['imagery_meters_per_pixel'] >= 10)
            capture('native-' + mode)

        # Independently project the actual local player coordinate through Python.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'engine/unreal/Content/Python'))
        from location_core import utm_to_lonlat
        origin, local = minimap['origin_utm_m'], minimap['player_local_m']
        lon, lat = utm_to_lonlat(origin[0] + local[0], origin[1] + local[1])
        expected = [6378137 * math.radians(lon), 6378137 * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))]
        samples['stationary_projection_error_m'] = math.dist(expected, minimap['center']['position_m'])
        checks['shared_projection_alignment'] = samples['stationary_projection_error_m'] < .02

        invoke(MOBILITY, 'minimap', mode='hybrid', heading_up=False)
        drive, north = wait_for(lambda d, m: not m['heading_up'] and abs(math.remainder(m['map_rotation_deg'], 360)) < .5)
        checks['north_up'] = True
        samples['north_up'] = north
        capture('native-north-up')
        invoke(MOBILITY, 'minimap', mode='hybrid', heading_up=True)
        drive, heading = wait_for(lambda d, m: m['heading_up'] and
                                 abs(math.remainder(m['map_rotation_deg'] - m['actual_heading_deg'], 360)) < .5)
        checks['heading_up'] = True

        invoke(MOBILITY, 'map_settings', mode='street', heading_up=True)
        wait_for(lambda d, m: m['mode'] == 'street' and m['half_range_m'] < 181)
        before, previous = state()
        try:
            invoke(DRIVE, 'input', throttle=.65, steering=.1, brake=False)
            moved, moving = wait_for(lambda d, m: abs(d['speed_kmh']) > 8 and
                                     math.dist(m['player_local_m'], previous['player_local_m']) > 2, seconds=15)
            checks['tracks_moving_player'] = math.dist(moving['center']['position_m'], previous['center']['position_m']) > 1
            checks['speed_dependent_zoom'] = moving['half_range_m'] > previous['half_range_m'] + 1
            checks['moving_requests_bounded'] = moving['pending'] <= moving['max_concurrent_loads']
            samples['moving'] = {'drive': moved, 'minimap': moving}
        finally:
            invoke(DRIVE, 'reset')
            invoke(DRIVE, 'release')
        drive, reset = wait_for(lambda d, m: d['surface_ready'] and abs(d['speed_kmh']) < .1 and
                               math.dist(m['player_local_m'], previous['player_local_m']) < 2)
        checks['reset_tracks_player'] = True
        samples['reset'] = reset

        # Exercise the actual settings action while stopped, then spawn a new HUD.
        invoke(DRIVE, 'stop')
        deadline = time.monotonic() + 15
        while invoke(DRIVE, 'status').get('playing') and time.monotonic() < deadline:
            time.sleep(.5)
        if invoke(DRIVE, 'status').get('playing'):
            raise RuntimeError('Verification Play session did not stop.')
        saved = invoke(MOBILITY, 'map_settings', mode='hybrid', heading_up=False)['map_settings']
        checks['settings_saved_outside_play'] = saved == {'version': 1, 'mode': 'hybrid', 'heading_up': False}
        invoke(DRIVE, 'play')
        drive, reopened = wait_for(lambda d, m: m['initialized'] and m['mode'] == 'hybrid' and not m['heading_up'])
        checks['settings_survive_new_play_session'] = reopened['preferences_loaded'] and not reopened['preferences_error']
        samples['reopened'] = reopened
        invoke(MOBILITY, 'map_settings', mode=original_settings['mode'], heading_up=original_settings['heading_up'])
        wait_for(lambda d, m: m['mode'] == original_settings['mode'] and m['heading_up'] == original_settings['heading_up'])
        if not all(checks.values()):
            raise RuntimeError('A native geographic minimap check failed.')
    except Exception as exception:
        error = str(exception)
    finally:
        if owns_play:
            try:
                invoke(DRIVE, 'release')
                if not args.leave_playing or error:
                    invoke(DRIVE, 'stop')
            except Exception as cleanup_error:
                error = error or 'Play cleanup failed: ' + str(cleanup_error)
        report = {'passed': bool(checks) and all(checks.values()) and error is None,
                  'checks': checks, 'samples': samples, 'error': error,
                  'limits': ['Editor-hosted Unreal MCP verification, not a packaged game.',
                             'Projection consistency is not surveyed geographic accuracy.',
                             'No before/after FPS claim; background editor throttling may apply.',
                             'Satellite is historical 2016 imagery at approximately 10 m/pixel.']}
        (out / 'native-minimap-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'passed': report['passed'], 'checks': checks, 'error': error}))
    if error:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
