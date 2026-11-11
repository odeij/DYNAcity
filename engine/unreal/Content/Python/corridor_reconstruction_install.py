"""Install / roll back / verify the source-supported corridor reconstruction. Editor-side, never during Play.

Each rebuilt building loses ONLY its street-wall triangles (omission patch); roof, rear, side walls and other frontages
stay original. install() snapshots every building batch first, hides only the original component of each affected batch
(visibility only; mesh and collision untouched), spawns a visual copy of that batch with the street walls omitted, and one
reconstruction actor. verify() recomputes the comparison against the snapshot; nothing is asserted from constants.
Only touched packages are saved; there is no SaveAll.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

import unreal

sys.path.insert(0, 'C:/Users/PC/DYNAcity/src/dynacity')
import captured_buildings as cb  # noqa: E402
import city_world  # noqa: E402
import scene_compare  # noqa: E402

ROOT = '/Game/DynaCity/CorridorReconstruction'
TAG = 'DYNACITY_CORRIDOR_RECONSTRUCTION_20261102'
CREATED_PREFIXES = ('Corridor visual - ', 'Corridor reconstruction - ')
REFERENCE_VISUAL_LABEL = 'Reference visual source batch - LGB historical facade'  # hidden (visibility only) when its batch is patched
TRACKED_EXTRA_LABELS = (REFERENCE_VISUAL_LABEL,)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sibling(manifest_path, name):
    return Path(manifest_path).with_name(name)


def _component(actor):
    return actor.get_component_by_class(unreal.StaticMeshComponent)


def _state(actor):
    comp = _component(actor)
    t = actor.get_actor_transform()
    r = t.rotation.rotator()
    return {'label': actor.get_actor_label(), 'visible': bool(comp.is_visible()),
            'hidden_in_game': bool(comp.get_editor_property('hidden_in_game')),
            'mesh': comp.static_mesh.get_path_name() if comp.static_mesh else None,
            'materials': [m.get_path_name() if m else None for m in comp.get_materials()],
            'collision': str(comp.get_collision_enabled()),
            'transform': [t.translation.x, t.translation.y, t.translation.z, r.pitch, r.yaw, r.roll, t.scale3d.x, t.scale3d.y, t.scale3d.z]}


def _load_batches():
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    wanted = [d.guid for d in descs if str(d.label).startswith('buildings_')]
    if wanted:
        unreal.WorldPartitionBlueprintLibrary.load_actors(wanted)
    return len(wanted)


def snapshot():
    """Live state of every building batch actor plus anything this installer created."""
    cb._ready()
    _load_batches()
    actors = {}
    for a in city_world._actors():
        label = a.get_actor_label()
        if (label.startswith('buildings_') or label in TRACKED_EXTRA_LABELS or TAG in a.tags) and _component(a):
            actors[a.get_path_name()] = _state(a)
    return {'schema': 'dynacity-scene-snapshot/1', 'actors': actors}


def _package_file(package_name):
    """On-disk .uasset path of a /Game package (external actors included)."""
    if not package_name.startswith('/Game/'):
        raise ValueError('not a /Game package: ' + package_name)
    return str(Path(unreal.Paths.project_content_dir()) / (package_name[len('/Game/'):] + '.uasset'))


def _package_files(actors):
    return {a.get_actor_label(): _package_file(a.get_outermost().get_name()) for a in actors}


def _import(filename, folder, name):
    """Import one GLB into a folder named by the file's hash; returns the single StaticMesh path, or None while importing."""
    paths = unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False)
    meshes = [p for p in paths if isinstance(unreal.load_asset(p), unreal.StaticMesh)]
    if not meshes:
        task = unreal.AssetImportTask()
        task.filename = str(filename); task.destination_path = folder; task.destination_name = name
        task.automated = True; task.save = True; task.replace_existing = False
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
            return None
        paths = unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False)
        meshes = [p for p in paths if isinstance(unreal.load_asset(p), unreal.StaticMesh)]
    if len(meshes) != 1:
        raise RuntimeError('Expected exactly one mesh in ' + folder + ': ' + str(meshes))
    return meshes[0]


def _material(name, spec):
    """Task-owned material; the asset name carries a hash of the spec so changed values never reuse a stale asset.

    ``spec['blend'] == 'translucent'`` creates a real translucent material (blend mode, two-sided, per-pixel lit
    translucency, constant Opacity input); anything else is opaque PBR. Constants only; no textures.
    """
    tag = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:8]
    path = ROOT + '/Materials/M_' + name + '_' + tag
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.load_asset(path)
    lib = unreal.MaterialEditingLibrary
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset('M_' + name + '_' + tag, ROOT + '/Materials', unreal.Material, unreal.MaterialFactoryNew())
    mat.set_editor_property('two_sided', True)
    if spec.get('blend') == 'translucent':
        mat.set_editor_property('blend_mode', unreal.BlendMode.BLEND_TRANSLUCENT)
        mat.set_editor_property('translucency_lighting_mode', unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
    color = lib.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -300, 0)
    r, g, b, _ = spec['base_color']
    color.set_editor_property('constant', unreal.LinearColor(r, g, b, 1.))
    lib.connect_material_property(color, '', unreal.MaterialProperty.MP_BASE_COLOR)
    props = [(spec.get('roughness', .8), unreal.MaterialProperty.MP_ROUGHNESS, 150), (spec.get('metallic', 0.), unreal.MaterialProperty.MP_METALLIC, 300)]
    if spec.get('blend') == 'translucent':
        props.append((spec['opacity'], unreal.MaterialProperty.MP_OPACITY, 450))
    for value, prop, y in props:
        node = lib.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, y)
        node.set_editor_property('r', float(value))
        lib.connect_material_property(node, '', prop)
    lib.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def material_report(paths):
    """Read-only: the ACTUAL saved blend mode, sidedness, lighting mode and constant inputs of task-owned materials."""
    lib = unreal.MaterialEditingLibrary
    out = {}
    for p in paths:
        mat = unreal.load_asset(p)
        row = {'blend_mode': str(mat.get_editor_property('blend_mode')), 'two_sided': bool(mat.get_editor_property('two_sided')),
               'translucency_lighting_mode': str(mat.get_editor_property('translucency_lighting_mode')),
               'shading_model': str(mat.get_editor_property('shading_model'))}
        for label, prop in (('base_color', unreal.MaterialProperty.MP_BASE_COLOR), ('roughness', unreal.MaterialProperty.MP_ROUGHNESS),
                            ('metallic', unreal.MaterialProperty.MP_METALLIC), ('opacity', unreal.MaterialProperty.MP_OPACITY)):
            node = lib.get_material_property_input_node(mat, prop)
            if node is None:
                row[label] = None
            elif isinstance(node, unreal.MaterialExpressionConstant3Vector):
                c = node.get_editor_property('constant')
                row[label] = [round(c.r, 4), round(c.g, 4), round(c.b, 4)]
            elif isinstance(node, unreal.MaterialExpressionConstant):
                row[label] = round(float(node.get_editor_property('r')), 4)
            else:
                row[label] = type(node).__name__
        out[p.split('/')[-1]] = row
    return out


def player_start_icons(hidden):
    """Temporarily hide (or restore) editor start-point sprites: PlayerStart and the project's DynaDriveStart actors.
    Editor-only and transient (never saved, not part of the level data); returns the before/after state of each."""
    cb._ready()
    out = []
    for a in city_world._actors():
        if type(a).__name__ in ('PlayerStart', 'PlayerStartPIE', 'DynaDriveStart'):
            before = bool(a.is_temporarily_hidden_in_editor())
            a.set_is_temporarily_hidden_in_editor(bool(hidden))
            out.append({'label': a.get_actor_label(), 'before_hidden': before, 'after_hidden': bool(a.is_temporarily_hidden_in_editor())})
    return out


def prepare(manifest_path, geometry_path, materials_path):
    """Import patches and geometry into hash-named folders. Call again while it reports importing."""
    cb._ready()
    manifest = _read(manifest_path)
    result = {'patches': {}, 'hashes': {}}
    for p in manifest['patches']:
        sha = _sha(p['asset'])
        if sha != p['sha256']:
            raise ValueError('Patch bytes differ from the manifest: ' + p['asset'])
        batch = p.get('batch') or p['removed'][0]['batch']
        path = _import(p['asset'], ROOT + '/Patches/' + batch + '/' + sha[:12], 'patch')
        if path is None:
            return {'importing': True, 'what': batch}
        result['patches'][batch] = path; result['hashes'][p['asset']] = sha
    gsha = _sha(geometry_path)
    path = _import(geometry_path, ROOT + '/Geometry/' + gsha[:12], 'reconstruction')
    if path is None:
        return {'importing': True, 'what': 'reconstruction'}
    result['geometry'] = path; result['hashes'][str(geometry_path)] = gsha; result['geometry_file'] = str(geometry_path)
    gm = unreal.load_asset(path)
    editor = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    ns = editor.get_nanite_settings(gm)
    if ns.get_editor_property('enabled'):  # Nanite cannot render translucent sections; this mesh is small (tens of thousands of triangles)
        ns.set_editor_property('enabled', False)
        editor.set_nanite_settings(gm, ns, True)
        unreal.EditorAssetLibrary.save_loaded_asset(gm)
    result['nanite_enabled_after_prepare'] = bool(editor.get_nanite_settings(gm).get_editor_property('enabled'))
    materials = _read(materials_path)
    mesh = unreal.load_asset(path)
    slots = [str(s.material_slot_name) for s in mesh.static_materials]
    unresolved = [s for s in slots if s not in materials]
    if unresolved:
        raise RuntimeError('Imported material slots with no mapping: ' + str(unresolved) + '; known: ' + str(sorted(materials)))
    result['slots'] = slots
    result['materials'] = {n: _material(n, materials[n]).get_path_name() for n in slots}
    _sibling(manifest_path, 'corridor-assets.json').write_text(json.dumps(result, indent=1))
    return result


def _check_hashes(manifest, assets):
    for p in manifest['patches']:
        if _sha(p['asset']) != assets['hashes'][p['asset']] or _sha(p['asset']) != p['sha256']:
            raise RuntimeError('Stale import: patch changed since prepare(): ' + p['asset'])
    if _sha(assets['geometry_file']) != assets['hashes'][assets['geometry_file']]:
        raise RuntimeError('Stale import: geometry changed since prepare()')


def install(manifest_path):
    cb._ready()
    manifest = _read(manifest_path)
    assets = _read(_sibling(manifest_path, 'corridor-assets.json'))
    _check_hashes(manifest, assets)
    batches = sorted(assets['patches'])
    pre_path, backup_path = _sibling(manifest_path, 'corridor-snapshot-pre.json'), _sibling(manifest_path, 'corridor-backup.json')
    if backup_path.exists() or pre_path.exists():
        raise RuntimeError('Previous install artifacts exist; rollback or clear them deliberately first')
    pre = snapshot()
    if not pre['actors']:
        raise RuntimeError('No building batch actors loaded; cannot snapshot')
    if any(TAG in a.tags for a in city_world._actors()):
        raise RuntimeError('Already installed; rollback first')
    pre_path.write_text(json.dumps(pre, indent=1))
    actors = city_world._actors()
    backup, sources, extra_hidden = {'schema': 'dynacity-corridor-backup/2', 'actors': []}, [], []
    ref_actor = None
    for batch in batches:
        matches = [a for a in actors if a.get_actor_label() == batch]
        if len(matches) != 1:
            raise RuntimeError('Missing or duplicate original batch ' + batch)
        actor = matches[0]
        backup['actors'].append({'actor': actor.get_path_name(), 'label': batch, 'state': _state(actor)})
        sources.append((actor, _component(actor), assets['patches'][batch]))
    if any(unreal.load_asset(assets['patches'][b]).static_materials.__len__() > 1 for b in batches):
        refs = [a for a in actors if a.get_actor_label() == REFERENCE_VISUAL_LABEL]
        if len(refs) != 1:
            raise RuntimeError('A patched batch has a reference slot but the reference visual actor is missing or duplicated')
        ref_actor = refs[0]
        backup['actors'].append({'actor': ref_actor.get_path_name(), 'label': REFERENCE_VISUAL_LABEL, 'state': _state(ref_actor)})
        extra_hidden.append(ref_actor)
    backup_path.write_text(json.dumps(backup, indent=1))
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    created = []
    try:
        for actor, component, mesh_path in sources:
            visual = subsystem.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector()); created.append(visual)
            visual.set_actor_label('Corridor visual - ' + actor.get_actor_label())
            visual.tags = [TAG, 'VISUAL_BATCH_STREET_WALLS_OMITTED']
            visual.set_folder_path('DynaCity/Corridor reconstruction')
            visual.set_actor_transform(actor.get_actor_transform(), False, True)
            comp = _component(visual)
            comp.set_static_mesh(unreal.load_asset(mesh_path))
            slots_v = [str(sl.material_slot_name) for sl in unreal.load_asset(mesh_path).static_materials]
            if slots_v == ['SourceExisting']:
                comp.set_editor_property('override_materials', list(component.get_editor_property('override_materials')))
            elif slots_v == ['SourceExisting', 'ReferenceFacade'] and ref_actor is not None:
                ref_mats = list(_component(ref_actor).get_materials())
                comp.set_editor_property('override_materials', [ref_mats[0], ref_mats[1]])
            else:
                raise RuntimeError('Unmapped visual slots for ' + mesh_path + ': ' + str(slots_v))
            cb._disable_visual_collision(visual, comp)
        recon = subsystem.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector()); created.append(recon)
        recon.set_actor_label('Corridor reconstruction - source-supported frontages (candidate identities, estimated geometry)')
        recon.tags = [TAG, 'ESTIMATED_GEOMETRY_NOT_SURVEYED'] + ['source:' + b['source_id'] for b in manifest['buildings']]
        recon.set_folder_path('DynaCity/Corridor reconstruction')
        recon.set_actor_transform(sources[0][0].get_actor_transform(), False, True)
        comp = _component(recon)
        mesh = unreal.load_asset(assets['geometry']); comp.set_static_mesh(mesh)
        slots = [str(s.material_slot_name) for s in mesh.static_materials]
        if slots != assets['slots']:
            raise RuntimeError('Geometry slots changed since prepare(): ' + str(slots))
        for i, slot in enumerate(slots):
            comp.set_material(i, unreal.load_asset(assets['materials'][slot]))
        for i, slot in enumerate(slots):
            got = comp.get_material(i)
            if got is None or got.get_path_name() != assets['materials'][slot]:
                raise RuntimeError('Material slot not applied: ' + slot)
        cb._disable_visual_collision(recon, comp)
        for actor, component, _ in sources:
            actor.modify(); component.modify(); component.set_visibility(False, True)
        for actor in extra_hidden:
            c = _component(actor); actor.modify(); c.modify(); c.set_visibility(False, True)
        touched = {a.get_outermost() for a in created} | {a.get_outermost() for a, _, _ in sources} | {a.get_outermost() for a in extra_hidden}
        if not unreal.EditorLoadingAndSavingUtils.save_packages(list(touched), True):
            raise RuntimeError('Could not save touched packages')
    except Exception:
        for actor in created:
            subsystem.destroy_actor(actor)
        for row in backup['actors']:   # exact saved prestate, not a guessed one
            for a in city_world._actors():
                if a.get_path_name() == row['actor']:
                    c = _component(a); c.modify(); c.set_visibility(row['state']['visible'], True)
        backup_path.unlink(missing_ok=True); pre_path.unlink(missing_ok=True)
        raise
    return report(manifest_path)


def _dirty_names():
    names = set()
    for fn in ('get_dirty_content_packages', 'get_dirty_map_packages'):
        names |= {p.get_name() for p in (getattr(unreal.EditorLoadingAndSavingUtils, fn)() or [])}
    return names


def report(manifest_path):
    """(Re)build corridor-install.json from the live tagged actors; safe to call repeatedly."""
    cb._ready()
    backup = _read(_sibling(manifest_path, 'corridor-backup.json'))
    live = [a for a in city_world._actors() if TAG in a.tags]
    files = _package_files(live)
    out = {'created': sorted(a.get_actor_label() for a in live), 'created_package_files': files,
           'files_exist_after_save': {k: Path(v).exists() for k, v in files.items()},
           'hidden_originals': [r['label'] for r in backup['actors']],
           'still_dirty_after_save': sorted(a.get_actor_label() for a in live if a.get_outermost().get_name() in _dirty_names())}
    _sibling(manifest_path, 'corridor-install.json').write_text(json.dumps(out, indent=1))
    return out


def verify(manifest_path):
    """Recompute: live snapshot vs the pre-install snapshot, allowing only the hidden originals' visibility."""
    pre = scene_compare.load(_sibling(manifest_path, 'corridor-snapshot-pre.json'))
    backup = _read(_sibling(manifest_path, 'corridor-backup.json'))
    post = snapshot()
    allowed = {row['actor']: {'visible': (True, False)} for row in backup['actors'] if row['state']['visible']}
    cmp = scene_compare.compare(pre, post, allowed, CREATED_PREFIXES)
    live = [a for a in city_world._actors() if TAG in a.tags]
    install = _read(_sibling(manifest_path, 'corridor-install.json'))
    files = {k: Path(v).exists() for k, v in install['created_package_files'].items()}
    out = {'compared_actors': cmp['compared_actors'], 'expected_changes': len(cmp['expected_changes']), 'unexpected': cmp['unexpected'],
           'tagged_actors': [a.get_actor_label() for a in live], 'created_package_files_exist': files,
           'originals': [{'label': r['label'], 'collision_unchanged': post['actors'][r['actor']]['collision'] == r['state']['collision'],
                          'mesh_unchanged': post['actors'][r['actor']]['mesh'] == r['state']['mesh']} for r in backup['actors']]}
    out['ok'] = cmp['ok'] and len(live) == len(install['created']) and all(files.values()) \
        and all(o['collision_unchanged'] and o['mesh_unchanged'] for o in out['originals'])
    return out


def reload_check(manifest_path):
    """Unload then reload the affected actors and re-verify (an in-editor reload of the saved actor packages)."""
    cb._ready()
    backup = _read(_sibling(manifest_path, 'corridor-backup.json'))
    labels = {r['label'] for r in backup['actors']} | {'Corridor visual - ' + r['label'] for r in backup['actors']}
    labels.add('Corridor reconstruction - source-supported frontages (candidate identities, estimated geometry)')
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    guids = [d.guid for d in descs if str(d.label) in labels]
    unreal.WorldPartitionBlueprintLibrary.unload_actors(guids)
    unreal.WorldPartitionBlueprintLibrary.load_actors(guids)
    result = verify(manifest_path)
    result['reloaded_descriptors'] = len(guids)
    return result


def rollback(manifest_path):
    """Restore the exact saved component states, delete every created actor, save, and prove the files are gone."""
    cb._ready()
    backup = _read(_sibling(manifest_path, 'corridor-backup.json'))
    install = _read(_sibling(manifest_path, 'corridor-install.json')) if _sibling(manifest_path, 'corridor-install.json').exists() else {}
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    _load_batches()
    actors = city_world._actors()
    restored = []
    for row in backup['actors']:
        for a in actors:
            if a.get_path_name() == row['actor']:
                c = _component(a); a.modify(); c.modify()
                c.set_visibility(row['state']['visible'], True)
                restored.append(a)
    doomed = [a for a in actors if TAG in a.tags]
    files = _package_files(doomed)
    packages = {a.get_outermost() for a in doomed} | {a.get_outermost() for a in restored}
    for a in doomed:
        subsystem.destroy_actor(a)
    saved = unreal.EditorLoadingAndSavingUtils.save_packages(list(packages), True)
    still = {k: v for k, v in files.items() if Path(v).exists()}
    after = {r['label']: _state(a) for r in backup['actors'] for a in restored if a.get_path_name() == r['actor']}
    mismatched = [r['label'] for r in backup['actors'] if after.get(r['label']) != r['state']]
    result = {'restored': [r['label'] for r in backup['actors']], 'deleted': sorted(files), 'save_ok': bool(saved),
              'deleted_files_still_on_disk': still, 'state_mismatch_after_restore': mismatched}
    result['ok'] = bool(saved) and not still and not mismatched
    if result['ok']:
        for name in ('corridor-backup.json', 'corridor-snapshot-pre.json', 'corridor-install.json'):
            p = _sibling(manifest_path, name)
            if p.exists():  # unique archive names: repeated rollbacks must never collide
                p.rename(p.with_name(p.stem + '.rolled-back-' + str(int(time.time())) + '.json'))
    return result


def mesh_bounds(paths):
    """Read-only: bounding box (cm) and triangle count of static meshes."""
    out = {}
    for p in paths:
        mesh = unreal.load_asset(p)
        box = mesh.get_bounding_box()
        out[p] = {'min': [box.min.x, box.min.y, box.min.z], 'max': [box.max.x, box.max.y, box.max.z], 'triangles': int(mesh.get_num_triangles(0))}
    return out


def mesh_settings(paths):
    """Read-only: Nanite flag, LOD count, triangles per LOD, vertices and material slots."""
    editor = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    out = {}
    for p in paths:
        mesh = unreal.load_asset(p)
        n = editor.get_lod_count(mesh)
        out[p] = {'nanite': bool(editor.get_nanite_settings(mesh).get_editor_property('enabled')), 'lods': n,
                  'triangles_per_lod': [int(mesh.get_num_triangles(i)) for i in range(n)], 'vertices_lod0': int(mesh.get_num_vertices(0)),
                  'slots': [str(s.material_slot_name) for s in mesh.static_materials]}
    return out


def compare_to(pre_snapshot_path):
    """Read-only: live building-batch snapshot vs a saved snapshot, no allowlist."""
    cb._ready()
    cmp = scene_compare.compare(scene_compare.load(pre_snapshot_path), snapshot())
    return {'ok': cmp['ok'], 'compared_actors': cmp['compared_actors'], 'unexpected': cmp['unexpected'], 'expected_changes': len(cmp['expected_changes'])}


def inspect_actor(label):
    """Read-only: mesh slots and component materials of the actors with this label."""
    cb._ready()
    out = []
    for a in city_world._actors():
        if a.get_actor_label() == label:
            comp = _component(a)
            mesh = comp.static_mesh
            out.append({'path': a.get_path_name(), 'mesh': mesh.get_path_name() if mesh else None,
                        'slots': [str(s.material_slot_name) for s in mesh.static_materials] if mesh else [],
                        'materials': [m.get_path_name() if m else None for m in comp.get_materials()],
                        'visible': bool(comp.is_visible()), 'collision': str(comp.get_collision_enabled()),
                        'tags': [str(t) for t in a.tags][:6]})
    return out


def find_start_actors():
    """Read-only: actors whose label or class mentions a start/spawn point, with their editor billboard state."""
    cb._ready()
    out = []
    for a in city_world._actors():
        label, cls = a.get_actor_label(), type(a).__name__
        if 'start' in label.lower() or 'start' in cls.lower() or 'spawn' in label.lower():
            comps = [type(c).__name__ for c in a.get_components_by_class(unreal.SceneComponent)][:6]
            out.append({'label': label, 'class': cls, 'components': comps, 'hidden_in_editor': bool(a.is_temporarily_hidden_in_editor()),
                        'location': [round(a.get_actor_location().x), round(a.get_actor_location().y), round(a.get_actor_location().z)]})
    return out


def ground_heights(points_xy_cm):
    """Read-only: world Z (cm) of the first blocking hit straight down at each (x, y); used to keep cameras above real ground."""
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    out = []
    for x, y in points_xy_cm:
        h = unreal.SystemLibrary.line_trace_single(world, unreal.Vector(x, y, 30000.), unreal.Vector(x, y, -30000.), unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
                                                   False, [], unreal.DrawDebugTrace.NONE, True)
        t = h.to_tuple() if hasattr(h, 'to_tuple') else h
        out.append({'xy_cm': [x, y], 'hit': bool(t[0]), 'z_cm': round(float(t[4].z), 1) if t[0] else None})
    return out


def inspect_lgb():
    """Read-only: every loaded actor that renders or carries the LGB (reference actors, patched visual batch, details)."""
    cb._ready()
    out = []
    for a in city_world._actors():
        label = a.get_actor_label()
        if not (('LGB' in label) or label.startswith('Reference') or label in ('Corridor visual - buildings_remeil_material_9', 'buildings_remeil_material_9')):
            continue
        comp = _component(a)
        row = {'label': label, 'class': type(a).__name__, 'path': a.get_path_name(), 'tags': [str(t) for t in a.tags][:6], 'component': type(comp).__name__ if comp else None}
        if comp:
            mesh = comp.static_mesh
            row.update({'mesh': mesh.get_path_name() if mesh else None, 'visible': bool(comp.is_visible()), 'collision': str(comp.get_collision_enabled()),
                        'materials': [m.get_path_name() if m else None for m in comp.get_materials()],
                        'slots': [str(s.material_slot_name) for s in mesh.static_materials] if mesh else []})
            if mesh:
                row['triangles_lod0'] = int(mesh.get_num_triangles(0))
            try:
                row['instance_count'] = int(comp.get_instance_count())
            except Exception:
                row['instance_count'] = None
            box = a.get_actor_bounds(False)
            row['bounds_centre_cm'] = [round(box[0].x), round(box[0].y), round(box[0].z)]
            row['bounds_extent_cm'] = [round(box[1].x), round(box[1].y), round(box[1].z)]
        out.append(row)
    return out


def restore_start_icons(states):
    """Restore the exact transient editor flags captured by player_start_icons."""
    wanted = {s['label']: s['before_hidden'] for s in states}
    out = []
    for a in city_world._actors():
        if a.get_actor_label() in wanted:
            a.set_is_temporarily_hidden_in_editor(wanted[a.get_actor_label()])
            out.append({'label': a.get_actor_label(), 'hidden': bool(a.is_temporarily_hidden_in_editor())})
    return out


def lgb_preview_prepare(spec):
    import importlib,lgb_native_pilot
    importlib.reload(lgb_native_pilot)
    return lgb_native_pilot.prepare(spec)


def lgb_preview_apply():
    import lgb_native_pilot
    return lgb_native_pilot.apply()


def lgb_preview_rollback():
    import lgb_native_pilot
    return lgb_native_pilot.rollback()


def tower_infill_stage(action, *args):
    import importlib, tower_infill_native_pilot
    importlib.reload(tower_infill_native_pilot)
    if action not in ('preflight', 'prepare', 'apply', 'rollback', 'verify_assets'):
        raise ValueError('Unsupported tower preview stage')
    return getattr(tower_infill_native_pilot, action)(*args)


def lgb_mask_test(action):
    import importlib, lgb_mask_diagnostic
    importlib.reload(lgb_mask_diagnostic)
    return lgb_mask_diagnostic.stage(action)


def facade_identification_stage(action):
    import importlib, facade_identification
    importlib.reload(facade_identification)
    return facade_identification.stage(action)


def corridor_canopy_stage(action):
    import importlib, corridor_canopy_preview
    importlib.reload(corridor_canopy_preview)
    return corridor_canopy_preview.stage(action)


def corridor_canopy_ahead_stage(action):
    """Separate estimated placement test; retain the rejected pilot's evidence."""
    import importlib, corridor_canopy_preview
    importlib.reload(corridor_canopy_preview)
    corridor_canopy_preview.R = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration23-canopy-ahead')
    corridor_canopy_preview.POINTS = [(2513.,1504.),(2533.,1505.)]
    return corridor_canopy_preview.stage(action)


def corridor_canopy_distance_stage(action):
    import importlib, corridor_canopy_preview
    importlib.reload(corridor_canopy_preview)
    corridor_canopy_preview.R = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration24-foliage-distance')
    corridor_canopy_preview.POINTS = [(2513.,1504.),(2533.,1505.)]
    return corridor_canopy_preview.stage(action)


def corridor_canopy_preserve_stage(action):
    import importlib, corridor_canopy_preview
    importlib.reload(corridor_canopy_preview)
    corridor_canopy_preview.R = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration25-preserve-area')
    corridor_canopy_preview.POINTS = [(2513.,1504.),(2533.,1505.)]
    return corridor_canopy_preview.stage(action)


def corridor_canopy_reload_stage(action):
    import importlib, corridor_canopy_preview
    importlib.reload(corridor_canopy_preview)
    corridor_canopy_preview.R = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/native/iteration26-canopy-reload')
    corridor_canopy_preview.POINTS = [(2513.,1504.),(2533.,1505.)]
    corridor_canopy_preview.MESH_OVERRIDE = '/Game/DynaCity/CanopyPilot/tree_small_02_preserve_area_v1.tree_small_02_preserve_area_v1'
    return corridor_canopy_preview.stage(action)


def granite_parapet_stage(action):
    import importlib, granite_parapet_native_pilot
    importlib.reload(granite_parapet_native_pilot)
    return granite_parapet_native_pilot.stage(action)


def corridor_feature_bundle_stage(action):
    import importlib, corridor_feature_bundle_pilot
    importlib.reload(corridor_feature_bundle_pilot)
    return corridor_feature_bundle_pilot.stage(action)


def corridor_stone_material_stage(action):
    """Root-only estimated cladding material pilot; no scene save."""
    import importlib
    import corridor_stone_material_pilot as pilot
    importlib.reload(pilot)
    return pilot.stage(action)
