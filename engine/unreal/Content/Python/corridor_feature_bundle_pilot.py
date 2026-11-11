"""Root-owned corridor feature bundle pilot combining tower infill (70%) and granite parapet (91 panels).

Additive-only visual preview; never saves the scene. Uses verified prepared.json assets from
native/iteration16/infill070 and native/iteration27-granite-parapet.
"""
import json
from pathlib import Path
import unreal
import corridor_reconstruction_install as ci

# Pinned geometry hashes from verified prepared.json assets
TOWER_SHA256 = '26b52c12c5395fcdc70c3d766ad95a8f59fc5e2c20300576a85acd7d1a70f67a'
GRANITE_SHA256 = '89f4b0371d56be65fc02d40172366ef89a86602430a21186305e537a3fcd801f'

# Expected project and map paths (private project)
EXPECTED_PROJECT = 'C:/private/dynacity-data/engine/sea-highway/UnrealPrototype/DynaCityBeirut.uproject'
EXPECTED_MAP = '/Game/Maps/BeirutCityBatched.BeirutCityBatched'

# Tower infill feature configuration (from iteration16/infill070)
TOWER_INFILL = {
    'root': Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration16/infill070'),
    'label': 'Corridor feature bundle - Tower infill 70% - estimated, NOT accepted',
    'source_id': 'beirut001:bde30f76-7868-4187-92a7-a18270c58815',
    'reference_label': 'Corridor reconstruction - source-supported frontages (candidate identities, estimated geometry)',
    'prepared_path': Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration16/infill070/prepared.json'),
    'expected_frac': 0.7,
    'expected_sha256': TOWER_SHA256,
    'expected_slots': frozenset({'concrete_pale', 'glass'}),
    'mesh_path_contains': '/TowerInfillPilot/',
}

# Granite parapet feature configuration (from iteration27-granite-parapet)
GRANITE_PARAPET = {
    'root': Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration27-granite-parapet'),
    'label': 'Corridor feature bundle - Granite parapet 91 panels - estimated, NOT accepted',
    'source_id': 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1',
    'reference_label': 'Corridor reconstruction - source-supported frontages (candidate identities, estimated geometry)',
    'prepared_path': Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration27-granite-parapet/prepared.json'),
    'expected_frac': 0.7,
    'expected_sha256': GRANITE_SHA256,
    'expected_slots': frozenset({'stone_light__granite_overlay'}),
    'mesh_path_contains': '/GraniteParapetPilot/',
}

# Bundle configuration - iteration28 unique labels
BUNDLE = {
    'root': Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration28-feature-bundle'),
    'features': [TOWER_INFILL, GRANITE_PARAPET],
    'labels': [TOWER_INFILL['label'], GRANITE_PARAPET['label']],
    'bundle_tag': 'DYNACITY_FEATURE_BUNDLE_PREVIEW',
}

def _guard():
    project = unreal.Paths.convert_relative_path_to_full(unreal.Paths.get_project_file_path()).replace('\\', '/')
    if project.casefold() != EXPECTED_PROJECT.casefold():
        raise RuntimeError('Wrong private project: ' + project)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if not world or world.get_path_name() != EXPECTED_MAP:
        raise RuntimeError('Wrong map')
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('PIE active; preserve play')


def _actors():
    return {a.get_path_name(): a for a in ci.city_world._actors()}


def _snapshot(excluded=()):
    return {p: ci._state(a) for p, a in _actors().items()
            if p not in excluded and ci._component(a)}


def _reference():
    found = [a for a in _actors().values() if a.get_actor_label() == TOWER_INFILL['reference_label']]
    if len(found) != 1:
        raise RuntimeError('Expected one corridor reference actor')
    return found[0]


def _verify(feature):
    data = ci._read(feature['prepared_path'])
    if data['sha256'] != feature['expected_sha256']:
        raise ValueError('Prepared geometry hash mismatch')
    spec = ci._read(data['spec'])
    geometry = spec.get('geometry', spec.get('overlay_glb'))
    if spec['source_id'] != feature['source_id'] or ci._sha(geometry) != feature['expected_sha256']:
        raise ValueError('Source identity or actual geometry bytes mismatch')
    fraction = spec.get('upper_infill_frac', spec.get('fraction'))
    if fraction != .7 or feature['mesh_path_contains'] not in data['mesh']:
        raise ValueError('Wrong pilot fraction or asset scope')
    mesh = unreal.load_asset(data['mesh'])
    if not mesh or {str(s.material_slot_name) for s in mesh.static_materials} != feature['expected_slots']:
        raise ValueError('Mesh or material slots mismatch')
    if set(data['materials']) != feature['expected_slots']:
        raise ValueError('Incomplete material bindings')
    if any(not unreal.load_asset(path) for path in data['materials'].values()):
        raise ValueError('Material asset missing')
    return data


def preflight():
    _guard()
    ci.cb._ready()
    ci._load_batches()
    descriptors = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    wanted = [d.guid for d in descriptors if str(d.label).startswith(('Corridor ', 'Reference ', 'DynaDriveStart'))]
    if wanted:
        unreal.WorldPartitionBlueprintLibrary.load_actors(wanted)
    ci.snapshot()
    _reference()
    if any(BUNDLE['bundle_tag'] in a.tags for a in _actors().values()):
        raise RuntimeError('Owned preview already present; rollback first')
    return {'project': EXPECTED_PROJECT, 'map': EXPECTED_MAP,
            'static_actor_count': len(_snapshot()), 'tracked_buildings': len(ci.snapshot()['actors']),
            'scene_saved': False}


def prepare():
    preflight()
    data = {f['label']: _verify(f) for f in BUNDLE['features']}
    BUNDLE['root'].mkdir(parents=True, exist_ok=True)
    (BUNDLE['root'] / 'prepared.json').write_text(json.dumps(data, indent=2))
    return {'features': data, 'scene_saved': False}


def _cleanup(owned, token):
    editor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for path in reversed(owned):
        actor = _actors().get(path)
        if actor:
            if token not in actor.tags or BUNDLE['bundle_tag'] not in actor.tags:
                raise RuntimeError('Ownership changed; refusing deletion')
            if not editor.destroy_actor(actor):
                raise RuntimeError('Preview actor deletion failed')


def apply():
    import uuid
    preflight()
    data = {f['label']: _verify(f) for f in BUNDLE['features']}
    before = _snapshot()
    token = 'bundle-run:' + str(uuid.uuid4())
    owned = []
    transaction = {'before': before, 'owned': owned, 'token': token}
    BUNDLE['root'].mkdir(parents=True, exist_ok=True)
    backup = BUNDLE['root'] / 'transaction.json'
    backup.write_text(json.dumps(transaction, indent=2))
    editor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    try:
        ref = _reference()
        for feature in BUNDLE['features']:
            actor = editor.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector())
            if not actor:
                raise RuntimeError('Preview spawn failed')
            path = actor.get_path_name()
            # Immediate local registration lets failures in tags/label/transform clean this actor.
            owned.append(path)
            try:
                actor.tags = [BUNDLE['bundle_tag'], token, 'source:' + feature['source_id'], 'ESTIMATED_NOT_SURVEYED']
                backup.write_text(json.dumps(transaction, indent=2))
                actor.set_actor_label(feature['label'])
                actor.set_actor_transform(ref.get_actor_transform(), False, True)
                comp = ci._component(actor)
                item = data[feature['label']]
                comp.set_static_mesh(unreal.load_asset(item['mesh']))
                for i, slot in enumerate(comp.static_mesh.static_materials):
                    comp.set_material(i, unreal.load_asset(item['materials'][str(slot.material_slot_name)]))
                ci.cb._disable_visual_collision(actor, comp)
            except Exception:
                # Actor is known from this exact spawn even if tag assignment itself failed.
                if not editor.destroy_actor(actor):
                    raise RuntimeError('Partial spawn cleanup failed')
                owned.remove(path)
                raise
        if before != _snapshot(owned):
            raise RuntimeError('Existing actor state changed')
        return {'existing_actors_exact': True, 'existing_actor_count': len(before),
                'owned': list(owned), 'scene_saved': False}
    except Exception:
        _cleanup(owned, token)
        backup.write_text(json.dumps(transaction, indent=2))
        if before != _snapshot():
            raise RuntimeError('Failure cleanup did not preserve original scene')
        raise


def rollback():
    _guard()
    transaction = ci._read(BUNDLE['root'] / 'transaction.json')
    _cleanup(transaction['owned'], transaction['token'])
    result = {'original_static_actors_exact': transaction['before'] == _snapshot(),
              'original_static_actor_count': len(transaction['before']),
              'preview_absent': not any(p in _actors() for p in transaction['owned']), 'scene_saved': False}
    (BUNDLE['root'] / 'rollback-report.json').write_text(json.dumps(result, indent=2))
    if not result['original_static_actors_exact'] or not result['preview_absent']:
        raise RuntimeError('Rollback mismatch; no historical transforms restored')
    return result


def stage(action):
    return {'preflight': preflight, 'prepare': prepare, 'apply': apply, 'rollback': rollback}[action]()
