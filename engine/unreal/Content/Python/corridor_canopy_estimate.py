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

import importlib

import canopy_unreal as cu
import city_world
import explore_city

importlib.reload(cu)  # the dispatcher reloads this module on every call; keep the pure planning module in step

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
    # Rows may name their own mesh (assign_meshes); otherwise every row uses the file's mesh (or the override).
    default_mesh = mesh_path or data['mesh']
    groups = {}
    for r in data['placed']:
        groups.setdefault(r.get('mesh') or default_mesh, []).append(r)
    for path in groups:
        box = unreal.load_asset(path).get_bounding_box()
        if abs(box.min.z) > 1e-3:
            raise RuntimeError('Tree pivot is not at the trunk base: ' + path)
    cls = _cls()
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    spawned, index = [], []
    try:
        for path, rows in groups.items():
            actor = subsystem.spawn_actor_from_class(cls, unreal.Vector())
            spawned.append(actor)
            label = cu.LABEL_PREFIX if len(groups) == 1 else '%s - %s' % (cu.LABEL_PREFIX, path.rsplit('/', 1)[-1].split('.')[0])
            actor.modify()
            actor.set_actor_label(label)
            actor.set_folder_path(FOLDER)
            actor.tags = list(cu.TAGS)
            actor.set_editor_property('is_spatially_loaded', True)
            actor.set_editor_property('layer_kind', 'tree_conifer' if 'conifer' in path else 'tree_broadleaf')
            actor.set_editor_property('production_area', 'Corridor canopy estimate')
            actor.set_editor_property('source_manifest', data['plan'])
            actor.set_street_mesh(unreal.load_asset(path))
            actor.clear_street_instances()
            actor.set_street_collision(False)
            actor.set_street_cull_distance(18000, 50000)
            ids = []
            for r in rows:
                t = unreal.Transform(location=unreal.Vector(r['unreal_x_cm'], r['unreal_y_cm'], r['ground_z_cm']),
                                     # unreal.Rotator takes (roll, pitch, yaw): pass yaw by keyword. Round 1 passed it positionally,
                                     # which set PITCH to the yaw angle and tilted every tree by an arbitrary 0-360 degrees.
                                     rotation=unreal.Rotator(roll=0.0, pitch=0.0, yaw=float(r['yaw_deg'])),
                                     scale=unreal.Vector(r['scale'], r['scale'], r['scale']))
                if actor.add_street_instance(t, r['id']) < 0:
                    raise RuntimeError('Could not add instance ' + r['id'])
                ids.append(r['id'])
            if actor.get_street_instance_count() != len(rows):
                raise RuntimeError('Instance count differs from the placement file')
            index.append({'label': label, 'mesh': path, 'ids': ids})
    except Exception:
        for a in spawned:
            subsystem.destroy_actor(a)
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
    if index and not isinstance(index[0], dict):
        index = [{'label': cu.LABEL_PREFIX, 'mesh': data['mesh'], 'ids': index}]       # round-1 single-group format
    actors = {a.get_actor_label(): a for a in _canopy_actors()}
    if sorted(actors) != sorted(g['label'] for g in index):
        return {'canopy_groups': len(actors), 'labels': sorted(actors), 'expected_labels': [g['label'] for g in index], 'passed': False}
    by_id = {r['id']: r for r in data['placed']}
    entries = []                                      # (actor component, instance index, id)
    total_instances, mesh_counts = 0, {}
    for g in index:
        actor = actors[g['label']]
        comp = actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        total_instances += int(actor.get_street_instance_count())
        mesh_counts[comp.static_mesh.get_path_name()] = int(actor.get_street_instance_count())
        entries.extend((comp, i, rid) for i, rid in enumerate(g['ids']))
    rows = []
    for comp, i, rid in cu.random_sample(entries, sample, seed):
        t = comp.get_instance_transform(i, True)
        loc = t.translation
        z_ground, surface = _trace_z(loc.x, loc.y)
        src = by_id[rid]
        base = float(loc.z)
        rows.append({'id': rid, 'instance': i, 'mesh': comp.static_mesh.get_path_name(), 'source_lon_lat': [src['lon'], src['lat']],
                     'source_local_m': [src['local_e_m'], src['local_n_m']],
                     'unreal_xyz_cm': [round(loc.x, 2), round(loc.y, 2), round(base, 2)], 'ground_z_cm': None if z_ground is None else round(z_ground, 2),
                     'ground_surface': surface, 'height_m': src['height_m'], 'scale': round(float(t.scale3d.z), 4),
                     'matches_plan_xy': abs(loc.x - src['unreal_x_cm']) < 0.5 and abs(loc.y - src['unreal_y_cm']) < 0.5,
                     'base_minus_ground_cm': None if z_ground is None else round(base - z_ground, 3),
                     'floating_or_sunken': None if z_ground is None else abs(base - z_ground) >= 10.0})
    # Full-population check, cheap: every instance against a fresh trace.
    worst, bad, max_tilt = 0.0, 0, 0.0
    for comp, i, rid in entries:
        tr = comp.get_instance_transform(i, True)
        loc = tr.translation
        rot = tr.rotation.rotator()
        max_tilt = max(max_tilt, abs(rot.pitch), abs(rot.roll))   # instance tilt away from vertical, degrees (yaw is free)
        z_ground, _ = _trace_z(loc.x, loc.y)
        d = 1e9 if z_ground is None else abs(loc.z - z_ground)
        worst = max(worst, d)
        bad += d >= 10.0
    return {'canopy_groups': len(actors), 'instances': total_instances, 'plan_instances': len(data['placed']), 'instances_per_mesh': mesh_counts,
            'sample': rows, 'all_instances_checked': len(entries), 'worst_abs_base_minus_ground_cm': round(worst, 3),
            'floating_or_sunken_count': int(bad), 'max_instance_tilt_deg': round(max_tilt, 4), 'tags': sorted({str(t) for a in actors.values() for t in a.tags}), 'labels': sorted(actors),
            'passed': total_instances == len(data['placed']) and bad == 0 and all(r['matches_plan_xy'] for r in rows)}


def assign_meshes(mesh_paths, src='placement.json', out='placement2.json'):
    """Re-scale and assign meshes to the SAME placed candidates (positions, ground Z, yaw, skip results unchanged).

    With several meshes the choice is a stable hash of the source id (sha256 of id + '|mesh'), never random.  The scale of
    each row is its estimated crown-top height divided by that mesh's height."""
    data = json.loads((RUN / src).read_text(encoding='utf-8'))
    heights = {}
    for p in mesh_paths:
        box = unreal.load_asset(p).get_bounding_box()
        if abs(box.min.z) > 1e-3:
            raise RuntimeError('Pivot is not at the trunk base: ' + p)
        heights[p] = float(box.max.z - box.min.z)
    counts_ = Counter()
    for r in data['placed']:
        p = mesh_paths[cu.stable_choice(r['id'], len(mesh_paths))]
        r['mesh'] = p
        r['scale'] = round(cu.mesh_scale(r['height_m'], heights[p]), 4)
        counts_[p] += 1
    data['mesh'] = mesh_paths[0]
    data['meshes'] = {p: {'height_cm': heights[p], 'assigned': counts_[p]} for p in mesh_paths}
    data['mesh_assignment'] = 'sha256(id + "|mesh") mod %d' % len(mesh_paths)
    (RUN / out).write_text(json.dumps(data), encoding='utf-8')
    scales = [r['scale'] for r in data['placed']]
    return {'placed': len(data['placed']), 'assigned': dict(counts_), 'scale_range': [min(scales), max(scales)], 'out': out}


def status():
    return {'basis': _basis_dict(), 'counts': counts()}


def trace(points_xy_cm):
    out = []
    for x, y in points_xy_cm:
        z, label = _trace_z(x, y)
        out.append({'xy_cm': [x, y], 'hit': z is not None, 'z_cm': z, 'actor': label})
    return out


TREE_WORDS = ('tree', 'palm', 'conifer', 'pine', 'cypress', 'olive', 'oak', 'poplar', 'shrub', 'bush', 'foliage', 'plant', 'fig', 'eucalypt')


def list_tree_meshes(words=TREE_WORDS, roots=('/Game',)):
    """Static meshes in the project whose asset name contains a tree-like word (street covers excluded); read-only survey."""
    out = []
    for root in roots:
        for p in unreal.EditorAssetLibrary.list_assets(root, recursive=True, include_folder=False):
            name = p.lower().rsplit('/', 1)[-1]
            if 'streetcover' in p.lower() or 'street_' in name:
                continue
            if any(w in name for w in words):
                a = unreal.load_asset(p)
                if isinstance(a, unreal.StaticMesh):
                    b = a.get_bounding_box()
                    out.append({'path': p, 'size_cm': [round(b.max.x - b.min.x, 1), round(b.max.y - b.min.y, 1), round(b.max.z - b.min.z, 1)],
                                'min': [round(b.min.x, 1), round(b.min.y, 1), round(b.min.z, 1)], 'tris_lod0': int(a.get_num_triangles(0))})
    return out


def mesh_lean(path, base_fraction=0.06, crown_fraction=0.25):
    """Lean of a tree mesh from its LOD0 vertices: horizontal offset between the trunk-base centroid (lowest slice)
    and the centroid of the vertices in the top slice, over their height difference, as an angle. Also the horizontal
    extent of the bottom slice (debris on the ground widens it) and the pivot-to-crown offset."""
    import math
    m = unreal.load_asset(path)
    verts = []
    for s in range(m.get_num_sections(0)):
        v = unreal.ProceduralMeshLibrary.get_section_from_static_mesh(m, 0, s)[0]
        verts.extend((p.x, p.y, p.z) for p in v)
    zs = [v[2] for v in verts]
    z0, z1 = min(zs), max(zs)
    h = z1 - z0
    base = [v for v in verts if v[2] <= z0 + base_fraction * h]
    top = [v for v in verts if v[2] >= z1 - crown_fraction * h]
    bx = sum(v[0] for v in base) / len(base)
    by = sum(v[1] for v in base) / len(base)
    tx = sum(v[0] for v in top) / len(top)
    ty = sum(v[1] for v in top) / len(top)
    tz = sum(v[2] for v in top) / len(top)
    bz = sum(v[2] for v in base) / len(base)
    horizontal = math.hypot(tx - bx, ty - by)
    base_spread = max(math.hypot(v[0] - bx, v[1] - by) for v in base)
    return {'path': path, 'height_cm': round(h, 1), 'vertices': len(verts), 'base_centroid_xy_cm': [round(bx, 1), round(by, 1)],
            'crown_centroid_xy_cm': [round(tx, 1), round(ty, 1)], 'pivot_to_crown_offset_cm': round(math.hypot(tx, ty), 1),
            'lean_deg': round(math.degrees(math.atan2(horizontal, tz - bz)), 2), 'base_slice_max_radius_cm': round(base_spread, 1),
            'base_slice_vertices': len(base)}


def trunk_lean(path):
    """Per material section: lean of the lowest 55 percent of that section's vertices (a trunk leans visibly there), the
    horizontal extent of everything within 25 cm of the ground (debris at the base), and the section's bounds.
    Lean = angle from vertical of the line between the centroids of the 0-12 percent and 35-50 percent height slices."""
    import math
    m = unreal.load_asset(path)
    slots = [str(s.material_slot_name) for s in m.static_materials]
    allv = []
    secs = []
    for s in range(m.get_num_sections(0)):
        v = unreal.ProceduralMeshLibrary.get_section_from_static_mesh(m, 0, s)[0]
        pts = [(p.x, p.y, p.z) for p in v]
        secs.append(pts)
        allv.extend(pts)
    z0 = min(p[2] for p in allv)
    z1 = max(p[2] for p in allv)
    h = z1 - z0
    out = {'path': path, 'height_cm': round(h, 1), 'slots': slots, 'sections': []}
    for i, pts in enumerate(secs):
        if not pts:
            continue
        lo = [p for p in pts if p[2] <= z0 + .12 * h]
        mid = [p for p in pts if z0 + .35 * h <= p[2] <= z0 + .50 * h]
        row = {'slot': slots[i] if i < len(slots) else str(i), 'vertices': len(pts), 'z_range_cm': [round(min(p[2] for p in pts) - z0, 1), round(max(p[2] for p in pts) - z0, 1)]}
        if lo and mid:
            cl = (sum(p[0] for p in lo) / len(lo), sum(p[1] for p in lo) / len(lo), sum(p[2] for p in lo) / len(lo))
            cm = (sum(p[0] for p in mid) / len(mid), sum(p[1] for p in mid) / len(mid), sum(p[2] for p in mid) / len(mid))
            row['lean_deg_0_12_to_35_50_pct'] = round(math.degrees(math.atan2(math.hypot(cm[0] - cl[0], cm[1] - cl[1]), cm[2] - cl[2])), 1)
        ground = [p for p in pts if p[2] <= z0 + 25]
        row['vertices_within_25cm_of_ground'] = len(ground)
        row['max_horizontal_reach_at_ground_cm'] = round(max((math.hypot(p[0], p[1]) for p in ground), default=0.0), 1)
        out['sections'].append(row)
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
           'set_visible': set_visible, 'verify': verify, 'status': status, 'trace': trace, 'actor_info': actor_info, 'tree_descs': tree_descs, 'mesh_info': mesh_info, 'los': los, 'list_tree_meshes': list_tree_meshes, 'mesh_lean': mesh_lean, 'trunk_lean': trunk_lean, 'assign_meshes': assign_meshes, 'game_view': game_view, 'reload_map': reload_map}
