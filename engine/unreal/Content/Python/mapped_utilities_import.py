"""Place finished CC0 models at mapped OSM utility/waterfront nodes (agent-3 manifest)."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import unreal

TAG = 'DYNACITY_MAPPED_UTILITY'
PREFIX = 'Street_MappedUtility_'

def apply(filename):
    import city_world, explore_city, library_assets
    from library_asset_core import read_prepared
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before importing utilities')
    path = Path(filename).resolve(); data = json.loads(path.read_text(encoding='utf8'))
    if data.get('schema') != 'dynacity-mapped-utilities-waterfront/1': raise ValueError('Invalid manifest')
    if data['origin_utm_m'] != json.loads(city_world._checkpoint().read_text(encoding='utf8'))['origin_utm_m']:
        raise ValueError('City origin mismatch')
    if hashlib.sha256(Path(data['context_path']).read_bytes()).hexdigest() != data['context_sha256']:
        raise ValueError('Source context changed')
    if any(TAG in a.tags for a in city_world._actors()): raise RuntimeError('Utility layer already exists')
    rows = data['placements']; seen = set()
    for row in rows:
        ident = row['source_id']; point = row['position_local_m']
        if (not ident.startswith('node/') or ident in seen or len(point) != 3 or
                any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in [*point, row['heading_degrees'], row['scale']])):
            raise ValueError('Invalid placement ' + ident)
        seen.add(ident)
    city_world.load_editor_geometry()
    _, east, north, _ = explore_city._basis()
    cls = unreal.load_class(None, '/Script/DynaCityDrive.DynaStreetInstances')
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    meshes = {}; assets_report = {}
    for aid, info in data['assets'].items():
        asset = read_prepared(Path(info['prepared_path']))
        if asset['fbx_sha256'] != info['fbx_sha256'] or asset['provenance'] != info['provenance']:
            raise ValueError('Prepared asset differs from manifest: ' + aid)
        imported = library_assets.import_prepared(Path(info['prepared_path']), aid)
        meshes[aid] = unreal.load_asset(imported['mesh']); assets_report[aid] = imported
    groups = defaultdict(list)
    for row in rows: groups[(row['asset_id'], row['production_area'] or 'Unassigned')].append(row)
    created = []
    try:
        for (aid, area), items in sorted(groups.items()):
            mesh = meshes[aid]; b = mesh.get_bounding_box(); center = (b.min + b.max) * .5
            actor = subsystem.spawn_actor_from_class(cls, unreal.Vector()); created.append(actor)
            actor.set_actor_label(PREFIX + aid + '_' + re.sub('[^A-Za-z0-9]+', '_', area)); actor.tags = [TAG]
            actor.set_folder_path('DynaCity/Mapped utilities/' + aid + '/' + area)
            actor.set_editor_property('is_spatially_loaded', True)
            actor.set_editor_property('layer_kind', 'utility'); actor.set_editor_property('production_area', area)
            actor.set_editor_property('source_manifest', str(path)); actor.set_street_mesh(mesh)
            actor.set_street_collision(False); actor.set_street_cull_distance(100000, 200000)
            for row in items:
                h = math.radians(row['heading_degrees']); d = east * math.cos(h) + north * math.sin(h)
                rotation = unreal.MathLibrary.make_rot_from_y(d) if row['rotation_rule'] == 'road_y' else unreal.MathLibrary.make_rot_from_x(d)
                s = row['scale']
                offset = (unreal.MathLibrary.get_forward_vector(rotation) * center.x + unreal.MathLibrary.get_right_vector(rotation) * center.y) * s
                transform = unreal.Transform(location=explore_city._world(row['position_local_m']) - offset, rotation=rotation, scale=unreal.Vector(s, s, s))
                if actor.add_street_instance(transform, row['source_id']) < 0: raise RuntimeError('Placement failed')
            if actor.get_street_instance_count() != len(items): raise RuntimeError('Count mismatch')
        if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in created}), True):
            raise RuntimeError('Save failed')
    except Exception:
        for actor in created: subsystem.destroy_actor(actor)
        raise
    report = {'schema': 'dynacity-mapped-utilities-import/1', 'manifest': str(path), 'assets': assets_report,
              'actors': [a.get_path_name() for a in created], 'source_ids': sorted(seen), 'count': len(rows),
              'notice': 'Illustrative models; dimensions, scale and orientation inferred as recorded in the manifest.'}
    (city_world._root() / 'Saved/DynaCity/mapped-utilities-import.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    return report

def verify():
    import city_world
    root = city_world._root() / 'Saved/DynaCity'
    report = json.loads((root / 'mapped-utilities-import.json').read_text(encoding='utf8'))
    expected = set(report['source_ids'])
    descs = [d for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs() if str(d.label).startswith(PREFIX)]
    if len(descs) != len(report['actors']): raise RuntimeError('Saved descriptor count differs')
    guids = [d.guid for d in descs]
    unreal.WorldPartitionBlueprintLibrary.unload_actors(guids); unreal.WorldPartitionBlueprintLibrary.load_actors(guids)
    ids = []; groups = []
    for actor in [a for a in city_world._actors() if a.get_actor_label().startswith(PREFIX)]:
        source = list(actor.get_editor_property('source_ids')); count = actor.get_street_instance_count()
        if count != len(source): raise RuntimeError('Source ID and instance count differ')
        ids.extend(source); groups.append({'label': actor.get_actor_label(), 'source_ids': source, 'count': count})
    if len(ids) != len(expected) or set(ids) != expected: raise RuntimeError('Reloaded identities differ')
    result = {'groups': groups, 'total': len(ids), 'reloaded_external_actors': True}
    (root / 'mapped-utilities-verification.json').write_text(json.dumps(result, indent=2), encoding='utf8')
    return result
