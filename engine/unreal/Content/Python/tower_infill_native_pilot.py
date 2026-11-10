"""Root-owned MCP integration of an additive-only tower preview; never saves a scene."""
import json
from pathlib import Path
import unreal
import corridor_reconstruction_install as ci

ROOT = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration16')
SOURCE = 'beirut001:bde30f76-7868-4187-92a7-a18270c58815'
LABEL = 'Tower infill pilot - estimated, NOT accepted'
REFERENCE = 'Corridor reconstruction - source-supported frontages (candidate identities, estimated geometry)'

def _actors():
    return {a.get_actor_label(): a for a in ci.city_world._actors()}

def _snapshot():
    ci.snapshot()  # Load the same World Partition building coverage before comparisons.
    return {a.get_path_name(): ci._state(a) for a in ci.city_world._actors()
            if ci._component(a) and a.get_actor_label() != LABEL}

def preflight():
    ci.cb._ready()
    ci._load_batches()
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    wanted = [d.guid for d in descs if str(d.label).startswith(('Corridor ', 'Reference ', 'DynaDriveStart'))]
    if wanted:
        unreal.WorldPartitionBlueprintLibrary.load_actors(wanted)
    a = _actors()
    if LABEL in a:
        raise RuntimeError('Existing tower preview must be rolled back first')
    if REFERENCE not in a:
        raise RuntimeError('Saved corridor reference actor is absent; candidates=' + str([str(d.label) for d in descs if 'Corridor' in str(d.label)]))
    return {'project': unreal.Paths.get_project_file_path(),
            'map': unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_path_name(),
            'reference': ci._state(a[REFERENCE]), 'static_actor_count': len(_snapshot()),
            'tracked_buildings': len(ci.snapshot()['actors']), 'scene_saved': False}

def prepare(spec_path):
    preflight()
    ROOT.mkdir(parents=True, exist_ok=True)
    s = ci._read(spec_path)
    if s['source_id'] != SOURCE or s['wall']['edge'] != 6:
        raise ValueError('Refusing different source building or wall')
    if ci._sha(s['geometry']) != s['sha256']:
        raise ValueError('Candidate geometry hash mismatch')
    if not 54 < s['verification']['additive_z_min'] < 55 or not 72 < s['verification']['additive_z_max'] < 73:
        raise ValueError('Candidate bounds do not match the existing raised tower')
    folder = '/Game/DynaCity/CorridorReconstruction/TowerInfillPilot/' + s['sha256'][:12]
    path = ci._import(s['geometry'], folder, 'Tower_upper_infill')
    if path is None:
        return {'importing': True}
    mesh = unreal.load_asset(path)
    editor = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    settings = editor.get_nanite_settings(mesh)
    if settings.get_editor_property('enabled'):
        settings.set_editor_property('enabled', False)
        editor.set_nanite_settings(mesh, settings, True)
    existing = ci._component(_actors()[REFERENCE]).get_materials()
    materials = {}
    for name, token in [('concrete_pale', 'M_concrete_pale__tower_'), ('glass', 'M_glass_window_')]:
        found = [m.get_path_name() for m in existing if m and token in m.get_name()]
        if len(found) != 1:
            raise RuntimeError('Expected one existing tower material for ' + name)
        materials[name] = found[0]
    slots = [str(x.material_slot_name) for x in mesh.static_materials]
    if set(slots) != set(materials):
        raise RuntimeError('Unexpected imported slots: ' + str(slots))
    data = {'mesh': path, 'materials': materials, 'spec': str(spec_path), 'sha256': s['sha256']}
    (ROOT / 'prepared.json').write_text(json.dumps(data, indent=2))
    return data

def apply():
    preflight()
    data = ci._read(ROOT / 'prepared.json')
    before = _snapshot()
    (ROOT / 'preview-backup.json').write_text(json.dumps(before, indent=2))
    actor = None
    try:
        ref = _actors()[REFERENCE]
        actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector())
        actor.set_actor_label(LABEL)
        actor.tags = ['DYNACITY_TOWER_INFILL_PREVIEW', 'source:' + SOURCE, 'ESTIMATED_NOT_SURVEYED']
        actor.set_actor_transform(ref.get_actor_transform(), False, True)
        comp = ci._component(actor)
        comp.set_static_mesh(unreal.load_asset(data['mesh']))
        for i, slot in enumerate(comp.static_mesh.static_materials):
            comp.set_material(i, unreal.load_asset(data['materials'][str(slot.material_slot_name)]))
        ci.cb._disable_visual_collision(actor, comp)
        if before != _snapshot():
            raise RuntimeError('Existing actor changed during additive preview')
        result = {'preview': ci._state(actor), 'existing_actors_exact': True,
                  'existing_actor_count': len(before), 'scene_saved': False}
        (ROOT / 'preview-report.json').write_text(json.dumps(result, indent=2))
        return result
    except Exception:
        if actor:
            unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(actor)
        raise

def rollback():
    ci.cb._ready()
    a = _actors()
    if LABEL in a:
        unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(a[LABEL])
    before = ci._read(ROOT / 'preview-backup.json')
    after = _snapshot()
    result = {'original_static_actors_exact': before == after,
              'original_static_actor_count': len(before), 'scene_saved': False,
              'preview_absent': LABEL not in _actors()}
    (ROOT / 'rollback-report.json').write_text(json.dumps(result, indent=2))
    if not result['original_static_actors_exact']:
        raise RuntimeError('Static actor restoration mismatch')
    return result


def verify_assets():
    """Save/reload only the two candidate mesh packages after preview rollback."""
    pre = preflight()
    before = _snapshot()
    editor = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    checks = {}
    for phase in ('infill070', 'infill085'):
        data = ci._read(ROOT / phase / 'prepared.json')
        mesh = unreal.load_asset(data['mesh'])
        package = mesh.get_outer()
        if not isinstance(package, unreal.Package):
            raise RuntimeError('Candidate outer is not a package')
        if '/TowerInfillPilot/' not in data['mesh']:
            raise RuntimeError('Refusing to save a non-candidate asset')
        saved = bool(unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False))
        result = unreal.EditorLoadingAndSavingUtils.reload_packages(
            [package], unreal.ReloadPackagesInteractionMode.ASSUME_POSITIVE)
        mesh = unreal.load_asset(data['mesh'])
        slots = [str(s.material_slot_name) for s in mesh.static_materials]
        nanite = bool(editor.get_nanite_settings(mesh).get_editor_property('enabled'))
        checks[phase] = {'mesh': data['mesh'], 'saved_only_candidate_mesh': saved,
                         'reload_succeeded': bool(result[0]), 'reload_message': str(result[1]),
                         'slots': slots, 'nanite_enabled': nanite}
        if not saved or not result[0] or nanite or set(slots) != set(data['materials']):
            raise RuntimeError('Candidate persistence verification failed: ' + str(checks[phase]))
    after = _snapshot()
    result = {'candidates': checks, 'original_static_actors_exact': before == after,
              'original_static_actor_count': len(before), 'tracked_buildings': pre['tracked_buildings'],
              'preview_absent': LABEL not in _actors(), 'scene_saved': False,
              'collision_scope': 'Additive visual overlay has no collision; original collisions unchanged'}
    (ROOT / 'candidate-assets-reload-verification.json').write_text(json.dumps(result, indent=2))
    if before != after:
        raise RuntimeError('Existing actor state changed during asset reload')
    return result
