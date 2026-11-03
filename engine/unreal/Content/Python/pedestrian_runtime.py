"""Show modelled walkers in Play: a pool of animated characters driven by `pedestrian_local` frames.

Walker positions come from UNA flows with Sevtsuk et al. (2024) coefficients and proxy inputs
(modelled, not observed). Characters are configured in `Saved/DynaCity/pedestrian-characters.json`:
  {"characters": [{"blueprint": "/Game/.../BP_X.BP_X_C"} | {"mesh": "/Game/.../SKM", "walk": "/Game/.../Anim"}],
   "walkers": "C:/private/dynacity-data/pedestrian-una/pedestrian-walkers.json", "limit": 80}
Each walker gets one pooled actor; positions are interpolated between 0.1 s frames every tick.
Characters have no collision and do not react to cars or the player.
"""
import json
import math
from pathlib import Path
import unreal

TAG = 'DYNACITY_PEDESTRIAN'
_state = {'tick': None, 'world': None, 'source': None, 'frames': [], 'clock': 0.0, 'pool': {}, 'ground': {}, 'free': [], 'config': None,
          'last_batch': -1e9, 'enabled': False, 'message': 'Pedestrians off'}


def _config():
    path = Path(unreal.Paths.project_saved_dir()) / 'DynaCity' / 'pedestrian-characters.json'
    if not path.is_file():
        raise FileNotFoundError('No pedestrian character configuration at ' + str(path))
    return json.loads(path.read_text(encoding='utf-8'))


POOL_TAG = 'DYNACITY_PEDESTRIAN_POOL'
PARKED = unreal.Vector(0, 0, -500000)   # far below the city while a pooled character is unused


def prepare_pool(count=None):
    """Editor only, before Play: place the character pool that Play copies.

    Python cannot spawn actors into a running Play world here, so the walkers reuse characters placed in
    the editor level beforehand. The pool is not saved by this module; call `remove_pool()` afterwards.
    """
    config = _config()
    count = int(count or config.get('limit', 30))
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    remove_pool()
    for index in range(count):
        choice = config['characters'][index % len(config['characters'])]
        cls = unreal.load_class(None, choice['blueprint']) if 'blueprint' in choice else unreal.SkeletalMeshActor
        actor = sub.spawn_actor_from_class(cls, PARKED)
        if 'blueprint' not in choice:
            component = actor.skeletal_mesh_component
            component.set_skeletal_mesh_asset(unreal.load_asset(choice['mesh']))
            component.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
            component.set_animation(unreal.load_asset(choice['walk']))
        actor.set_actor_label(f'PedestrianPool_{index:02d}')
        actor.set_actor_enable_collision(False)
        actor.set_actor_hidden_in_game(True)
        actor.set_editor_property('is_spatially_loaded', False)
        actor.tags = [TAG, POOL_TAG]
    return count


def remove_pool():
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    pool = [a for a in sub.get_all_level_actors() if POOL_TAG in [str(t) for t in a.tags]]
    for actor in pool:
        sub.destroy_actor(actor)
    return len(pool)


def _spawn(world, index):
    """Take a free pooled character (copied into Play from the editor level), or None if all are in use."""
    if not _state['free']:
        return None
    actor = _state['free'].pop()
    actor.set_actor_hidden_in_game(False)
    _walk(actor, index)
    return actor


LOCO = '/Game/CitySampleCrowd/Character/Anims/Loco/'
# City Sample Crowd walk cycles: an in-place male cycle and forward female cycles per body weight.
WALKS = {'m': LOCO + 'MTN_N_Walk_InPlace', 'f_nrw': LOCO + 'FTN_Set/FTN_N_Walk_F',
         'f_ovw': LOCO + 'FTO_Set/FTO_N_Walk_F', 'f_unw': LOCO + 'FTU_Set/FTU_N_Walk_F'}


def _walk(actor, index):
    """Loop a walk cycle on the character's base skeleton; clothing and body follow it.

    `BP_CrowdCharacter` only animates the face, so a pooled character would glide in a fixed pose.
    Root motion is locked so the walker stays on the path this module sets.
    """
    parts = actor.get_components_by_class(unreal.SkeletalMeshComponent)
    base = next((c for c in parts if c.get_skeletal_mesh_asset() and c.get_skeletal_mesh_asset().get_name().endswith('_base')),
                parts[0] if parts else None)
    if base is None:
        return
    name = base.get_skeletal_mesh_asset().get_name()          # e.g. m_tal_nrw_base, f_tal_ovw_base
    key = 'm' if name.startswith('m_') else 'f_' + (name.split('_')[2] if name.count('_') >= 3 else 'nrw')
    anim = unreal.load_asset(WALKS.get(key, WALKS['m']))
    if anim is None:
        return
    anim.set_editor_property('force_root_lock', True)
    base.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
    base.play_animation(anim, True)
    base.set_play_rate(0.92 + (index % 5) * 0.04)


def _release(actor):
    actor.set_actor_hidden_in_game(True)
    actor.set_actor_location(PARKED, False, True)
    _state['free'].append(actor)


def _local_point(world_basis, position):
    origin, east, north, up = world_basis
    return origin + east * position[0] + north * position[1] + up * position[2]


def start(pawn):
    import mobility_runtime
    from pedestrian_local import LocalPedestrians
    world = pawn.get_world()
    _state.update(config=_config(), world=world, frames=[], clock=0.0, pool={}, ground={}, enabled=True,
                  free=list(unreal.GameplayStatics.get_all_actors_with_tag(world, POOL_TAG)))
    if not _state['free']:
        raise RuntimeError('No pedestrian pool in this Play world: run prepare_pool() in the editor before Play.')
    basis = mobility_runtime._basis_values or mobility_runtime.world_basis()
    _state['basis'] = basis
    origin, *axes = basis
    delta = pawn.get_actor_location() - origin
    centre = [float(delta.dot(a) / a.dot(a)) for a in axes[:2]]
    _state['source'] = LocalPedestrians(_state['config']['walkers'], centre,
                                        limit=int(_state['config'].get('limit', 80)))
    _state['pawn'] = pawn
    _state['message'] = 'Pedestrians: modelled weekday PM peak (UNA, proxy inputs), not observed'
    if _state['tick'] is None:
        _state['tick'] = unreal.register_slate_post_tick_callback(_tick)


def stop():
    for actor in list(_state['pool'].values()):
        try: _release(actor)
        except Exception: pass
    _state.update(pool={}, frames=[], enabled=False, message='Pedestrians off')
    if _state['tick'] is not None:
        unreal.unregister_slate_post_tick_callback(_state['tick'])
        _state['tick'] = None


def _refill():
    pawn = _state['pawn']
    origin, *axes = _state['basis']
    delta = pawn.get_actor_location() - origin
    centre = [float(delta.dot(a) / a.dot(a)) for a in axes[:2]]
    batch = json.loads(_state['source'].batch(centre))['frames']
    _state['frames'] = [f for f in _state['frames'] if f['time_s'] >= _state['clock']] + batch


def _tick(delta):
    if not _state['enabled']:
        return
    if _state['pawn'] is None or not unreal.SystemLibrary.is_valid(_state['pawn']):
        stop(); return   # Play ended or the car was replaced
    _state['clock'] += delta
    frames = _state['frames']
    if not frames or frames[-1]['time_s'] - _state['clock'] < 1.0:
        if not frames:
            _state['clock'] = 0.0
        _refill()
        frames = _state['frames']
    if _state['clock'] < frames[0]['time_s']:
        _state['clock'] = frames[0]['time_s']
    i = 0
    while i < len(frames) - 2 and frames[i + 1]['time_s'] <= _state['clock']:
        i += 1
    a, b = frames[i], frames[i + 1] if i + 1 < len(frames) else frames[i]
    span = max(b['time_s'] - a['time_s'], 1e-6)
    t = min(1.0, max(0.0, (_state['clock'] - a['time_s']) / span))
    after = {p['id']: p for p in b['pedestrians']}
    origin, east, north, up = _state['basis']
    seen = set()
    for index, p in enumerate(a['pedestrians']):
        q = after.get(p['id'], p)
        pos = [p['position'][k] + (q['position'][k] - p['position'][k]) * t for k in range(3)]
        actor = _state['pool'].get(p['id'])
        if actor is None:
            actor = _spawn(_state['world'], len(_state['pool']))
            if actor is None:
                continue   # every pooled character is in use; this walker is not drawn
            _state['pool'][p['id']] = actor
            _state['ground'][p['id']] = _ground_offset(_local_point(_state['basis'], pos), actor)
        h = math.radians(p['heading_deg'])
        direction = east * math.sin(h) + north * math.cos(h)
        where = _local_point(_state['basis'], pos) + unreal.Vector(0, 0, _state['ground'].get(p['id'], 0.0))
        actor.set_actor_location_and_rotation(where, unreal.MathLibrary.make_rot_from_x(direction), False, True)
        seen.add(p['id'])
    for key in [k for k in _state['pool'] if k not in seen]:
        _release(_state['pool'].pop(key))
        _state['ground'].pop(key, None)


def _ground_offset(where, actor):
    """Height correction (cm) from the planned pavement point to the surface actually rendered there.

    Planned heights come from the road data, which can differ from the rendered surface by 1-2 m (seen on
    Hamra Street); a downward trace from 5 m above finds the visible pavement or road. Corrections up to
    4.5 m are trusted; anything larger is treated as a bad hit (bridge deck, roof) and ignored.
    """
    ignore = [actor] + ([_state['pawn']] if _state.get('pawn') else [])
    hit = unreal.SystemLibrary.line_trace_single(
        _state['world'], where + unreal.Vector(0, 0, 500), where - unreal.Vector(0, 0, 500),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, ignore, unreal.DrawDebugTrace.NONE, True)
    if hit is None:
        return 0.0
    impact = hit.to_tuple()[4]
    dz = impact.z - where.z
    return dz if abs(dz) <= 450 else 0.0


def status():
    return {'enabled': _state['enabled'], 'walkers': len(_state['pool']), 'message': _state['message']}
