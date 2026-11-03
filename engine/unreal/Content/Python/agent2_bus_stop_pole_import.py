"""Agent 2: finished stop poles at mapped OSM bus_stop nodes. Additive; touches no existing actor."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import unreal

TAG = 'DYNACITY_AGENT2_BUS_STOP_POLE'
BUILDING_WORDS = ('building', 'beirut001', 'structure', 'roof', 'bbed')


def apply(filename):
    import city_world, explore_city, library_assets
    from library_asset_core import read_prepared
    world = city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before importing')
    path = Path(filename).resolve(); data = json.loads(path.read_text(encoding='utf8'))
    if data.get('schema') != 'dynacity-agent2-bus-stop-poles/1': raise ValueError('Invalid manifest')
    if data['origin_utm_m'] != json.loads(city_world._checkpoint().read_text(encoding='utf8'))['origin_utm_m']:
        raise ValueError('City origin mismatch')
    if hashlib.sha256(Path(data['context_path']).read_bytes()).hexdigest() != data['context_sha256']:
        raise ValueError('Source context changed')
    prepared = Path(data['prepared_path']); asset = read_prepared(prepared)
    if asset['fbx_sha256'] != data['asset_fbx_sha256'] or asset['provenance']['id'] != 'kenney_road_sign_empty':
        raise ValueError('Asset mismatch')
    city_world.load_editor_geometry()
    if any(TAG in a.tags for a in city_world._actors()): raise RuntimeError('Already imported')
    imported = library_assets.import_prepared(prepared, 'kenney_road_sign_empty')
    mesh = unreal.load_asset(imported['mesh']); b = mesh.get_bounding_box(); center = (b.min + b.max) * .5
    _, east, north, _ = explore_city._basis(); valid = []; skipped = []; seen = set()
    for row in data['placements']:
        ident = row['source_id']; pt = row['position_local_m']; yaw = row['yaw_degrees_inferred'] or 0.0; sc = row['scale_inferred']
        if ident in seen or not ident.startswith('node/') or len(pt) != 3 or not all(math.isfinite(v) for v in [*pt, yaw, *sc]):
            raise ValueError('Invalid placement ' + ident)
        seen.add(ident); where = explore_city._world(pt)
        hit = unreal.SystemLibrary.line_trace_single(world, where + unreal.Vector(0, 0, 400), where - unreal.Vector(0, 0, 300),
            unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, [], unreal.DrawDebugTrace.NONE, True)
        label = ''; z = where.z; traced = False
        if hit is not None:
            info = hit.to_dict(); a = info.get('hit_actor'); label = a.get_actor_label() if a else ''
            if any(w in label.lower() for w in BUILDING_WORDS) or info['impact_normal'].z < .3:
                skipped.append({'source_id': ident, 'reason': 'trace hit building/steep geometry', 'hit_actor': label}); continue
            if abs(info['impact_point'].z - where.z) <= 300: z = info['impact_point'].z; traced = True
        a_rad = math.radians(yaw)
        rot = unreal.MathLibrary.make_rot_from_x(east * math.cos(a_rad) + north * math.sin(a_rad))
        off = unreal.MathLibrary.get_forward_vector(rot) * center.x * sc[0] + unreal.MathLibrary.get_right_vector(rot) * center.y * sc[1]
        valid.append((row, unreal.Vector(where.x, where.y, z) - off, rot, traced, label, z - where.z))
    if not valid: raise RuntimeError('No placements')
    cls = unreal.load_class(None, '/Script/DynaCityDrive.DynaStreetInstances'); sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    groups = defaultdict(list)
    for e in valid: groups[e[0]['production_area']].append(e)
    made = []
    old = []
    for a in city_world._actors():
        if 'street_kind:bus_stop' in {str(t) for t in a.tags}:
            c = a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
            if c is not None: old.append((a, c, c.is_visible(), c.get_editor_property('hidden_in_game')))
    try:
        for area, items in sorted(groups.items()):
            actor = sub.spawn_actor_from_class(cls, unreal.Vector()); made.append(actor)
            actor.set_actor_label('Street_BusStopPole_' + re.sub('[^A-Za-z0-9]+', '_', area)); actor.tags = [TAG]
            actor.set_folder_path('DynaCity/Agent2 mapped bus stop poles/' + area); actor.set_editor_property('is_spatially_loaded', True)
            actor.set_editor_property('layer_kind', 'bus_stop_pole'); actor.set_editor_property('production_area', area)
            actor.set_editor_property('source_manifest', str(path)); actor.set_street_mesh(mesh); actor.set_street_collision(False)
            actor.set_street_cull_distance(25000, 70000)
            for row, pos, rot, _, _, _ in items:
                s = row['scale_inferred']
                if actor.add_street_instance(unreal.Transform(location=pos, rotation=rot, scale=unreal.Vector(*s)), row['source_id']) < 0:
                    raise RuntimeError('Placement failed ' + row['source_id'])
            if actor.get_street_instance_count() != len(items): raise RuntimeError('Count mismatch')
        # Generic procedural bus_stop props sit at the same mapped nodes; hide (not delete) them, as the bollard replacement did.
        for a, c, _, _ in old:
            a.modify(); c.modify(); c.set_visibility(False); c.set_hidden_in_game(True)
        if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in made} | {a.get_outermost() for a, _, _, _ in old}), True):
            raise RuntimeError('Could not save new actor packages')
    except Exception:
        for a, c, v, h in old: c.set_visibility(v); c.set_hidden_in_game(h)
        for a in made: sub.destroy_actor(a)
        raise
    report = {'schema': 'dynacity-agent2-bus-stop-pole-import/1', 'manifest': str(path), 'asset': imported,
        'actors': [a.get_path_name() for a in made], 'count': len(valid), 'skipped_at_import': skipped,
        'skipped_in_source': data['skipped_in_source'],
        'hidden_generic_bus_stop_groups': [{'actor': a.get_path_name(), 'label': a.get_actor_label(), 'was_visible': v, 'was_hidden_in_game': h, 'instances': a.get_street_instance_count()} for a, c, v, h in old],
        'placements': [{'source_id': r['source_id'], 'surface_traced': t, 'hit_actor': l, 'height_adjustment_cm': dz} for r, _, _, t, l, dz in valid]}
    (city_world._root() / 'Saved/DynaCity/agent2-bus-stop-pole-import.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    return report
