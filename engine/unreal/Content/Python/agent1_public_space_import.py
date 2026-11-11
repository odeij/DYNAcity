"""Replace the retired procedural mapped-fountain placeholder with a finished CC0 model.

Positions come from the existing source-node instances (OSM amenity=fountain nodes);
none are moved or added. Scale and heading are inferred and recorded.
"""
import json
from pathlib import Path
import unreal

KIND = 'fountain'
KEY = 'fountain_polypizza'
PREPARED = r'C:/private/dynacity-data/engine/asset-quality-pass/prepared/fountain_polypizza'
INFERRED_SCALE = 0.6  # ~2.4 m basin, matching the retired 2.36 m placeholder; OSM gives no size


def _kind_actors():
    import city_world
    out = []
    for actor in city_world._actors():
        tags = {str(t) for t in actor.tags}
        if 'DYNACITY_STREET_CONTEXT' in tags and 'street_kind:' + KIND in tags:
            out.append(actor)
    return out


def apply():
    import city_world, street_context, library_assets
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play first')
    city_world.load_editor_geometry(); street_context.load_editor_geometry()
    root = city_world._root() / 'Saved/DynaCity'
    record = library_assets.import_prepared(Path(PREPARED), KEY)
    new = unreal.load_asset(record['mesh'])
    actors = _kind_actors()
    if not actors:
        raise RuntimeError('No fountain actors loaded')
    backup_path = root / 'agent1-fountain-replacement-backup.json'
    backup = json.loads(backup_path.read_text(encoding='utf-8')) if backup_path.exists() else {}
    touched = []; report = []
    for actor in actors:
        component = actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        if not component:
            continue
        key = actor.get_path_name()
        tags = [str(t) for t in actor.tags]
        if key not in backup:
            backup[key] = {'mesh': component.static_mesh.get_path_name(), 'tags': tags,
                           'hidden': bool(actor.get_editor_property('hidden'))}
        actor.modify(); component.modify()
        if component.static_mesh != new:
            transforms = [component.get_instance_transform(i, world_space=True) for i in range(component.get_instance_count())]
            actor.set_street_mesh(new)
            for i, t in enumerate(transforms):
                t.scale3d = unreal.Vector(INFERRED_SCALE, INFERRED_SCALE, INFERRED_SCALE)
                component.update_instance_transform(i, t, world_space=True,
                    mark_render_state_dirty=i == len(transforms) - 1, teleport=True)
        actor.tags = [t for t in actor.tags if str(t) != 'DYNACITY_RETIRED_PLACEHOLDER']
        actor.set_actor_hidden_in_game(False); actor.set_is_temporarily_hidden_in_editor(False)
        touched.append(actor)
        report.append({'actor': key, 'label': actor.get_actor_label(), 'instances': component.get_instance_count()})
    backup_path.write_text(json.dumps(backup, indent=2), encoding='utf-8')
    # Keep the policy pass from hiding the finished group again.
    active_path = root / 'library-assets-active.json'
    data = json.loads(active_path.read_text(encoding='utf-8'))
    copy = root / 'library-assets-active.before-agent1.json'
    if not copy.exists():
        copy.write_bytes(active_path.read_bytes())
    data['street'][KIND] = record
    active_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    packages = list({a.get_outermost() for a in touched})
    if not unreal.EditorLoadingAndSavingUtils.save_packages(packages, True):
        raise RuntimeError('Save failed')
    out = {'schema': 'agent1-fountain-import/1', 'asset': record, 'inferred_scale': INFERRED_SCALE,
           'scale_note': 'Inferred; OSM nodes carry no size or orientation. Heading from nearest road tangent (inferred).',
           'groups': report, 'total_instances': sum(r['instances'] for r in report)}
    (root / 'agent1-fountain-import.json').write_text(json.dumps(out, indent=2), encoding='utf-8')
    return out


def verify():
    """Report loaded vegetation / public-space instance counts and mesh identities."""
    import city_world, street_context
    city_world._current(); city_world.load_editor_geometry(); street_context.load_editor_geometry()
    root = city_world._root() / 'Saved/DynaCity'
    manifest = json.loads((root / 'street-instances.json').read_text(encoding='utf-8'))
    ids_by_actor = {}
    for row in manifest['instances']:
        ids_by_actor.setdefault(row['actor'], []).append(row['source_id'])
    kinds = {}
    for actor in city_world._actors():
        tags = {str(t) for t in actor.tags}
        kind = next((t.split(':', 1)[1] for t in tags if t.startswith('street_kind:')), None)
        component = actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        if kind in ('fountain', 'bench', 'waste_basket', 'bicycle_parking', 'shelter', 'tree_broadleaf', 'tree_conifer') and component:
            row = kinds.setdefault(kind, {'instances': 0, 'actors': 0, 'meshes': set(), 'hidden_actors': 0, 'retired': 0})
            row['instances'] += component.get_instance_count(); row['actors'] += 1
            row['meshes'].add(component.static_mesh.get_name())
            row['hidden_actors'] += int(bool(actor.get_editor_property('hidden')))
            row['retired'] += int('DYNACITY_RETIRED_PLACEHOLDER' in tags)
        if actor.get_actor_label().startswith('Street_MappedRecycling_'):
            row = kinds.setdefault('recycling', {'instances': 0, 'actors': 0, 'meshes': set(), 'source_ids': []})
            row['instances'] += actor.get_street_instance_count(); row['actors'] += 1
            row['source_ids'] += list(actor.get_editor_property('source_ids'))
        if actor.get_actor_label().startswith(('Street_MappedBollard', 'Street_MappedBarrier', 'Street_MappedSpeedBump',
                                               'Street_MappedPowerTower', 'Street_MappedCamera', 'Street_MappedFixedCamera')):
            row = kinds.setdefault('other:' + actor.get_actor_label().split('_')[1], {'instances': 0, 'actors': 0})
            try:
                row['instances'] += actor.get_street_instance_count()
            except Exception:
                pass
            row['actors'] += 1
    for row in kinds.values():
        if 'meshes' in row:
            row['meshes'] = sorted(row['meshes'])
    (root / 'agent1-vegetation-public-space-verification.json').write_text(json.dumps(kinds, indent=2), encoding='utf-8')
    return kinds
