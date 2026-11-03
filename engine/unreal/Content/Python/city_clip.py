"""Keep the city map to municipal Beirut: remove or cut everything outside it.

The boundary arrives as a 5 m inside/outside mask in Unreal centimetres
(`dynacity.engine_city_clip`). Per actor:
- source building batches are kept whole (they are already assigned to the
  12 areas); `buildings_context_*` batches are left to their own rebuild;
- street instance groups keep only instances whose location is inside;
- other static meshes fully outside are deleted, fully inside kept, and meshes
  crossing the line lose the triangles whose centre is outside.
Cut edges follow triangles, not the exact line. Nothing is moved or re-heighted.
"""
import base64
import json
from collections import Counter, defaultdict
from pathlib import Path

import unreal

SKIP_CLASSES = {'DirectionalLight', 'SkyLight', 'SkyAtmosphere', 'PostProcessVolume', 'WorldDataLayers',
                'WorldPartitionMiniMap', 'DynaDriveStart', 'LidarPointCloudActor', 'PointLight'}


class Mask:
    def __init__(self, path):
        data = json.loads(Path(path).read_text())
        self.x0, self.y0, self.cell = data['x0'], data['y0'], data['cell_cm']
        self.width, self.height = data['width'], data['height']
        self.bits = base64.b64decode(data['mask_b64'])

    def inside(self, x, y):
        c = int((x - self.x0) // self.cell); r = int((y - self.y0) // self.cell)
        if c < 0 or r < 0 or c >= self.width or r >= self.height:
            return False
        i = r * self.width + c
        return bool((self.bits[i >> 3] >> (7 - (i & 7))) & 1)

    def box(self, low, high):
        """'in', 'out' or 'cross' for an XY box, by every mask cell it covers."""
        c0 = max(0, int((low[0] - self.x0) // self.cell)); c1 = min(self.width - 1, int((high[0] - self.x0) // self.cell))
        r0 = max(0, int((low[1] - self.y0) // self.cell)); r1 = min(self.height - 1, int((high[1] - self.y0) // self.cell))
        beyond = low[0] < self.x0 or low[1] < self.y0 or c1 < int((high[0] - self.x0) // self.cell) or r1 < int((high[1] - self.y0) // self.cell)
        seen = set()
        if beyond:
            seen.add(False)
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                i = r * self.width + c
                seen.add(bool((self.bits[i >> 3] >> (7 - (i & 7))) & 1))
                if len(seen) == 2:
                    return 'cross'
        return 'in' if seen == {True} else 'out'


def _identity(xf):
    t, r, s = xf.translation, xf.rotation, xf.scale3d
    return (abs(t.x) + abs(t.y) + abs(t.z) < 1e-3 and abs(r.x) + abs(r.y) + abs(r.z) < 1e-6
            and abs(s.x - 1) + abs(s.y - 1) + abs(s.z - 1) < 1e-6)


def _cut(mask, component, dry):
    """Delete triangles whose centre is outside; returns (kept, removed)."""
    Q, L = unreal.GeometryScript_MeshQueries, unreal.GeometryScript_List
    mesh = component.static_mesh
    dm = unreal.DynamicMesh()
    dm, _ = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(
        mesh, dm, unreal.GeometryScriptCopyMeshFromAssetOptions(), unreal.GeometryScriptMeshReadLOD())
    _, positions, _ = Q.get_all_vertex_positions(dm, False)
    _, triangles, _ = Q.get_all_triangle_indices(dm, False)
    xf = component.get_world_transform()
    if not _identity(xf):
        positions.vector_transform_in_place(xf)
    p = L.convert_vector_list_to_array(positions)
    tris = L.convert_triangle_list_to_array(triangles)
    outside, kept = [], 0
    for tid, t in enumerate(tris):
        a, b, c = t.x, t.y, t.z
        if a < 0:
            continue
        x = (p[a].x + p[b].x + p[c].x) / 3; y = (p[a].y + p[b].y + p[c].y) / 3
        if mask.inside(x, y):
            kept += 1
        else:
            outside.append(tid)
    if dry or not outside or not kept:
        return kept, len(outside)
    dm, _ = unreal.GeometryScript_MeshEdits.delete_triangles_from_mesh(dm, L.convert_array_to_index_list(outside))
    options = unreal.GeometryScriptCopyMeshToAssetOptions()
    options.enable_recompute_normals = False; options.enable_recompute_tangents = False
    options.enable_remove_degenerates = False; options.replace_materials = False
    unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh(dm, mesh, options, unreal.GeometryScriptMeshWriteLOD(), True)
    unreal.EditorAssetLibrary.save_loaded_asset(mesh)
    # Large meshes (the terrain extension) otherwise exhaust memory across many cuts.
    del dm, positions, triangles, p, tris
    unreal.SystemLibrary.collect_garbage()
    return kept, len(outside)


def _filter_street_instances(mask, actor, dry):
    component = actor.get_editor_property('instances')
    ids = list(actor.get_editor_property('source_ids'))
    xfs = [component.get_instance_transform(i, True) for i in range(component.get_instance_count())]
    keep = [i for i, xf in enumerate(xfs) if mask.inside(xf.translation.x, xf.translation.y)]
    if dry or len(keep) == len(xfs) or not keep:
        return len(keep), len(xfs) - len(keep)
    actor.modify()
    actor.clear_street_instances()
    for i in keep:
        actor.add_street_instance(xfs[i], ids[i] if i < len(ids) else '')
    return len(keep), len(xfs) - len(keep)


def _filter_instances(mask, component, dry):
    n = component.get_instance_count()
    out = [i for i in range(n) if not mask.inside(*(lambda t: (t.x, t.y))(component.get_instance_transform(i, True).translation))]
    if not dry and out and len(out) < n:
        component.modify(); component.remove_instances(out)
    return n - len(out), len(out)


def clip(mask_path, report_path, dry=True):
    import city_world
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before clipping the city')
    mask = Mask(mask_path)
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    users = defaultdict(list)
    for actor in actors:
        for comp in actor.get_components_by_class(unreal.StaticMeshComponent):
            if comp.static_mesh and not isinstance(comp, unreal.InstancedStaticMeshComponent):
                users[comp.static_mesh.get_path_name()].append(actor.get_actor_label())
    rows, counts, destroy = [], Counter(), []
    # Biggest meshes last, so a memory failure leaves the most work already saved.
    actors = sorted(actors, key=lambda a: a.get_actor_label() in ('StreetCover_TerrainExtension', 'source-ground', 'beirut001_transport-structures'))
    for actor in actors:
        label = actor.get_actor_label(); kind = actor.get_class().get_name()
        if kind in SKIP_CLASSES or label.startswith('DYNACITY_BASIS_'):
            continue
        if label.startswith('buildings_'):
            counts['building batches left to their own layer'] += 1
            continue
        if kind == 'DynaStreetInstances':
            kept, removed = _filter_street_instances(mask, actor, dry)
            action = 'kept' if not removed else 'deleted' if not kept else 'instances filtered'
            if action == 'deleted':
                destroy.append(actor)
            rows.append({'label': label, 'action': action, 'kept': kept, 'removed': removed}); counts[action] += 1
            continue
        meshes = [c for c in actor.get_components_by_class(unreal.StaticMeshComponent) if c.static_mesh]
        if not meshes:
            continue
        states = []
        for comp in meshes:
            if isinstance(comp, unreal.InstancedStaticMeshComponent):
                kept, removed = _filter_instances(mask, comp, dry)
                states.append('out' if not kept else 'in' if not removed else 'cross')
                rows.append({'label': label, 'action': 'instances filtered' if removed and kept else 'checked', 'kept': kept, 'removed': removed})
                continue
            origin, extent, _ = unreal.SystemLibrary.get_component_bounds(comp)
            state = mask.box((origin.x - extent.x, origin.y - extent.y), (origin.x + extent.x, origin.y + extent.y))
            if state == 'cross':
                if len(set(users[comp.static_mesh.get_path_name()])) > 1:
                    rows.append({'label': label, 'action': 'skipped: mesh shared', 'mesh': comp.static_mesh.get_path_name()})
                    counts['skipped: mesh shared'] += 1; states.append('in'); continue
                kept, removed = _cut(mask, comp, dry)
                state = 'out' if not kept else 'in' if not removed else 'cross'
                rows.append({'label': label, 'action': 'cut', 'triangles_kept': kept, 'triangles_removed': removed})
                counts['cut'] += 1
            states.append(state)
        if all(s == 'out' for s in states):
            destroy.append(actor); rows.append({'label': label, 'action': 'deleted'}); counts['deleted'] += 1
        elif all(s == 'in' for s in states):
            counts['kept'] += 1
    if not dry:
        subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        for actor in destroy:
            subsystem.destroy_actor(actor)
        if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():
            raise RuntimeError('City save failed after clipping')
        if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(False, True):
            raise RuntimeError('Asset save failed after clipping')
    report = {'schema': 'dynacity-city-clip/1', 'dry_run': dry, 'mask': str(mask_path), 'counts': dict(counts), 'actors': rows,
              'limits': ['Cut edges follow mesh triangles (centre test on a 5 m mask), not the exact boundary line.',
                         'Source building batches are untouched; context building batches are replaced separately.']}
    Path(report_path).write_text(json.dumps(report, indent=2))
    return report
