"""Root-owned stone material pilot for corridor facade cladding — procedural pale grid, no textures.

Single-slot override on the corridor reference actor material index 16 (stone_light__granite).
Hash-versioned candidate material under /Game/DynaCity/StoneMaterialPilot.
Preflight -> prepare -> apply -> rollback transactional pattern.
"""
import hashlib
import json
import uuid
import copy
from pathlib import Path
import unreal
import corridor_feature_bundle_pilot as bundle

# Private project / map guards
EXPECTED_PROJECT = 'C:/private/dynacity-data/engine/sea-highway/UnrealPrototype/DynaCityBeirut.uproject'
EXPECTED_MAP = '/Game/Maps/BeirutCityBatched.BeirutCityBatched'

# Target actor / slot (pinned from retained snapshot)
REFERENCE_LABEL = 'Corridor reconstruction - source-supported frontages (candidate identities, estimated geometry)'
MESH_PATH = '/Game/DynaCity/CorridorReconstruction/Geometry/3ebba28a028c/corridor-reconstruction-v2/StaticMeshes/reconstruction.reconstruction'
TARGET_SLOT_NAME = 'stone_light__granite'
TARGET_MATERIAL_INDEX = 16
ORIGINAL_MATERIAL_PATH = '/Game/DynaCity/CorridorReconstruction/Materials/M_stone_light__granite_39b51bd0.M_stone_light__granite_39b51bd0'

# Source identity from corridor-manifest-v2.json
SOURCE_ID_FULL = 'beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1'

# Candidate material folder
CANDIDATE_ROOT = '/Game/DynaCity/StoneMaterialPilot'

# Procedural grid parameters (ESTIMATED, no dimension source accuracy claim)
GRID_WIDTH_M = 1.0
GRID_HEIGHT_M = 0.5
JOINT_WIDTH_M = 0.008
DIFFUSE_COLOR = (0.28, 0.30, 0.32)  # linear sRGB
JOINT_COLOR = (0.19, 0.20, 0.21)
ROUGHNESS = 0.7
METALLIC = 0.0

# Private transaction directory
TXN_ROOT = Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration29-stone-material')


def _guard_environment():
    """Read-only project/map/PIE verification (no transaction/material checks)."""
    project = unreal.Paths.convert_relative_path_to_full(unreal.Paths.get_project_file_path()).replace('\\', '/')
    if project.casefold() != EXPECTED_PROJECT.casefold():
        raise RuntimeError('Wrong private project: ' + project)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if not world or world.get_path_name() != EXPECTED_MAP:
        raise RuntimeError('Wrong map: ' + (world.get_path_name() if world else 'None'))
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('PIE active; preserve play')
    return {'project': EXPECTED_PROJECT, 'map': EXPECTED_MAP, 'scene_saved': False}


def _guard_target():
    """Read-only target actor/mesh/slot/material verification (allows existing transaction)."""
    # Verify reference actor exists and has correct mesh
    actors_list = list(bundle.ci.city_world._actors())
    refs = [a for a in actors_list if a.get_actor_label() == REFERENCE_LABEL]
    if len(refs) != 1:
        raise RuntimeError('Reference actor not unique: found ' + str(len(refs)))
    ref = refs[0]
    comp = bundle.ci._component(ref)
    if not comp or not comp.static_mesh:
        raise RuntimeError('Reference actor missing StaticMeshComponent or mesh')
    if comp.static_mesh.get_path_name() != MESH_PATH:
        raise RuntimeError('Reference actor mesh mismatch: ' + comp.static_mesh.get_path_name())
    # Verify slot index 16 exists and has expected name
    slots = [str(s.material_slot_name) for s in comp.static_mesh.static_materials]
    if len(slots) <= TARGET_MATERIAL_INDEX:
        raise RuntimeError('Mesh has fewer slots than target index: ' + str(len(slots)))
    if slots[TARGET_MATERIAL_INDEX] != TARGET_SLOT_NAME:
        raise RuntimeError('Slot name mismatch at index ' + str(TARGET_MATERIAL_INDEX) + ': ' + slots[TARGET_MATERIAL_INDEX])
    # Verify source identity matches manifest
    source_tags = [str(t) for t in ref.tags if str(t).startswith('source:')]
    if SOURCE_ID_FULL not in [t.split(':', 1)[1] for t in source_tags if ':' in t]:
        raise RuntimeError('Reference actor source_id mismatch; expected ' + SOURCE_ID_FULL)
    return ref, comp


def _snapshot(excluded_paths=()):
    """Snapshot all currently loaded static actor states and material lists."""
    actors = {}
    for a in bundle.ci.city_world._actors():
        path = a.get_path_name()
        if path in excluded_paths:
            continue
        comp = bundle.ci._component(a)
        if comp:
            actors[path] = bundle.ci._state(a)
    return actors


def _make_grid_material(name, tag):
    """Create procedural pale cladding grid material using WorldPosition/VertexNormalWS.

    World-space projection on vertical planes:
    - Dominant absolute WorldNormal horizontal component (X or Y) chooses vertical plane (X,Z) or (Y,Z)
    - absX >= absY -> X-plane (normal along X) -> use Y,Z coords
    - absY > absX  -> Y-plane (normal along Y) -> use X,Z coords
    - Units: UE WorldPosition in cm -> grid params in cm (width=100cm, height=50cm, joint=0.8cm)
    - Joint test: frac(coord / size) < joint/size => 1 (joint), else 0 (panel)
    - No texture dependencies, pure math nodes
    """
    lib = unreal.MaterialEditingLibrary
    path = CANDIDATE_ROOT + '/Materials/' + name
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.load_asset(path)
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, CANDIDATE_ROOT + '/Materials', unreal.Material, unreal.MaterialFactoryNew())
    mat.set_editor_property('used_with_instanced_static_meshes', True)
    mat.set_editor_property('two_sided', True)

    # --- Constants with default coordinates ---
    def const(val, x=-1000, y=0):
        if isinstance(val, (tuple, list)):
            node = lib.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, x, y)
            node.set_editor_property('constant', unreal.LinearColor(*val[:3], 1.0))
        else:
            node = lib.create_material_expression(mat, unreal.MaterialExpressionConstant, x, y)
            node.set_editor_property('r', float(val))
        return node

    # Grid parameters in cm (UE WorldPosition units)
    width_cm = GRID_WIDTH_M * 100.0      # 100 cm
    height_cm = GRID_HEIGHT_M * 100.0    # 50 cm
    joint_cm = JOINT_WIDTH_M * 100.0     # 0.8 cm

    c_width = const(width_cm, -1000, -300)
    c_height = const(height_cm, -1000, -150)
    c_joint = const(joint_cm, -1000, 0)
    c_diffuse = const(DIFFUSE_COLOR, -1000, 150)
    c_joint_col = const(JOINT_COLOR, -1000, 300)
    c_rough = const(ROUGHNESS, -1000, 450)
    c_metal = const(METALLIC, -1000, 600)
    c_one = const(1.0, -1000, 750)
    c_zero = const(0.0, -1000, 900)

    # --- World Position (cm) ---
    world_pos = lib.create_material_expression(mat, unreal.MaterialExpressionWorldPosition, -800, -300)

    # --- Vertex Normal WS ---
    vert_normal = lib.create_material_expression(mat, unreal.MaterialExpressionVertexNormalWS, -800, 100)

    # --- Determine dominant horizontal normal component (X vs Y) ---
    # Mask X and Y components
    mask_x = lib.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -500, -300)
    mask_x.set_editor_property('r', True)
    mask_x.set_editor_property('g', False)
    mask_x.set_editor_property('b', False)
    lib.connect_material_expressions(vert_normal, '', mask_x, '')

    mask_y = lib.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -500, -100)
    mask_y.set_editor_property('r', False)
    mask_y.set_editor_property('g', True)
    mask_y.set_editor_property('b', False)
    lib.connect_material_expressions(vert_normal, '', mask_y, '')

    # Absolute values
    abs_x = lib.create_material_expression(mat, unreal.MaterialExpressionAbs, -300, -300)
    lib.connect_material_expressions(mask_x, '', abs_x, '')
    abs_y = lib.create_material_expression(mat, unreal.MaterialExpressionAbs, -300, -100)
    lib.connect_material_expressions(mask_y, '', abs_y, '')

    # Compare |X| >= |Y| ? 1 : 0  (absX >= absY -> X-plane -> use Y; absY > absX -> Y-plane -> use X)
    # MaterialExpressionIf has pins: A>B, A==B, A<B
    cmp_xy = lib.create_material_expression(mat, unreal.MaterialExpressionIf, -100, -200)
    cmp_xy.set_editor_property('equals_threshold', 0.0)
    lib.connect_material_expressions(abs_x, '', cmp_xy, 'A')
    lib.connect_material_expressions(abs_y, '', cmp_xy, 'B')
    lib.connect_material_expressions(c_one, '', cmp_xy, 'A>B')     # absX > absY -> X-plane
    lib.connect_material_expressions(c_one, '', cmp_xy, 'A==B')    # tie -> X-plane (match CPU)
    lib.connect_material_expressions(c_zero, '', cmp_xy, 'A<B')    # absX < absY -> Y-plane

    # --- Extract coordinate components from WorldPosition ---
    mask_wp_x = lib.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -500, 150)
    mask_wp_x.set_editor_property('r', True); mask_wp_x.set_editor_property('g', False); mask_wp_x.set_editor_property('b', False)
    lib.connect_material_expressions(world_pos, '', mask_wp_x, '')

    mask_wp_y = lib.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -500, 300)
    mask_wp_y.set_editor_property('r', False); mask_wp_y.set_editor_property('g', True); mask_wp_y.set_editor_property('b', False)
    lib.connect_material_expressions(world_pos, '', mask_wp_y, '')

    mask_wp_z = lib.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -500, 450)
    mask_wp_z.set_editor_property('r', False); mask_wp_z.set_editor_property('g', False); mask_wp_z.set_editor_property('b', True)
    lib.connect_material_expressions(world_pos, '', mask_wp_z, '')

    # Select horizontal coordinate based on dominant normal
    # cmp_xy = 1 (X-plane) -> use Y; cmp_xy = 0 (Y-plane) -> use X
    lerp_h = lib.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, -200, 300)
    lib.connect_material_expressions(mask_wp_x, '', lerp_h, 'A')  # X-plane (Alpha=1) -> use Y
    lib.connect_material_expressions(mask_wp_y, '', lerp_h, 'B')  # Y-plane (Alpha=0) -> use X
    lib.connect_material_expressions(cmp_xy, '', lerp_h, 'Alpha')

    # Vertical coordinate is always Z
    vert_coord = mask_wp_z

    # --- Grid calculation (modulo with joint offset) ---
    # Horizontal: frac(coord / width) < joint/width -> 1 (joint), else 0 (panel)
    div_h = lib.create_material_expression(mat, unreal.MaterialExpressionDivide, 100, 150)
    lib.connect_material_expressions(lerp_h, '', div_h, 'A')
    lib.connect_material_expressions(c_width, '', div_h, 'B')
    frac_h = lib.create_material_expression(mat, unreal.MaterialExpressionFrac, 300, 150)
    lib.connect_material_expressions(div_h, '', frac_h, '')
    joint_thresh_h = lib.create_material_expression(mat, unreal.MaterialExpressionDivide, 100, 300)
    lib.connect_material_expressions(c_joint, '', joint_thresh_h, 'A')
    lib.connect_material_expressions(c_width, '', joint_thresh_h, 'B')
    cmp_h = lib.create_material_expression(mat, unreal.MaterialExpressionIf, 500, 150)
    cmp_h.set_editor_property('equals_threshold', 0.0)
    lib.connect_material_expressions(frac_h, '', cmp_h, 'A')
    lib.connect_material_expressions(joint_thresh_h, '', cmp_h, 'B')
    lib.connect_material_expressions(c_zero, '', cmp_h, 'A>B')      # frac >= threshold -> panel (0)
    lib.connect_material_expressions(c_one, '', cmp_h, 'A<B')    # frac < threshold -> joint (1)
    # A==B treated as panel (0) - exact boundary is panel
    lib.connect_material_expressions(c_zero, '', cmp_h, 'A==B')

    # Vertical: frac(coord / height) < joint/height -> 1 (joint), else 0 (panel)
    div_v = lib.create_material_expression(mat, unreal.MaterialExpressionDivide, 100, 450)
    lib.connect_material_expressions(vert_coord, '', div_v, 'A')
    lib.connect_material_expressions(c_height, '', div_v, 'B')
    frac_v = lib.create_material_expression(mat, unreal.MaterialExpressionFrac, 300, 450)
    lib.connect_material_expressions(div_v, '', frac_v, '')
    joint_thresh_v = lib.create_material_expression(mat, unreal.MaterialExpressionDivide, 100, 600)
    lib.connect_material_expressions(c_joint, '', joint_thresh_v, 'A')
    lib.connect_material_expressions(c_height, '', joint_thresh_v, 'B')
    cmp_v = lib.create_material_expression(mat, unreal.MaterialExpressionIf, 500, 450)
    cmp_v.set_editor_property('equals_threshold', 0.0)
    lib.connect_material_expressions(frac_v, '', cmp_v, 'A')
    lib.connect_material_expressions(joint_thresh_v, '', cmp_v, 'B')
    lib.connect_material_expressions(c_zero, '', cmp_v, 'A>B')
    lib.connect_material_expressions(c_one, '', cmp_v, 'A<B')
    lib.connect_material_expressions(c_zero, '', cmp_v, 'A==B')

    # Combine: joint if either horizontal OR vertical is in joint (Max of 0/1)
    max_joint = lib.create_material_expression(mat, unreal.MaterialExpressionMax, 700, 300)
    lib.connect_material_expressions(cmp_h, '', max_joint, 'A')
    lib.connect_material_expressions(cmp_v, '', max_joint, 'B')

    # --- Base Color: lerp between diffuse (panel) and joint color ---
    # Alpha = 1 (joint) -> joint color; Alpha = 0 (panel) -> diffuse
    lerp_col = lib.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, 900, 300)
    lib.connect_material_expressions(c_diffuse, '', lerp_col, 'A')     # panel color
    lib.connect_material_expressions(c_joint_col, '', lerp_col, 'B')   # joint color
    lib.connect_material_expressions(max_joint, '', lerp_col, 'Alpha')
    lib.connect_material_property(lerp_col, '', unreal.MaterialProperty.MP_BASE_COLOR)

    # --- Roughness / Metallic ---
    lib.connect_material_property(c_rough, '', unreal.MaterialProperty.MP_ROUGHNESS)
    lib.connect_material_property(c_metal, '', unreal.MaterialProperty.MP_METALLIC)

    lib.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def preflight():
    """Read-only environment + target verification."""
    env = _guard_environment()
    ref, comp = _guard_target()
    current = comp.get_material(TARGET_MATERIAL_INDEX)
    if not current or current.get_path_name() != ORIGINAL_MATERIAL_PATH:
        raise RuntimeError('Slot material not original')
    env.update({
        'reference_actor': ref.get_path_name(),
        'mesh': MESH_PATH,
        'slot_index': TARGET_MATERIAL_INDEX,
        'slot_name': TARGET_SLOT_NAME,
        'original_material': ORIGINAL_MATERIAL_PATH
    })
    return env


def _candidate_tag():
    params = ['graph-v3', GRID_WIDTH_M, GRID_HEIGHT_M, JOINT_WIDTH_M,
              DIFFUSE_COLOR, JOINT_COLOR, ROUGHNESS, METALLIC]
    return hashlib.sha256(json.dumps(params).encode()).hexdigest()[:12]


def prepare():
    """Create hash-versioned candidate material and prepared.json (no transaction)."""
    _guard_environment()
    ref, comp = _guard_target()

    # Verify current material is original
    current_mat = comp.get_material(TARGET_MATERIAL_INDEX)
    if not current_mat or current_mat.get_path_name() != ORIGINAL_MATERIAL_PATH:
        raise RuntimeError('Slot material not original: ' + (current_mat.get_path_name() if current_mat else 'None'))

    # Create hash-versioned material name from procedural params
    tag = _candidate_tag()
    mat_name = f'M_StoneGrid_{tag}'

    mat = _make_grid_material(mat_name, tag)

    # Verify candidate material exists and is loadable
    candidate_path = mat.get_path_name()
    if not unreal.EditorAssetLibrary.does_asset_exist(candidate_path):
        raise RuntimeError('Candidate material not saved: ' + candidate_path)
    if not candidate_path.startswith(CANDIDATE_ROOT + '/Materials/'):
        raise RuntimeError('Candidate material not in allowed folder: ' + candidate_path)

    # Prepare data (no transaction yet)
    TXN_ROOT.mkdir(parents=True, exist_ok=True)
    data = {
        'candidate_material': candidate_path,
        'candidate_tag': tag,
        'params': {
            'grid_width_m': GRID_WIDTH_M,
            'grid_height_m': GRID_HEIGHT_M,
            'joint_width_m': JOINT_WIDTH_M,
            'diffuse_color': DIFFUSE_COLOR,
            'joint_color': JOINT_COLOR,
            'roughness': ROUGHNESS,
            'metallic': METALLIC
        },
        'original_material': ORIGINAL_MATERIAL_PATH,
        'target_slot_index': TARGET_MATERIAL_INDEX,
        'target_slot_name': TARGET_SLOT_NAME,
        'reference_label': REFERENCE_LABEL,
        'reference_path': ref.get_path_name(),
        'mesh_path': MESH_PATH,
        'source_id': SOURCE_ID_FULL
    }
    (TXN_ROOT / 'prepared.json').write_text(json.dumps(data, indent=2))
    return {'candidate_material': candidate_path, 'tag': tag, 'scene_saved': False}


def apply():
    """Apply material override with transaction; snapshot before/after with only target slot difference."""
    _guard_environment()

    # Refuse if transaction already exists
    if (TXN_ROOT / 'transaction.json').exists():
        raise RuntimeError('Existing candidate transaction present; rollback first')

    data = json.loads((TXN_ROOT / 'prepared.json').read_text())

    # Verify prepared.json pins match current state
    ref, comp = _guard_target()
    if ref.get_path_name() != data['reference_path']:
        raise RuntimeError('Reference actor path changed since prepare')
    if data['mesh_path'] != MESH_PATH or data['target_slot_index'] != TARGET_MATERIAL_INDEX or data['target_slot_name'] != TARGET_SLOT_NAME:
        raise RuntimeError('Prepared data mesh/slot mismatch')
    if data['source_id'] != SOURCE_ID_FULL:
        raise RuntimeError('Prepared data source_id mismatch')

    # Verify candidate material still valid
    candidate_path = data['candidate_material']
    tag = _candidate_tag()
    name = 'M_StoneGrid_' + tag
    expected_path = CANDIDATE_ROOT + '/Materials/' + name
    if data.get('candidate_tag') != tag or candidate_path not in (expected_path, expected_path + '.' + name):
        raise RuntimeError('Prepared candidate hash/version mismatch')
    if not unreal.EditorAssetLibrary.does_asset_exist(candidate_path):
        raise RuntimeError('Candidate material missing: ' + candidate_path)
    candidate_mat = unreal.load_asset(candidate_path)
    if not candidate_mat:
        raise RuntimeError('Candidate material not loadable: ' + candidate_path)

    if not candidate_path.startswith(CANDIDATE_ROOT + '/Materials/'):
        raise RuntimeError('Candidate outside owned folder')
    current = comp.get_material(TARGET_MATERIAL_INDEX)
    if not current or current.get_path_name() != ORIGINAL_MATERIAL_PATH:
        raise RuntimeError('Slot material not original')

    # Snapshot ALL actors BEFORE any mutation (including reference actor)
    before = _snapshot()

    # Capture original reference actor state for transaction
    ref_path = ref.get_path_name()
    original_tags = list(ref.tags)
    original_materials = [m.get_path_name() if m else None for m in comp.get_materials()]

    # Fresh ownership token
    token = 'stone-pilot:' + str(uuid.uuid4())

    transaction = {
        'before': before,
        'token': token,
        'candidate_material': candidate_path,
        'reference_path': ref_path,
        'original_tags': original_tags,
        'original_materials': original_materials,
        'target_slot_index': TARGET_MATERIAL_INDEX,
        'original_material_path': ORIGINAL_MATERIAL_PATH
    }
    (TXN_ROOT / 'transaction.json').write_text(json.dumps(transaction, indent=2))

    try:
        # Apply material override on slot 16 only
        ref.modify()
        comp.modify()
        # Add our tags, preserve existing
        ref.tags = original_tags + [token, 'DYNACITY_STONE_MATERIAL_PILOT', 'ESTIMATED_NOT_SURVEYED', 'source:' + SOURCE_ID_FULL]
        comp.set_material(TARGET_MATERIAL_INDEX, candidate_mat)

        # Verify application
        applied = comp.get_material(TARGET_MATERIAL_INDEX)
        if not applied or applied.get_path_name() != candidate_path:
            raise RuntimeError('Material slot override failed')

        # Snapshot AFTER - compare with before, allowing ONLY the target slot change on reference actor
        after = _snapshot()

        expected = copy.deepcopy(before)
        expected[ref_path]['materials'][TARGET_MATERIAL_INDEX] = candidate_path
        if 'tags' in expected[ref_path]:
            expected[ref_path]['tags'] = list(ref.tags)
        if after != expected:
            raise RuntimeError('Unexpected actor state changed during apply')

        result = {
            'existing_actors_exact': True,
            'existing_actor_count': len(before),
            'slot_index': TARGET_MATERIAL_INDEX,
            'slot_name': TARGET_SLOT_NAME,
            'original_material': ORIGINAL_MATERIAL_PATH,
            'candidate_material': candidate_path,
            'scene_saved': False
        }
        (TXN_ROOT / 'apply-report.json').write_text(json.dumps(result, indent=2))
        return result
    except Exception as error:
        # Retain recovery evidence if cleanup fails; never overwrite a foreign material.
        recovered = False
        try:
            current = comp.get_material(TARGET_MATERIAL_INDEX)
            current_path = current.get_path_name() if current else None
            if token in ref.tags and current_path in (candidate_path, ORIGINAL_MATERIAL_PATH):
                if current_path == candidate_path:
                    comp.set_material(TARGET_MATERIAL_INDEX, unreal.load_asset(ORIGINAL_MATERIAL_PATH))
                ref.tags = original_tags
                recovered = _snapshot() == before
            elif token not in ref.tags:
                recovered = _snapshot() == before
        except Exception as cleanup_error:
            error.add_note('Cleanup failed; transaction retained: ' + str(cleanup_error))
        if recovered:
            (TXN_ROOT / 'transaction.json').unlink(missing_ok=True)
        else:
            error.add_note('Recovery incomplete; transaction retained')
        raise


def rollback():
    """Restore original material and tags; verify exact state restoration."""
    _guard_environment()

    if not (TXN_ROOT / 'transaction.json').exists():
        raise RuntimeError('No transaction to rollback')

    transaction = json.loads((TXN_ROOT / 'transaction.json').read_text())
    token = transaction['token']
    before = transaction['before']
    candidate_mat_path = transaction['candidate_material']
    ref_path = transaction['reference_path']
    original_tags = transaction['original_tags']
    original_materials = transaction['original_materials']
    target_slot = transaction['target_slot_index']
    original_mat_path = transaction['original_material_path']

    # Find reference actor by PATH (not label, to handle label changes)
    ref = None
    for a in bundle.ci.city_world._actors():
        if a.get_path_name() == ref_path:
            ref = a
            break
    if not ref:
        raise RuntimeError('Reference actor missing during rollback (path: ' + ref_path + ')')

    comp = bundle.ci._component(ref)
    if not comp:
        raise RuntimeError('Reference actor missing component')

    # Verify ownership: current candidate material must match transaction AND token present
    current_mat = comp.get_material(target_slot)
    if not current_mat or current_mat.get_path_name() != candidate_mat_path:
        raise RuntimeError('Current material does not match transaction candidate; refusing rollback')
    if token not in ref.tags:
        raise RuntimeError('Ownership token missing; refusing rollback')

    if comp.static_mesh.get_path_name() != MESH_PATH or target_slot != TARGET_MATERIAL_INDEX or original_mat_path != ORIGINAL_MATERIAL_PATH:
        raise RuntimeError('Transaction binding mismatch; refusing rollback')
    # Restore original material on target slot only
    ref.modify()
    comp.modify()
    comp.set_material(target_slot, unreal.load_asset(original_mat_path))

    # Restore exact original tags (remove only our additions)
    ref.tags = original_tags

    # Verify exact state restoration
    after = _snapshot()
    original_exact = before == after
    slot_restored = comp.get_material(target_slot).get_path_name() == original_mat_path

    result = {
        'original_static_actors_exact': original_exact,
        'original_static_actor_count': len(before),
        'preview_absent': True,
        'slot_restored': slot_restored,
        'scene_saved': False
    }

    (TXN_ROOT / 'rollback-report.json').write_text(json.dumps(result, indent=2))

    if not result['original_static_actors_exact'] or not result['slot_restored']:
        raise RuntimeError('Rollback mismatch; original state not restored exactly')

    # Clean up transaction file
    (TXN_ROOT / 'transaction.json').unlink(missing_ok=True)
    return result


def stage(action):
    return {'preflight': preflight, 'prepare': prepare, 'apply': apply, 'rollback': rollback}[action]()


# --- Pure CPU grid evaluation for tests (must match shader logic) ---
def cpu_grid_eval(world_pos_cm, vertex_normal_ws):
    """CPU reference implementation of grid logic.

    Args:
        world_pos_cm: (x, y, z) in Unreal cm
        vertex_normal_ws: (nx, ny, nz) world-space normal
    Returns:
        (is_joint, diffuse_color, joint_color) tuple
    """
    x, y, z = world_pos_cm
    nx, ny, nz = vertex_normal_ws

    # Dominant horizontal normal component: absX >= absY -> X-plane (use Y,Z)
    abs_nx = abs(nx)
    abs_ny = abs(ny)
    use_x_plane = abs_nx >= abs_ny  # Tie -> X-plane (matches shader A==B -> 1)

    # Horizontal coordinate in metres (UE cm -> m)
    if use_x_plane:
        h_coord_m = y / 100.0  # Y coordinate for X-plane
    else:
        h_coord_m = x / 100.0  # X coordinate for Y-plane

    v_coord_m = z / 100.0  # Z always vertical

    # Grid parameters in metres
    width = GRID_WIDTH_M
    height = GRID_HEIGHT_M
    joint = JOINT_WIDTH_M

    # Joint test: frac(coord / size) < joint/size -> joint (1)
    h_frac = (h_coord_m / width) % 1.0
    h_in_joint = h_frac < (joint / width)

    v_frac = (v_coord_m / height) % 1.0
    v_in_joint = v_frac < (joint / height)

    is_joint = h_in_joint or v_in_joint

    if is_joint:
        return True, JOINT_COLOR, JOINT_COLOR
    else:
        return False, DIFFUSE_COLOR, JOINT_COLOR


def cpu_grid_eval_batch(points_cm, normals_ws):
    """Batch CPU evaluation for test vectors."""
    return [cpu_grid_eval(p, n) for p, n in zip(points_cm, normals_ws)]
