"""Supplementary Overture catalogs, search data and scenery; additive imports only."""
import json
from pathlib import Path

import unreal
import toolset_registry

ROOT = Path('C:/private/dynacity-data/engine/overture-context')
FOLDER = '/Game/Beirut/Overture'


def manifest():
    return json.loads((ROOT/'overture-context.json').read_text(encoding='utf-8'))


def actor_signature(actor):
    location, rotation, scale = actor.get_actor_location(), actor.get_actor_rotation(), actor.get_actor_scale3d()
    component = actor.get_component_by_class(unreal.StaticMeshComponent)
    return {'label': actor.get_actor_label(),
            'location': [round(float(v), 4) for v in (location.x, location.y, location.z)],
            'rotation': [round(float(v), 4) for v in (rotation.pitch, rotation.yaw, rotation.roll)],
            'scale': [round(float(v), 6) for v in (scale.x, scale.y, scale.z)],
            'mesh': component.static_mesh.get_path_name() if component and component.static_mesh else None,
            'materials': [component.get_material(i).get_path_name() if component.get_material(i) else None
                          for i in range(component.get_num_materials())] if component else []}


def baseline():
    import city_world
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('Stop Play before importing')
    if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
        raise RuntimeError('Wait for the active import')
    data = {'descriptor_labels': [str(d.label) for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs()],
            'actors': {a.get_path_name(): actor_signature(a) for a in city_world._actors()}}
    path = ROOT/'original-scene-baseline.json'
    if not path.exists(): path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return {'baseline_actors': len(data['actors']), 'baseline_descriptors': len(data['descriptor_labels'])}


def import_catalog():
    """Import geographic labels and JSON metadata as native Unreal StringTables."""
    data = manifest()
    imported = []
    for row in data['tables']:
        path = FOLDER+'/'+row['asset_name']
        table = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
        if table is None:
            table = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                row['asset_name'], FOLDER, unreal.StringTable, unreal.StringTableFactory())
        if table is None: raise RuntimeError('Could not create '+path)
        table.modify()
        table_id = unreal.StringTableLibrary.get_table_id(table)
        if not unreal.StringTableLibrary.import_table_from_csv_file(table_id, row['csv']):
            raise RuntimeError('Could not import '+row['csv'])
        keys = unreal.StringTableLibrary.get_keys_from_string_table(table_id)
        if len(keys) != row['entries']: raise RuntimeError('Catalog count mismatch '+row['type'])
        if not unreal.EditorAssetLibrary.save_loaded_asset(table, only_if_is_dirty=False):
            raise RuntimeError('Could not save '+path)
        imported.append({'type': row['type'], 'asset': path, 'entries': len(keys)})
    import location_search
    location_search._index = None
    report = {'catalogs': imported, 'search_places': len(location_search._data()['places']),
              'overture_search': location_search._data().get('overture'),
              'geometry': 'Catalog records only; visible ground patches imported separately.'}
    (ROOT/'catalog-import.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def finish_scenery():
    """Tag and save only the imported Overture ground actors, with no collision."""
    import city_world
    city_world._current()
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    expected = {j['name'] for j in manifest()['jobs']}
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs()
    wanted = [d.guid for d in descs if str(d.label) in expected]
    if wanted: unreal.WorldPartitionBlueprintLibrary.load_actors(wanted)
    actors = [a for a in subsystem.get_all_level_actors() if a.get_actor_label() in expected]
    if {a.get_actor_label() for a in actors} != expected: raise RuntimeError('Missing Overture scenery actors')
    for actor in actors:
        actor.modify()
        actor.tags = list(set(actor.tags)|{'DYNACITY_OVERTURE_GROUND'})
        actor.set_folder_path('DynaCity/Overture/Ground')
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        component.modify()
        actor.set_actor_enable_collision(False)
        component.set_collision_profile_name('NoCollision')
        component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        component.set_cast_shadow(False)
    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():
        raise RuntimeError('Could not save supplemental ground')
    if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(False, True):
        raise RuntimeError('Could not save supplemental assets')
    return verify()


def verify():
    data = manifest()
    expected = {j['name'] for j in data['jobs']}
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs()
    wanted = [d.guid for d in descs if str(d.label) in expected]
    if wanted: unreal.WorldPartitionBlueprintLibrary.load_actors(wanted)
    actors = [a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
              if a.get_actor_label() in expected]
    meshes = []
    for actor in actors:
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        meshes.append({'label': actor.get_actor_label(), 'actor': actor.get_path_name(),
                       'mesh': component.static_mesh.get_path_name(),
                       'visible': component.is_visible() and not actor.get_editor_property('hidden')
                                  and not component.get_editor_property('hidden_in_game'),
                       'no_collision': not actor.get_actor_enable_collision()
                                       and component.get_collision_enabled() == unreal.CollisionEnabled.NO_COLLISION})
    catalogs = []
    for row in data['tables']:
        table = unreal.load_asset(FOLDER+'/'+row['asset_name'])
        keys = unreal.StringTableLibrary.get_keys_from_string_table(unreal.StringTableLibrary.get_table_id(table)) if table else []
        catalogs.append({'type': row['type'], 'expected': row['entries'], 'actual': len(keys),
                         'passed': table is not None and len(keys) == row['entries']})
    report = {'map': unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_path_name(),
              'expected_ground_actors': len(expected), 'loaded_ground_actors': len(actors), 'ground': meshes,
              'catalogs': catalogs, 'source_counts': data['counts'],
              'passed': len(actors) == len(expected) and all(c['passed'] for c in catalogs)
                        and all(m['visible'] and m['no_collision'] for m in meshes)}
    baseline_path = ROOT/'original-scene-baseline.json'
    if baseline_path.exists():
        before = json.loads(baseline_path.read_text(encoding='utf-8'))
        # Guid.__str__ includes a Python wrapper address, so use stable labels for
        # descriptor loading and persistent actor paths for the identity check.
        original_labels = set(before.get('descriptor_labels', [a['label'] for a in before['actors'].values()]))
        absent = sorted(original_labels-{str(d.label) for d in descs})
        unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs if str(d.label) in original_labels])
        current = {a.get_path_name(): actor_signature(a) for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
        changes = [path for path, signature in before['actors'].items() if current.get(path) != signature]
        report['original_scene'] = {'missing_descriptors': absent, 'changed_actors': changes,
                                    'checked_actors': len(before['actors']), 'passed': not absent and not changes}
        report['passed'] = report['passed'] and report['original_scene']['passed']
    (ROOT/'unreal-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return {k: v for k, v in report.items() if k not in ('ground', 'source_counts')}


@unreal.uclass()
class OvertureTools(unreal.ToolsetDefinition):
    @toolset_registry.tool_call
    @staticmethod
    def baseline() -> str:
        return json.dumps(baseline())

    @toolset_registry.tool_call
    @staticmethod
    def import_catalog() -> str:
        return json.dumps(import_catalog())

    @toolset_registry.tool_call
    @staticmethod
    def finish_scenery() -> str:
        return json.dumps(finish_scenery())

    @toolset_registry.tool_call
    @staticmethod
    def verify() -> str:
        return json.dumps(verify())

    @toolset_registry.tool_call
    @staticmethod
    def reload_verify() -> str:
        import city_world
        city_world._current()
        levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if levels.is_in_play_in_editor(): raise RuntimeError('Stop Play before reload')
        if not levels.save_current_level(): raise RuntimeError('Could not save city')
        if not levels.load_level(city_world.MAP): raise RuntimeError('Could not reload city')
        return json.dumps(verify())


def register():
    global _registration
    from toolset_registry.registration import Registration
    _registration = Registration([OvertureTools])
    _registration.register()
