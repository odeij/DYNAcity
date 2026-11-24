"""Editor side of the FABDEM terrain extension (stage 2): inspect the installed GLO-30 extension, install a FABDEM
replacement as a NEW mesh next to it, switch between the two, and measure seam and road contact.

Runs through ``open_data_tools.OpenDataTools.terrain``.  The GLO-30 extension actor and mesh are never edited or
deleted; ``switch`` only changes which one is visible and has collision, so rollback is one call.
The editor's Python has no numpy: arrays travel as raw little-endian files (float64 vertices, int32 triangles).
"""
import json
import struct
from array import array
from pathlib import Path

import unreal

import city_world

RUN = Path('C:/private/dynacity-data/open-sources-20261120/terrain-unreal-run')
OLD_LABEL = 'StreetCover_TerrainExtension'
GAP_LABEL = 'StreetCover_TerrainGapRepair'
GROUND_LABEL = 'source-ground'


def _find(label):
    for a in city_world._actors():
        if a.get_actor_label() == label:
            return a
    return None


def _mesh_arrays(mesh, lod=0):
    """All sections of a static mesh: flat vertex list [(x,y,z)...] in mesh space (cm) and flat triangle index list."""
    verts, tris, base = [], [], 0
    for section in range(mesh.get_num_sections(lod)):
        v, t, _n, _uv, _tan = unreal.ProceduralMeshLibrary.get_section_from_static_mesh(mesh, lod, section)
        verts.extend((p.x, p.y, p.z) for p in v)
        tris.extend(base + int(i) for i in t)
        base += len(v)
    return verts, tris


def dump_actor(label, stem):
    """World-space vertices/triangles of a static-mesh actor to <stem>.verts.f64 / <stem>.tris.i32, plus summary numbers."""
    a = _find(label)
    if a is None:
        return {'label': label, 'found': False}
    comp = a.get_component_by_class(unreal.StaticMeshComponent)
    mesh = comp.static_mesh
    v, t = _mesh_arrays(mesh)
    tr = a.get_actor_transform()
    loc, sc = tr.translation, tr.scale3d
    rot = tr.rotation.rotator()
    world = [(x * sc.x + loc.x, y * sc.y + loc.y, z * sc.z + loc.z) for x, y, z in v]
    RUN.mkdir(parents=True, exist_ok=True)
    flat = array('d', [c for p in world for c in p])
    (RUN / (stem + '.verts.f64')).write_bytes(flat.tobytes())
    (RUN / (stem + '.tris.i32')).write_bytes(array('i', t).tobytes())
    ns = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem).get_nanite_settings(mesh)
    area = 0.0
    for i in range(0, len(t), 3):
        a0, a1, a2 = world[t[i]], world[t[i + 1]], world[t[i + 2]]
        area += abs((a1[0] - a0[0]) * (a2[1] - a0[1]) - (a2[0] - a0[0]) * (a1[1] - a0[1])) / 2.0
    xs, ys, zs = [p[0] for p in world], [p[1] for p in world], [p[2] for p in world]
    return {'label': label, 'found': True, 'mesh': mesh.get_path_name(), 'vertices': len(v), 'triangles': len(t) // 3,
            'rotation': [rot.pitch, rot.yaw, rot.roll], 'scale': [sc.x, sc.y, sc.z], 'location': [loc.x, loc.y, loc.z],
            'xy_bounds_cm': [min(xs), min(ys), max(xs), max(ys)], 'z_range_cm': [min(zs), max(zs)], 'plan_area_m2': round(area / 1e4, 1),
            'nanite': bool(ns.get_editor_property('enabled')), 'collision': str(comp.get_collision_enabled()),
            'visible': bool(comp.is_visible()), 'material': comp.get_material(0).get_path_name() if comp.get_material(0) else None}


NEW_LABEL = 'StreetCover_TerrainExtensionFabdem'
NEW_FOLDER = '/Game/Beirut/TerrainExtensionFabdem'
FOLDER_PATH = 'DynaCity/Mapped ground/Shared city terrain'
ACTIVE_PROFILE = 'BlockAll'  # the profile of the installed extension and the other terrain meshes
SOURCE_TAGS = ['DYNACITY_STREET_COVER', 'cover_kind:terrain_extension_fabdem', 'FABDEM_V1_2_CC_BY_NC_SA_4_0']


def _comp(actor):
    return actor.get_component_by_class(unreal.StaticMeshComponent)


def status():
    out = {}
    for key, label in (('glo30', OLD_LABEL), ('fabdem', NEW_LABEL)):
        a = _find(label)
        if a is None:
            out[key] = None
            continue
        c = _comp(a)
        out[key] = {'label': label, 'visible': bool(c.is_visible()), 'collision': str(c.get_collision_enabled()),
                    'collision_profile': str(c.get_collision_profile_name()),
                    'hidden_in_editor': bool(a.is_temporarily_hidden_in_editor()), 'mesh': c.static_mesh.get_path_name() if c.static_mesh else None,
                    'path': a.get_path_name(), 'triangles_lod0': int(c.static_mesh.get_num_triangles(0)) if c.static_mesh else None}
    return out


def start_import(glb):
    """Begin the async Interchange scene import of the FABDEM extension GLB into its own content folder."""
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play first')
    if _find(NEW_LABEL) is not None:
        raise RuntimeError('FABDEM extension already installed')
    if _find(OLD_LABEL) is None:
        raise RuntimeError('The installed GLO-30 extension is not loaded; load the city geometry first')
    manager = unreal.InterchangeManager.get_interchange_manager_scripted()
    if manager.is_interchange_active():
        raise RuntimeError('Another import is active')
    params = unreal.ImportAssetParameters()
    params.is_automated = True
    params.replace_existing = False
    manager.scripted_import_scene_async(NEW_FOLDER, unreal.InterchangeManager.create_source_data(glb), params)
    return {'started': True, 'glb': glb}


def finish_import(save=True):
    """After the import: tidy the actor (detach from the import root, tags, folder), match the old extension, hide it."""
    manager = unreal.InterchangeManager.get_interchange_manager_scripted()
    if manager.is_interchange_active():
        return {'importing': True}
    actor = _find(NEW_LABEL)
    if actor is None:
        raise RuntimeError('Imported actor %s not found' % NEW_LABEL)
    old = _find(OLD_LABEL)
    actor.modify()
    parent = actor.get_attach_parent_actor()
    actor.detach_from_actor(unreal.DetachmentRule.KEEP_WORLD, unreal.DetachmentRule.KEEP_WORLD, unreal.DetachmentRule.KEEP_WORLD)
    actor.tags = list(dict.fromkeys([str(t) for t in actor.tags] + SOURCE_TAGS))
    if parent and parent.get_class().get_name() == 'InterchangeSceneActor':
        unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(parent)
    actor.set_folder_path(FOLDER_PATH)
    actor.set_editor_property('is_spatially_loaded', True)
    comp, old_comp = _comp(actor), _comp(old)
    material = old_comp.get_material(0)
    comp.set_material(0, material)
    comp.set_cast_shadow(bool(old_comp.cast_shadow))
    # Start hidden and without collision: the installed GLO-30 extension stays the active one until switch('fabdem').
    comp.set_visibility(False)
    comp.set_collision_profile_name('NoCollision')
    actor.set_is_temporarily_hidden_in_editor(True)
    mesh = comp.static_mesh
    editor = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    old_nanite = editor.get_nanite_settings(old_comp.static_mesh).get_editor_property('enabled')
    saved = None
    if save:
        saved = bool(unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True))
    return {'installed': True, 'actor': actor.get_path_name(), 'mesh': mesh.get_path_name(), 'material': material.get_path_name() if material else None,
            'triangles_lod0': int(mesh.get_num_triangles(0)), 'old_mesh_nanite_enabled': bool(old_nanite), 'saved': saved, 'status': status()}


def remove_fabdem(save=True):
    """Delete ONLY the FABDEM actor and its imported assets (to re-import a rebuilt GLB). Refuses unless GLO-30 is the active one."""
    old, new = _find(OLD_LABEL), _find(NEW_LABEL)
    if old is None:
        raise RuntimeError('GLO-30 extension not loaded')
    if not _comp(old).is_visible() or str(_comp(old).get_collision_profile_name()) != ACTIVE_PROFILE:
        raise RuntimeError('Switch to glo30 first; refusing to remove the FABDEM actor while it may be the active surface')
    removed_actor = False
    if new is not None:
        unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(new)
        removed_actor = True
    deleted = bool(unreal.EditorAssetLibrary.delete_directory(NEW_FOLDER)) if unreal.EditorAssetLibrary.does_directory_exist(NEW_FOLDER) else None
    saved = bool(unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)) if save else None
    return {'removed_actor': removed_actor, 'deleted_asset_folder': deleted, 'saved': saved, 'status': status()}


def switch(to, save=True):
    """Make exactly one extension active. Only visibility and collision flags change; no mesh is edited or deleted."""
    if to not in ('glo30', 'fabdem'):
        raise ValueError(to)
    old, new = _find(OLD_LABEL), _find(NEW_LABEL)
    if old is None or new is None:
        raise RuntimeError('Both extensions must be loaded to switch')
    old_comp, new_comp = _comp(old), _comp(new)
    for actor, comp, active in ((old, old_comp, to == 'glo30'), (new, new_comp, to == 'fabdem')):
        actor.modify()
        comp.set_visibility(active)
        # The collision PROFILE is what survives a save/reload (set_collision_enabled alone is reset to the profile on load).
        comp.set_collision_profile_name(ACTIVE_PROFILE if active else 'NoCollision')
        actor.set_is_temporarily_hidden_in_editor(not active)
    saved = bool(unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)) if save else None
    return {'active': to, 'saved': saved, 'status': status()}


def _trace(world, x, y, complex_=False, ignore=None):
    h = unreal.SystemLibrary.line_trace_single(world, unreal.Vector(x, y, 60000.), unreal.Vector(x, y, -30000.),
                                               unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, bool(complex_), list(ignore or []), unreal.DrawDebugTrace.NONE, True)
    if h is None:
        return None, None
    t = h.to_tuple() if hasattr(h, 'to_tuple') else h
    if not t[0]:
        return None, None
    actor = t[9] if len(t) > 9 else None
    return float(t[4].z), (actor.get_actor_label() if actor else None)


def trace(points_xy_cm, complex_=False, ignore_prefixes=None, only_prefixes=None):
    """First blocking hit straight down at each point.

    ``ignore_prefixes``: actors whose label starts with one of these are skipped.
    ``only_prefixes``: every static-mesh actor whose label does NOT start with one of these is skipped (isolates a surface).
    """
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    ignore = []
    for a in city_world._actors():
        label = a.get_actor_label()
        if ignore_prefixes and label.startswith(tuple(ignore_prefixes)):
            ignore.append(a)
        elif only_prefixes and not label.startswith(tuple(only_prefixes)):
            ignore.append(a)
    out = []
    for x, y in points_xy_cm:
        z, label = _trace(world, x, y, complex_, ignore)
        out.append([z, label])
    return out


def reload_and_load():
    """Persistence check: reload the saved map, then load the World Partition actors the terrain checks need."""
    sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    ok = sub.load_level(city_world.MAP)
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    resident = len(city_world._actors())
    wanted = [d.guid for d in descs if str(d.label).startswith(('StreetCover_Terrain', 'source-', 'terrain', 'roads', 'buildings', 'Street'))]
    if wanted:
        unreal.WorldPartitionBlueprintLibrary.load_actors(wanted)
    return {'load_level_ok': bool(ok), 'resident_right_after_reload': resident, 'loaded_after': len(city_world._actors()), 'status': status()}


def game_view(on=True):
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_set_game_view(bool(on))
    return {'game_view': bool(on)}


ACTIONS = {'dump_actor': dump_actor, 'status': status, 'start_import': start_import, 'finish_import': finish_import, 'switch': switch, 'remove_fabdem': remove_fabdem,
           'trace': trace, 'reload_and_load': reload_and_load, 'game_view': game_view}
