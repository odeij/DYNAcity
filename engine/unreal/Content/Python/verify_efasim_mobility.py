"""Nonblocking verification of one owned PIE traffic session.

Run on the game thread: import verify_efasim_mobility as v; v.start()
The callback samples actual native instance transforms while the ordinary
runtime receives SUMO batches. It restores the traffic/Play state it owns.
"""
import importlib
import json
from pathlib import Path
import time

import unreal

_run = None


def surface_probe(output='C:/private/dynacity-data/engine/street-twin-20261029/mobility/surface-probe.json'):
    """Read the same four wheel supports as the native guard in the PIE world."""
    import math
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    if world is None:
        raise RuntimeError('Probe needs the current root-owned PIE world.')
    pawn = unreal.GameplayStatics.get_player_pawn(world, 0)
    rows = []
    cls = unreal.load_class(None, '/Script/DynaCityDrive.DynaDriveStart')
    starts = unreal.GameplayStatics.get_all_actors_of_class(world, cls)
    targets = [('current pawn', pawn)] + [(str(start.get_editor_property('area')), start) for start in starts]
    for name, actor in targets:
        centre = actor.get_actor_location()
        yaw = math.radians(actor.get_actor_rotation().yaw)
        forward = unreal.Vector(math.cos(yaw), math.sin(yaw), 0)
        right = unreal.Vector(-math.sin(yaw), math.cos(yaw), 0)
        hits = []
        for x in (-135, 135):
            for y in (-70, 70):
                probe = centre + forward*x + right*y
                hit = unreal.SystemLibrary.line_trace_single(world, probe+unreal.Vector(0,0,300),
                    probe-unreal.Vector(0,0,900), unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
                    False, [pawn], unreal.DrawDebugTrace.NONE, True)
                if hit is None:
                    hits.append({'missing': True})
                    continue
                values = hit.to_dict()
                point, normal = values['impact_point'], values['impact_normal']
                support = values['hit_actor']
                hits.append({'z_cm':point.z, 'normal_z':normal.z,
                             'actor':support.get_actor_label() if support else None,
                             'source_elevation_error_cm': point.z-(centre.z-120)})
        rows.append({'target':name, 'position_cm':[centre.x,centre.y,centre.z], 'hits':hits})
    report = {'pawn':json.loads(pawn.get_drive_status()), 'wheel_probes':rows}
    Path(output).write_text(json.dumps(report, indent=2))
    return {'output':output, 'starts':len(starts)}


def start(output='C:/private/dynacity-data/engine/street-twin-20261029/mobility/native-verification.json', use_current=False):
    global _run
    import setup_driving, mobility_runtime
    if _run is not None:
        raise RuntimeError('A mobility verification is already active.')
    playing = setup_driving.status().get('playing', False)
    if playing and not use_current:
        raise RuntimeError('Play already exists; use_current is only for a root-owned verification session.')
    if mobility_runtime.status()['enabled']:
        raise RuntimeError('Traffic is already enabled; leave the existing session undisturbed.')
    if not playing:
        # Pick up source edits only before creating the verification game world.
        import traffic_client, traffic_evidence
        importlib.reload(traffic_evidence)
        importlib.reload(traffic_client)
        importlib.reload(mobility_runtime)
        setup_driving.play()
    _run = {'output': Path(output), 'started': time.monotonic(), 'last_sample': 0,
            'owns_play': not playing, 'phase': 'surface', 'samples': [], 'checks': {},
            'previous_instances': {}, 'instance_changes': 0, 'moving_pairs': 0,
            'last_batch': 0, 'error': None, 'cleanup_started': None}
    _run['output'].parent.mkdir(parents=True, exist_ok=True)
    _run['callback'] = unreal.register_slate_post_tick_callback(_tick)
    _write('running')
    return {'started': True, 'output': str(_run['output'])}


def _instances(renderer):
    # Enumerate real native components; additional fleet groups are private in
    # some compiled plugin revisions and are not exposed as Python properties.
    components = renderer.get_components_by_class(unreal.InstancedStaticMeshComponent)
    groups, positions = [], {}
    for group, component in enumerate(components):
        count = component.get_instance_count()
        mesh = component.get_editor_property('static_mesh')
        groups.append({'mesh': mesh.get_path_name() if mesh else None, 'instances': count})
        for index in range(min(count, 8)):
            transform = component.get_instance_transform(index, world_space=True)
            point = transform.translation
            positions[(group,index)] = [point.x, point.y, point.z]
    return groups, positions


def _write(status):
    data = {key: value for key, value in _run.items()
            if key not in ('output','callback','previous_instances','started','last_sample','cleanup_started')}
    data['status'] = status
    data['passed'] = status == 'pass'
    data['limits'] = ['Editor PIE integration; no packaged build or GPU FPS measurement.',
                      'Synthetic SUMO demand; vehicle mix and paint are illustrative proxies.',
                      'Traffic instances do not collide or interact with the player vehicle.',
                      'Changed instance slots alone do not establish individual identity; authoritative motion is checked separately.']
    _run['output'].write_text(json.dumps(data, indent=2))


def _cleanup(error=None):
    import mobility_runtime
    if error is not None:
        _run['error'] = str(error)
    mobility_runtime.stop()
    _run['phase'] = 'cleanup'
    _run['cleanup_started'] = time.monotonic()
    _write('cleaning_up')


def _tick(delta_seconds):
    global _run
    import setup_driving, mobility_runtime
    now = time.monotonic()
    if _run is None or now-_run['last_sample'] < .5:
        return
    _run['last_sample'] = now
    try:
        if _run['phase'] == 'cleanup':
            state = mobility_runtime.status()
            _run['checks']['disconnect_clears_instances'] = state['render'].get('visible_vehicles',0) == 0
            if mobility_runtime._client is not None or mobility_runtime._future is not None:
                if now-_run['cleanup_started'] < 55:
                    return
                _run['error'] = _run['error'] or 'Owned backend session did not finish closing.'
            _run['checks']['owned_backend_session_closed'] = mobility_runtime._client is None and mobility_runtime._future is None
            if _run['owns_play']:
                unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_end_play()
            _write('pass' if not _run['error'] and all(_run['checks'].values()) else 'failed')
            unreal.unregister_slate_post_tick_callback(_run['callback'])
            _run = None
            return
        if now-_run['started'] > 180:
            raise RuntimeError('Traffic verification exceeded its 180 second bounded deadline.')
        driving = setup_driving.status().get('driving') or {}
        state = mobility_runtime.status()
        if _run['phase'] == 'surface':
            _run['last_surface_state'] = {
                key:driving.get(key) for key in ('surface_ready','surface_status','blocked_reason')}
            _write('running')
            if not driving.get('surface_ready') or mobility_runtime._renderer is None:
                return
            _run['checks']['source_surface_ready'] = True
            mobility_runtime.start()
            _run['phase'] = 'traffic'
            return
        if not state['enabled']:
            raise RuntimeError(state['message'])
        groups, positions = _instances(mobility_runtime._renderer)
        previous = _run['previous_instances']
        if any(sum((point[i]-previous[key][i])**2 for i in range(3)) > 1
               for key,point in positions.items() if key in previous):
            _run['instance_changes'] += 1
        _run['previous_instances'] = positions
        if state['batches_received'] != _run['last_batch']:
            _run['moving_pairs'] += state.get('last_batch',{}).get('moving_trajectory_pairs',0)
            _run['last_batch'] = state['batches_received']
        sample = {'elapsed_s': now-_run['started'], 'mobility': state, 'instance_groups': groups,
                  'actual_instances': sum(group['instances'] for group in groups)}
        _run['samples'].append(sample)
        visible = [sample for sample in _run['samples'] if sample['actual_instances'] > 0]
        first = visible[0]['mobility']['render']['time_s'] if visible else None
        checks = _run['checks']
        checks.update(sustained_batches=state['batches_received'] >= 5,
                      actual_native_instances=len(visible)>=3,
                      instance_transforms_change=_run['instance_changes']>=2,
                      authoritative_trajectories_move=_run['moving_pairs']>0,
                      bounded_instances=bool(visible) and all(0<s['actual_instances']<=150 for s in visible),
                      simulation_clock_advances=first is not None and state['render']['time_s']-first>=8)
        _write('running')
        if all(checks.values()):
            _cleanup()
    except Exception as error:
        _cleanup(error)
