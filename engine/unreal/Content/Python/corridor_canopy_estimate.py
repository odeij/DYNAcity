"""Editor side of the corridor canopy estimate: Meta/WRI CHM crown peaks as one reversible library-tree group.

Runs inside the editor through ``open_data_tools.OpenDataTools.canopy``.  Works from the plan written by
``scripts/prepare_canopy_unreal_plan.py`` (offline filters and the project's EPSG:32636 -> Unreal conversion) and adds
the checks that need the live scene: ground trace, what the trace hits and a ring of clearance traces.  The group is
ONE ``DynaStreetInstances`` actor labelled ``Corridor canopy estimate (Meta/WRI 2019)``; the 1,498 OSM/illustrative
tree instances are never touched.  Positions are model estimates good to about 10 m, never shifted here.
"""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import unreal

import canopy_unreal as cu
import city_world
import explore_city

RUN = Path('C:/private/dynacity-data/open-sources-20261120/canopy/unreal-run')
MESH = '/Game/Beirut/LibraryAssets/Meshes/tree_small_02_e381adfb18.tree_small_02_e381adfb18'
FOLDER = 'DynaCity/Corridor canopy estimate'
CLEARANCE_CM = 150.0
OSM_TREE_KINDS = ('tree_broadleaf', 'tree_conifer', 'tree_palm')


def _world():
    return unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


def _cls():
    return unreal.load_class(None, '/Script/DynaCityDrive.DynaStreetInstances')


def _basis_dict():
    origin, east, north, up = explore_city._basis()
    return {'origin': [origin.x, origin.y, origin.z], 'east': [east.x, east.y, east.z],
            'north': [north.x, north.y, north.z], 'up': [up.x, up.y, up.z]}


def _canopy_actors():
    return [a for a in city_world._actors() if a.get_actor_label().startswith(cu.LABEL_PREFIX)]


def _osm_tree_actors():
    out = []
    for a in city_world._actors():
        tags = [str(t) for t in a.tags]
        if 'DYNACITY_STREET_CONTEXT' in tags and any('street_kind:' + k in tags for k in OSM_TREE_KINDS):
            out.append(a)
    return out


def _street_instance_total(actors):
    return sum(int(a.get_street_instance_count()) for a in actors)


def load_all():
    """Load the saved street groups and any canopy group (World Partition external actors are unloaded after a reopen)."""
    city_world._current()
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    prefixes = ('Street_', 'StreetCover_', 'StreetCollision_', 'StreetLinear_', 'buildings', 'roads', 'terrain', 'source-', 'structures', cu.LABEL_PREFIX)
    wanted = [d.guid for d in descs if str(d.label).startswith(prefixes)]
    if wanted:
        unreal.WorldPartitionBlueprintLibrary.load_actors(wanted)
    return {'descs': len(descs), 'requested': len(wanted), 'actors': len(city_world._actors()),
            'canopy_descs': sum(1 for d in descs if str(d.label).startswith(cu.LABEL_PREFIX))}


def tree_descs():
    """Every World Partition descriptor of an OSM tree group: label, loaded or not (read-only)."""
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    live = {a.get_actor_label() for a in _osm_tree_actors()}
    rows = []
    for d in descs:
        label = str(d.label)
        if label.startswith('Street_') and '_tree_' in label:
            rows.append({'label': label, 'loaded': label in live})
    return {'descs': len(rows), 'rows': rows}


def counts():
    """Tree counts: the OSM/illustrative groups against the saved report, and the canopy group."""
    root = city_world._root() / 'Saved' / 'DynaCity'
    report = json.loads((root / 'street-instances.json').read_text())
    pre_clip = sum(g['instances'] for g in report['groups'] if g['kind'].startswith('tree_'))
    actors = _osm_tree_actors()
    descs = tree_descs()['descs']
    per_group = {a.get_actor_label(): int(a.get_street_instance_count()) for a in actors}
    canopy = _canopy_actors()
    kinds = Counter()
    for a in actors:
        for t in a.tags:
            if str(t).startswith('street_kind:tree_'):
                kinds[str(t)[len('street_kind:'):]] += int(a.get_street_instance_count())
    return {'osm_tree_groups_in_world_partition': descs, 'osm_tree_groups_loaded': len(actors),
            'osm_tree_instances_loaded': sum(per_group.values()), 'osm_per_group': per_group, 'osm_by_kind': dict(kinds),
            'osm_tree_instances_in_saved_report_before_beirut_clip': pre_clip,
            'canopy_groups': len(canopy), 'canopy_instances': _street_instance_total(canopy),
            'canopy_labels': [a.get_actor_label() for a in canopy], 'map': _world().get_path_name()}


def _trace_z(x, y):
    h = unreal.SystemLibrary.line_trace_single(_world(), unreal.Vector(x, y, 60000.), unreal.Vector(x, y, -30000.),
                                               unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False, [],
                                               unreal.DrawDebugTrace.NONE, True)
    if h is None:
        return None, None
    t = h.to_tuple() if hasattr(h, 'to_tuple') else h
    if not t[0]:
        return None, None
    actor = t[9] if len(t) > 9 else None
    return float(t[4].z), (actor.get_actor_label() if actor else None)


def prepare(plan_path, out_name='placement.json'):
    """Trace every offline-passing candidate: ground Z, trunk surface, clearance ring; write the placement file."""
    plan = cu.load_plan(plan_path)
    live = _basis_dict()
    if cu.Basis.from_dict(live) != cu.Basis.from_dict(plan['basis']):
        raise RuntimeError('Live scene basis differs from the plan basis: %s vs %s' % (live, plan['basis']))
    mesh = unreal.load_asset(MESH)
    box = mesh.get_bounding_box()
    mesh_height_cm = float(box.max.z - box.min.z)
    if abs(box.min.z) > 1e-3:
        raise RuntimeError('Tree pivot is not at the base; this stage assumes z=0 at the trunk base')
    ring = cu.ring_offsets_cm(CLEARANCE_CM)
    skipped = Counter(plan['offline_skipped'])
    skipped_by_surface = Counter()
    placed, skipped_rows = [], []
    for row in plan['rows']:
        x, y = row['unreal_x_cm'], row['unreal_y_cm']
        z, label = _trace_z(x, y)
        centre = cu.classify_surface(label)
        ring_cats = []
        if centre[0] == 'ground':
            for dx, dy in ring:
                _, rl = _trace_z(x + dx, y + dy)
                ring_cats.append(cu.classify_surface(rl))
        reason = cu.clearance_verdict(centre, ring_cats)
        if reason:
            skipped[reason] += 1
            skipped_by_surface[reason + ' | ' + centre[1]] += 1
            skipped_rows.append({'id': row['id'], 'reason': reason, 'surface': centre[1], 'xy_cm': [x, y]})
            continue
        scale = cu.mesh_scale(row['height_m'], mesh_height_cm)
        yaw = int.from_bytes(hashlib.sha256(row['id'].encode()).digest()[:4], 'little') / 2 ** 32 * 360.0
        placed.append({**row, 'ground_z_cm': round(z, 2), 'ground_surface': label, 'scale': round(scale, 4), 'yaw_deg': round(yaw, 3)})
    out = {'schema': 'dynacity-canopy-placement/1', 'plan': str(plan_path), 'mesh': MESH, 'mesh_height_cm': mesh_height_cm,
           'basis': live, 'candidates_in': plan['candidates_in'], 'placed_count': len(placed),
           'skipped_by_reason': dict(skipped), 'skipped_by_reason_and_surface': dict(skipped_by_surface),
           'ground_surfaces_used': dict(Counter(r['ground_surface'] for r in placed)), 'clearance_cm': CLEARANCE_CM,
           'placed': placed, 'skipped_rows': skipped_rows}
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / out_name).write_text(json.dumps(out), encoding='utf-8')
    return {k: out[k] for k in ('candidates_in', 'placed_count', 'skipped_by_reason', 'ground_surfaces_used', 'skipped_by_reason_and_surface')}


def apply(placement_name='placement.json', save=True, mesh_path=None):
    """Spawn the single canopy group from the placement file. Refuses if a canopy group already exists.

    ``mesh_path`` may name a derived copy of the library tree (same geometry and materials, different Nanite
    shape preservation) instead of the library mesh itself; the plain library mesh is the default.
    """
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play first')
    city_world._current()
    if _canopy_actors():
        raise RuntimeError('A canopy group already exists; roll it back first')
    before = counts()
    if before['osm_tree_groups_loaded'] != before['osm_tree_groups_in_world_partition']:
        raise RuntimeError('OSM tree groups are not all loaded: %s' % before)
    data = json.loads((RUN / placement_name).read_text(encoding='utf-8'))
    mesh = unreal.load_asset(mesh_path or data['mesh'])
    base_box, box = unreal.load_asset(data['mesh']).get_bounding_box(), mesh.get_bounding_box()
    if abs(base_box.max.z - box.max.z) > 1e-3 or abs(box.min.z) > 1e-3:
        raise RuntimeError('Override mesh does not match the library tree height/pivot')
    cls = _cls()
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor = subsystem.spawn_actor_from_class(cls, unreal.Vector())
    try:
        actor.modify()
        actor.set_actor_label(cu.LABEL_PREFIX)
        actor.set_folder_path(FOLDER)
        actor.tags = list(cu.TAGS)
        actor.set_editor_property('is_spatially_loaded', True)
        actor.set_editor_property('layer_kind', 'tree_broadleaf')
        actor.set_editor_property('production_area', 'Corridor canopy estimate')
        actor.set_editor_property('source_manifest', data['plan'])
        actor.set_street_mesh(mesh)
        actor.clear_street_instances()
        actor.set_street_collision(False)
        actor.set_street_cull_distance(18000, 50000)
        index = []
        for r in data['placed']:
            t = unreal.Transform(location=unreal.Vector(r['unreal_x_cm'], r['unreal_y_cm'], r['ground_z_cm']),
                                 rotation=unreal.Rotator(0.0, r['yaw_deg'], 0.0),
                                 scale=unreal.Vector(r['scale'], r['scale'], r['scale']))
            i = actor.add_street_instance(t, r['id'])
            if i < 0:
                raise RuntimeError('Could not add instance ' + r['id'])
            index.append(r['id'])
        if actor.get_street_instance_count() != len(data['placed']):
            raise RuntimeError('Instance count differs from the placement file')
    except Exception:
        subsystem.destroy_actor(actor)
        raise
    (RUN / 'instance-index.json').write_text(json.dumps(index), encoding='utf-8')
    saved = None
    if save:
        saved = {'level': bool(unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()),
                 'dirty_packages': bool(unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True))}
    after = counts()
    return {'before': before, 'after': after, 'saved': saved,
            'osm_group_unchanged': before['osm_per_group'] == after['osm_per_group']}


def rollback(save=True):
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    removed = 0
    for a in _canopy_actors():
        sub.destroy_actor(a)
        removed += 1
    saved = None
    if save:
        saved = {'level': bool(unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()),
                 'dirty_packages': bool(unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True))}
    return {'removed_groups': removed, 'saved': saved, 'counts': counts()}


def set_visible(visible):
    """Toggle the group without deleting it (editor visibility and in-game hidden flag)."""
    out = []
    for a in _canopy_actors():
        a.set_is_temporarily_hidden_in_editor(not visible)
        comp = a.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        if comp:
            comp.set_visibility(bool(visible))
            comp.set_hidden_in_game(not visible)
        out.append({'label': a.get_actor_label(), 'hidden_in_editor': bool(a.is_temporarily_hidden_in_editor())})
    return out


def verify(placement_name='placement.json', sample=20, seed=20261121):
    """Re-read the saved group and check each sampled instance against its source row and a fresh ground trace."""
    data = json.loads((RUN / placement_name).read_text(encoding='utf-8'))
    index = json.loads((RUN / 'instance-index.json').read_text(encoding='utf-8'))
    actors = _canopy_actors()
    if len(actors) != 1:
        return {'canopy_groups': len(actors), 'passed': False}
    actor = actors[0]
    comp = actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
    by_id = {r['id']: r for r in data['placed']}
    rows = []
    for rid in cu.random_sample(index, sample, seed):
        i = index.index(rid)
        t = comp.get_instance_transform(i, True)
        loc = t.translation
        z_ground, surface = _trace_z(loc.x, loc.y)
        src = by_id[rid]
        base = float(loc.z)
        rows.append({'id': rid, 'instance': i, 'source_lon_lat': [src['lon'], src['lat']], 'source_local_m': [src['local_e_m'], src['local_n_m']],
                     'unreal_xyz_cm': [round(loc.x, 2), round(loc.y, 2), round(base, 2)], 'ground_z_cm': None if z_ground is None else round(z_ground, 2),
                     'ground_surface': surface, 'height_m': src['height_m'], 'scale': round(float(t.scale3d.z), 4),
                     'matches_plan_xy': abs(loc.x - src['unreal_x_cm']) < 0.5 and abs(loc.y - src['unreal_y_cm']) < 0.5,
                     'base_minus_ground_cm': None if z_ground is None else round(base - z_ground, 3),
                     'floating_or_sunken': None if z_ground is None else abs(base - z_ground) >= 10.0})
    # Full-population check, cheap: every instance against a fresh trace.
    worst, bad = 0.0, 0
    for i, rid in enumerate(index):
        loc = comp.get_instance_transform(i, True).translation
        z_ground, _ = _trace_z(loc.x, loc.y)
        d = 1e9 if z_ground is None else abs(loc.z - z_ground)
        worst = max(worst, d)
        bad += d >= 10.0
    return {'canopy_groups': 1, 'instances': int(actor.get_street_instance_count()), 'plan_instances': len(data['placed']),
            'sample': rows, 'all_instances_checked': len(index), 'worst_abs_base_minus_ground_cm': round(worst, 3),
            'floating_or_sunken_count': int(bad), 'tags': [str(t) for t in actor.tags], 'label': actor.get_actor_label(),
            'passed': int(actor.get_street_instance_count()) == len(data['placed']) and bad == 0 and all(r['matches_plan_xy'] for r in rows)}


def status():
    return {'basis': _basis_dict(), 'counts': counts()}


def trace(points_xy_cm):
    out = []
    for x, y in points_xy_cm:
        z, label = _trace_z(x, y)
        out.append({'xy_cm': [x, y], 'hit': z is not None, 'z_cm': z, 'actor': label})
    return out


def los(segments):
    """Visibility check for camera planning: each segment {'a': [x,y,z], 'b': [x,y,z]} in Unreal cm."""
    out = []
    for s in segments:
        a, b = s['a'], s['b']
        h = unreal.SystemLibrary.line_trace_single(_world(), unreal.Vector(*a), unreal.Vector(*b), unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
                                                   False, [], unreal.DrawDebugTrace.NONE, True)
        if h is None:
            out.append({'blocked': False, 'by': None})
            continue
        t = h.to_tuple() if hasattr(h, 'to_tuple') else h
        actor = t[9] if len(t) > 9 and t[0] else None
        out.append({'blocked': bool(t[0]), 'by': actor.get_actor_label() if actor else None})
    return out


def reload_map():
    """Reload the saved city map from disk (persistence check); World Partition actors come back unloaded."""
    sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    before = {'world': _world().get_path_name(), 'dirty': bool(unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages())}
    ok = sub.load_level(city_world.MAP)
    return {'before': before, 'load_level_ok': bool(ok), 'world_after': _world().get_path_name(),
            'actors_resident_after_reload': len(city_world._actors()),
            'canopy_resident_after_reload': len(_canopy_actors())}


def game_view(on=True):
    """Editor Game View hides the editor grid and icons so captures show only scene content."""
    sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    sub.editor_set_game_view(bool(on))
    return {'game_view': bool(on)}


def mesh_info(path):
    m = unreal.load_asset(path)
    b = m.get_bounding_box()
    ns = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).get_nanite_settings(m)
    return {'path': path, 'min': [b.min.x, b.min.y, b.min.z], 'max': [b.max.x, b.max.y, b.max.z],
            'nanite': bool(ns.get_editor_property('enabled')), 'shape_preservation': str(ns.get_editor_property('shape_preservation')),
            'slots': [str(s.material_slot_name) for s in m.static_materials]}


def actor_info(label):
    for a in city_world._actors():
        if a.get_actor_label() == label:
            comp = a.get_component_by_class(unreal.StaticMeshComponent)
            origin, extent = a.get_actor_bounds(False)
            row = {'label': label, 'class': a.get_class().get_name(), 'origin': [origin.x, origin.y, origin.z],
                   'extent': [extent.x, extent.y, extent.z], 'tags': [str(t) for t in a.tags]}
            if comp and comp.static_mesh:
                mesh = comp.static_mesh
                row.update({'collision': str(comp.get_collision_enabled()), 'mesh': mesh.get_path_name(),
                            'visible': bool(comp.is_visible()), 'tris': int(mesh.get_num_triangles(0))})
            return row
    return None


ACTIONS = {'load_all': load_all, 'counts': counts, 'prepare': prepare, 'apply': apply, 'rollback': rollback,
           'set_visible': set_visible, 'verify': verify, 'status': status, 'trace': trace, 'actor_info': actor_info, 'tree_descs': tree_descs, 'mesh_info': mesh_info, 'los': los, 'game_view': game_view, 'reload_map': reload_map}
