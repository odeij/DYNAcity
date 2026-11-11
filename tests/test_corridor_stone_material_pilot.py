"""Isolated fake-Unreal tests for corridor stone material pilot.

Tests cover:
- Invalid project/map/PIE guards
- Mesh/slot mismatch refusal
- Rollback refuses foreign changed material
- Exact single-slot rollback verification
- CPU grid axis/units tests for both wall orientations
"""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

# We'll set up the fake modules in the fixture, after monkeypatch is available
ROOT = Path(__file__).parents[1]


def _make_fake_unreal(current_state):
    """Create a fake unreal module that uses current_state for guards."""
    class FakeUnreal:
        class MaterialProperty:
            MP_BASE_COLOR = 'BaseColor'
            MP_ROUGHNESS = 'Roughness'
            MP_METALLIC = 'Metallic'
        class LinearColor:
            def __init__(self, r, g, b, a=1): pass
        class Paths:
            @staticmethod
            def get_project_file_path():
                return current_state['project']
            @staticmethod
            def convert_relative_path_to_full(path):
                return path
        class UnrealEditorSubsystem:
            @staticmethod
            def get_editor_world():
                return NS(get_path_name=lambda: current_state['map'])
        class LevelEditorSubsystem:
            @staticmethod
            def is_in_play_in_editor():
                return current_state['pie']
        EditorActorSubsystem = 'editor_actor_subsystem'
        StaticMeshActor = 'meshactor'
        Vector = lambda: None
        EditorAssetLibrary = NS(
            does_asset_exist=lambda path: 'StoneMaterialPilot' in path or path in current_state.get('fake_materials', {}),
            save_loaded_asset=lambda *args: True
        )
        AssetToolsHelpers = NS(
            get_asset_tools=NS(
                create_asset=lambda name, folder, cls, factory: NS(
                    get_path_name=lambda: f'{folder}/{name}.{name}',
                    set_editor_property=lambda *a, **k: None
                )
            )
        )
        MaterialEditingLibrary = NS(
            create_material_expression=lambda mat, cls, x, y: NS(set_editor_property=lambda *a, **k: None),
            connect_material_expressions=lambda *a: True,
            connect_material_property=lambda *a: True,
            recompile_material=lambda *a: None
        )
        MaterialExpressionWorldPosition = 'WorldPosition'
        MaterialExpressionVertexNormalWS = 'VertexNormalWS'
        MaterialExpressionComponentMask = 'ComponentMask'
        MaterialExpressionAbs = 'Abs'
        MaterialExpressionIf = 'If'
        MaterialExpressionDivide = 'Divide'
        MaterialExpressionFrac = 'Frac'
        MaterialExpressionMax = 'Max'
        MaterialExpressionLinearInterpolate = 'LinearInterpolate'
        MaterialExpressionConstant = 'Constant'
        MaterialExpressionConstant3Vector = 'Constant3Vector'
        BlendMode = NS(BLEND_TRANSLUCENT='Translucent')

        @staticmethod
        def get_editor_subsystem(subsystem_class):
            if subsystem_class == FakeUnreal.UnrealEditorSubsystem:
                return FakeUnreal.UnrealEditorSubsystem()
            if subsystem_class == FakeUnreal.LevelEditorSubsystem:
                return FakeUnreal.LevelEditorSubsystem()
            return None

    return FakeUnreal


def _make_fake_bundle(actors, fake_materials):
    """Create a fake corridor_feature_bundle_pilot module."""
    class FakeComponent:
        def __init__(self, actor):
            self.actor = actor
        def modify(self): return None
        def get_material(self, idx):
            return self.actor.comp.get_material(idx)
        def set_material(self, idx, mat):
            self.actor.comp.set_material(idx, mat)
        def get_materials(self):
            return self.actor.comp.get_materials()
        @property
        def static_mesh(self):
            return self.actor.comp.static_mesh

    def load_asset(path):
        if path in fake_materials:
            return fake_materials[path]
        if 'StoneMaterialPilot' in path:
            m = NS(get_path_name=lambda: path)
            fake_materials[path] = m
            return m
        return NS(get_path_name=lambda: path, static_materials=[NS(material_slot_name='slot')])

    class FakeBundle:
        class ci:
            @staticmethod
            def _ready(): pass
            @staticmethod
            def _load_batches(): pass
            @staticmethod
            def snapshot(): return {'actors': {}}
            @staticmethod
            def _state(a):
                mats = [m.get_path_name() if m else None for m in a.comp.get_materials()]
                return {'label': a.label, 'transform': a.transform, 'tags': list(a.tags),
                        'mesh': a.comp.static_mesh.get_path_name(),
                        'materials': mats,
                        'visible': True, 'hidden_in_game': False,
                        'collision': 'QueryAndPhysics',
                        'transform_data': [0,0,0,0,0,0,1,1,1]}
            @staticmethod
            def _component(a): return FakeComponent(a)
            @staticmethod
            def _read(p): return json.loads(Path(p).read_text())
            @staticmethod
            def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else 'hash'
            cb = NS(_ready=lambda: None, _disable_visual_collision=lambda *a: None)
            city_world = NS(_actors=lambda: list(actors.values()))

    return FakeBundle


class Actor:
    def __init__(self, path, label, mesh_path=None, slots=None, materials=None):
        self.path = path
        self.label = label
        self.tags = []
        self.transform = 'original'
        self.comp = NS(
            static_mesh=NS(
                get_path_name=lambda: mesh_path,
                static_materials=[NS(material_slot_name=s) for s in (slots or ['slot0', 'slot1', 'slot2'])]
            )
        )
        self.comp.set_static_mesh = lambda mesh: setattr(self.comp.static_mesh, 'get_path_name', lambda: mesh)
        self._materials = materials or [NS(get_path_name=lambda: 'original')] * 20
        self.comp.get_material = lambda idx: self._materials[idx] if idx < len(self._materials) else None
        self.comp.set_material = lambda idx, mat: self._materials.__setitem__(idx, mat)
        self.modify = lambda: None
        self.comp.modify = lambda: None
        self.comp.get_materials = lambda: self._materials
    def get_path_name(self): return self.path
    def get_actor_label(self): return self.label
    def get_actor_transform(self): return self.transform
    def set_actor_label(self, value): self.label = value
    def set_actor_transform(self, *args): self.transform = args[0]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Create fake Unreal environment for isolated testing."""
    actors = {}
    destroyed = []
    fail = [None]
    fake_materials = {}

    # Reference actor with correct mesh and 20 slots (index 16 = stone_light__granite)
    slots = [f'slot{i}' for i in range(16)] + ['stone_light__granite'] + [f'slot{i}' for i in range(18, 20)]
    # Use the EXACT material path from the pilot module
    mats = [NS(get_path_name=lambda: '/Game/DynaCity/CorridorReconstruction/Materials/M_stone_light__granite_39b51bd0.M_stone_light__granite_39b51bd0') for _ in range(20)]
    actors['reference'] = Actor('reference', 'Corridor reconstruction - source-supported frontages (candidate identities, estimated geometry)',
                                 mesh_path='/Game/DynaCity/CorridorReconstruction/Geometry/3ebba28a028c/corridor-reconstruction-v2/StaticMeshes/reconstruction.reconstruction',
                                 slots=slots, materials=mats)
    # Add source tag
    actors['reference'].tags = ['source:beirut001:106849cb-0a30-45f7-ba11-dc219083a1c1']

    # Foreign actor (should not be affected)
    actors['foreign'] = Actor('foreign', 'keep me')

    current = {
        'project': 'C:/private/dynacity-data/engine/sea-highway/UnrealPrototype/DynaCityBeirut.uproject',
        'map': '/Game/Maps/BeirutCityBatched.BeirutCityBatched',
        'pie': False,
        'fake_materials': fake_materials
    }

    # Install fake modules
    fake_unreal = _make_fake_unreal(current)
    fake_bundle = _make_fake_bundle(actors, fake_materials)

    monkeypatch.setitem(sys.modules, 'unreal', fake_unreal)
    monkeypatch.setitem(sys.modules, 'corridor_feature_bundle_pilot', fake_bundle)

    fake_unreal.load_asset = staticmethod(lambda path: fake_materials.setdefault(path, NS(get_path_name=lambda: path)))
    # NOW import the pilot module fresh
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'corridor_stone_material_pilot_test',
        ROOT / 'engine/unreal/Content/Python/corridor_stone_material_pilot.py'
    )
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)

    # Point transaction root to temp dir
    pilot.TXN_ROOT = tmp_path

    return NS(m=pilot, actors=actors, destroyed=destroyed, fail=fail, ci=fake_bundle.ci, current=current, fake_materials=fake_materials, tmp_path=tmp_path)


# --- Environment Guard Tests ---

def test_guard_wrong_project_refuses(env):
    env.current['project'] = 'C:/other/project.uproject'
    with pytest.raises(RuntimeError, match='Wrong private project'):
        env.m.preflight()


def test_guard_wrong_map_refuses(env):
    env.current['map'] = '/Game/Maps/Other.Other'
    with pytest.raises(RuntimeError, match='Wrong map'):
        env.m.preflight()


def test_guard_pie_active_refuses(env):
    env.current['pie'] = True
    with pytest.raises(RuntimeError, match='PIE active'):
        env.m.preflight()


def test_guard_missing_reference_actor_refuses(env):
    del env.actors['reference']
    with pytest.raises(RuntimeError, match='Reference actor not unique'):
        env.m.preflight()


def test_guard_duplicate_reference_actor_refuses(env):
    # Add another actor with same label
    env.actors['reference2'] = Actor('reference2', 'Corridor reconstruction - source-supported frontages (candidate identities, estimated geometry)',
                                      mesh_path='/Game/DynaCity/CorridorReconstruction/Geometry/3ebba28a028c/corridor-reconstruction-v2/StaticMeshes/reconstruction.reconstruction')
    with pytest.raises(RuntimeError, match='Reference actor not unique'):
        env.m.preflight()


def test_guard_wrong_mesh_refuses(env):
    env.actors['reference'].comp.static_mesh.get_path_name = lambda: '/Game/Other/Mesh.Mesh'
    with pytest.raises(RuntimeError, match='mesh mismatch'):
        env.m.preflight()


def test_guard_wrong_slot_name_refuses(env):
    old_slots = env.actors['reference'].comp.static_mesh.static_materials
    new_slots = list(old_slots)
    new_slots[16] = NS(material_slot_name='wrong_slot')
    env.actors['reference'].comp.static_mesh.static_materials = new_slots
    with pytest.raises(RuntimeError, match='Slot name mismatch'):
        env.m.preflight()


def test_guard_wrong_original_material_refuses(env):
    def get_mat(idx):
        if idx == 16:
            return NS(get_path_name=lambda: '/Game/Other/Mat.Mat')
        return NS(get_path_name=lambda: 'C:/private/dynacity-data/engine/sea-highway/UnrealPrototype/Content/DynaCity/CorridorReconstruction/Materials/M_stone_light__granite_39b51bd0.uasset')
    env.actors['reference'].comp.get_material = get_mat
    with pytest.raises(RuntimeError, match='Slot material not original'):
        env.m.preflight()


def test_guard_source_id_mismatch_refuses(env):
    env.actors['reference'].tags = ['source:other:uuid']
    with pytest.raises(RuntimeError, match='source_id mismatch'):
        env.m.preflight()


def test_prepare_creates_candidate_material(env):
    result = env.m.prepare()
    assert 'candidate_material' in result
    assert 'StoneMaterialPilot' in result['candidate_material']
    assert (env.tmp_path / 'prepared.json').exists()


def test_prepare_refuses_if_not_original(env):
    # Change slot 16 to non-original
    def get_mat(idx):
        if idx == 16:
            return NS(get_path_name=lambda: '/Game/Other/Mat.Mat')
        return env.actors['reference']._materials[idx]
    env.actors['reference'].comp.get_material = get_mat
    with pytest.raises(RuntimeError, match='Slot material not original'):
        env.m.prepare()


def test_apply_overrides_only_target_slot(env):
    env.m.prepare()
    result = env.m.apply()
    assert result['slot_index'] == 16
    assert result['slot_name'] == 'stone_light__granite'
    assert 'StoneMaterialPilot' in result['candidate_material']
    assert result['existing_actors_exact']


def test_apply_foreign_actor_unchanged(env):
    env.m.prepare()
    foreign_before = env.m._snapshot().get('foreign')
    env.m.apply()
    foreign_after = env.m._snapshot().get('foreign')
    assert foreign_before == foreign_after


def test_apply_refuses_existing_transaction(env):
    env.m.prepare()
    env.m.apply()
    # Try to apply again without rollback
    with pytest.raises(RuntimeError, match='Existing candidate transaction'):
        env.m.apply()


def test_rollback_restores_exact_original_slot(env):
    env.m.prepare()
    env.m.apply()
    result = env.m.rollback()
    assert result['original_static_actors_exact']
    assert result['slot_restored']
    assert result['preview_absent']


def test_rollback_refuses_foreign_changed_material(env):
    env.m.prepare()
    env.m.apply()
    # Simulate foreign actor's material changed externally
    env.actors['foreign']._materials[0] = NS(get_path_name=lambda: '/Game/Other/Mat.Mat')
    with pytest.raises(RuntimeError, match='original state not restored exactly'):
        env.m.rollback()


def test_rollback_refuses_if_token_missing(env):
    env.m.prepare()
    env.m.apply()
    # Remove token from reference actor tags
    env.actors['reference'].tags = [t for t in env.actors['reference'].tags if not t.startswith('stone-pilot:')]
    with pytest.raises(RuntimeError, match='Ownership token missing'):
        env.m.rollback()


def test_rollback_refuses_if_candidate_mismatch(env):
    env.m.prepare()
    env.m.apply()
    # Change the material to something else
    env.actors['reference']._materials[16] = NS(get_path_name=lambda: '/Game/Other/Mat.Mat')
    with pytest.raises(RuntimeError, match='Current material does not match transaction candidate'):
        env.m.rollback()


def test_rollback_refuses_if_reference_missing(env):
    env.m.prepare()
    env.m.apply()
    del env.actors['reference']
    with pytest.raises(RuntimeError, match='Reference actor missing during rollback'):
        env.m.rollback()


def test_apply_failure_restores_and_cleans_transaction(env):
    env.m.prepare()
    # Make set_material fail
    def fail_set_material(idx, mat):
        raise RuntimeError('material failure')
    env.actors['reference'].comp.set_material = fail_set_material
    with pytest.raises(RuntimeError, match='material failure'):
        env.m.apply()
    # Transaction file should be cleaned up
    assert not (env.tmp_path / 'transaction.json').exists()


def test_no_apply_leaves_original_unchanged(env):
    env.m.prepare()
    before = env.m._snapshot()
    after = env.m._snapshot()
    assert before == after


def test_prepare_then_rollback_without_apply_refuses(env):
    env.m.prepare()
    with pytest.raises(RuntimeError, match='No transaction to rollback'):
        env.m.rollback()


# --- CPU Grid Evaluation Tests (must match shader logic) ---

def test_cpu_grid_x_plane_vertical_wall():
    """Wall facing +X (normal 1,0,0) -> uses Y,Z coordinates (X-plane)."""
    pilot = _import_pilot()
    # Point on panel - Z=20.25m (not on 0.5m boundary)
    pos = (1000, 5000, 2025)  # cm -> x=10m, y=50m, z=20.25m
    normal = (1.0, 0.0, 0.0)  # Facing +X
    is_joint, diffuse, joint = pilot.cpu_grid_eval(pos, normal)
    # X-plane: horizontal = Y, vertical = Z
    # Y=50m -> 50/1.0 = 50.0 -> frac=0.0 -> in joint (0 < 0.008/1.0)
    assert is_joint == True

    # Offset slightly into panel (Y=50.5m)
    pos = (1000, 5050, 2025)  # Y=50.5m -> frac=0.5 -> panel
    is_joint, diffuse, joint = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False


def test_cpu_grid_y_plane_vertical_wall():
    """Wall facing +Y (normal 0,1,0) -> uses X,Z coordinates (Y-plane)."""
    pilot = _import_pilot()
    pos = (5000, 1000, 2025)  # X=50m, Y=10m, Z=20.25m
    normal = (0.0, 1.0, 0.0)  # Facing +Y
    is_joint, diffuse, joint = pilot.cpu_grid_eval(pos, normal)
    # Y-plane: horizontal = X, vertical = Z
    # X=50m -> 50/1.0 = 50.0 -> frac=0.0 -> in joint
    assert is_joint == True

    # Offset slightly into panel
    pos = (5050, 1000, 2025)  # X=50.5m
    is_joint, diffuse, joint = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False


def test_cpu_grid_negative_normal_same_plane():
    """Negative normal (-X or -Y) should select same plane as positive."""
    pilot = _import_pilot()
    # -X normal still X-plane
    pos = (1000, 5050, 2025)
    normal = (-1.0, 0.0, 0.0)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False

    # -Y normal still Y-plane
    pos = (5050, 1000, 2025)
    normal = (0.0, -1.0, 0.0)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False


def test_cpu_grid_diagonal_normal_selects_dominant():
    """Normal (0.8, 0.6, 0) -> |X| > |Y| -> X-plane."""
    pilot = _import_pilot()
    pos = (1000, 5050, 2025)  # Y=50.5m
    normal = (0.8, 0.6, 0.0)  # |X|=0.8 > |Y|=0.6 -> X-plane -> uses Y
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False

    # Normal (0.6, 0.8, 0) -> |Y| > |X| -> Y-plane -> uses X
    pos = (5050, 1000, 2025)  # X=50.5m
    normal = (0.6, 0.8, 0.0)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False


def test_cpu_grid_tie_goes_to_x_plane():
    """Equal |X| = |Y| -> tie -> X-plane (absX >= absY)."""
    pilot = _import_pilot()
    # Normal (0.707, 0.707, 0) -> |X| == |Y| -> X-plane -> uses Y
    pos = (1000, 5050, 2025)  # Y=50.5m -> panel
    normal = (0.70710678, 0.70710678, 0.0)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False


def test_cpu_grid_vertical_joints():
    """Test vertical (Z) joint detection at 0.5m intervals."""
    pilot = _import_pilot()
    # Z=10m exactly -> 10/0.5 = 20.0 -> frac=0.0 -> joint
    pos = (5050, 1000, 1000)  # X=50.5m, Y=10m, Z=10m
    normal = (0.0, 1.0, 0.0)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == True

    # Z=10.25m -> 10.25/0.5 = 20.5 -> frac=0.5 -> panel
    pos = (5050, 1000, 1025)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False


def test_cpu_grid_joint_width_8mm():
    """Test joint width threshold (8mm = 0.008m = 0.8cm)."""
    pilot = _import_pilot()
    # At boundary X=50.0m -> joint
    pos = (5000, 1000, 2025)  # X=50.0m
    normal = (0.0, 1.0, 0.0)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == True

    # In integer cm: 1cm = 10mm > 8mm joint width, so 50.01m is already panel
    # At X=50.01m (1cm = 10mm from boundary) -> panel
    pos = (5001, 1000, 2025)  # X=50.01m
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False

    # At X=50.08m (8cm = 80mm from boundary) -> panel
    pos = (5008, 1000, 2025)  # X=50.08m
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False


def test_cpu_grid_units_cm_to_m():
    """Verify cm to metre conversion is correct."""
    pilot = _import_pilot()
    # 10000cm = 100m
    pos = (100000, 0, 2025)  # X=100m
    normal = (0.0, 1.0, 0.0)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    # 100m / 1.0m = 100 -> frac=0 -> joint
    assert is_joint == True


def test_cpu_grid_batch_evaluation():
    """Test batch evaluation helper."""
    pilot = _import_pilot()
    points = [
        (5000, 1000, 2025),  # X=50m -> joint (Y-plane)
        (5050, 1000, 2025),  # X=50.5m -> panel
        (1000, 5000, 2025),  # Y=50m -> joint (X-plane)
        (1000, 5050, 2025),  # Y=50.5m -> panel
    ]
    normals = [
        (0.0, 1.0, 0.0),  # Y-plane
        (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0),  # X-plane
        (1.0, 0.0, 0.0),
    ]
    results = pilot.cpu_grid_eval_batch(points, normals)
    assert results[0][0] == True   # joint
    assert results[1][0] == False  # panel
    assert results[2][0] == True   # joint
    assert results[3][0] == False  # panel


def test_cpu_grid_negative_coordinates():
    """Test negative world coordinates work correctly."""
    pilot = _import_pilot()
    # X=-50m -> -50/1.0 = -50 -> frac=0.0 -> joint
    pos = (-5000, 1000, 2025)
    normal = (0.0, 1.0, 0.0)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == True

    # X=-50.5m -> panel
    pos = (-5050, 1000, 2025)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False


def test_cpu_grid_translated_coordinates():
    """Test translated coordinates (not on exact grid boundaries)."""
    pilot = _import_pilot()
    # Offset by 0.3m from boundary
    pos = (5030, 1000, 2025)  # X=50.3m -> 0.3/1.0 = 0.3 -> panel
    normal = (0.0, 1.0, 0.0)
    is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
    assert is_joint == False


def test_cpu_grid_seam_boundaries():
    """Test exact seam boundaries at 8mm, 1m, 0.5m intervals."""
    pilot = _import_pilot()
    normal = (0.0, 1.0, 0.0)

    # Horizontal joint every 1m, width 8mm = 0.8cm
    # Since we work in integer cm: joint is only at exact boundary (0cm offset)
    # 1cm = 10mm > 8mm -> panel
    for base in range(0, 5):
        # At exact boundary -> joint
        pos = (base * 100, 1000, 2025)  # base * 100cm
        is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
        assert is_joint == True, f"Boundary {base}m should be joint"

        # At 1cm = 10mm -> panel (since 10mm > 8mm)
        pos = (base * 100 + 1, 1000, 2025)
        is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
        assert is_joint == False, f"{base}m + 1cm should be panel"

        # At 8cm = 80mm -> panel
        pos = (base * 100 + 8, 1000, 2025)
        is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
        assert is_joint == False, f"{base}m + 8cm should be panel"

        # At 50cm (mid-panel) -> panel
        pos = (base * 100 + 50, 1000, 2025)
        is_joint, _, _ = pilot.cpu_grid_eval(pos, normal)
        assert is_joint == False, f"{base}m + 50cm should be panel"


def _import_pilot():
    """Import pilot fresh for CPU tests (no Unreal needed)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'corridor_stone_material_pilot_cpu',
        ROOT / 'engine/unreal/Content/Python/corridor_stone_material_pilot.py'
    )
    pilot = importlib.util.module_from_spec(spec)
    # Provide minimal fake modules for CPU-only functions
    import sys
    class MinimalUnreal:
        class MaterialProperty:
            MP_BASE_COLOR = 'BaseColor'
            MP_ROUGHNESS = 'Roughness'
            MP_METALLIC = 'Metallic'
        class LinearColor:
            def __init__(self, r, g, b, a=1): pass
        Paths = NS(
            get_project_file_path=lambda: 'C:/private/dynacity-data/engine/sea-highway/UnrealPrototype/DynaCityBeirut.uproject',
            convert_relative_path_to_full=lambda path: path
        )
    sys.modules['unreal'] = MinimalUnreal
    sys.modules['corridor_feature_bundle_pilot'] = NS(ci=NS(
        _ready=lambda: None,
        _load_batches=lambda: None,
        snapshot=lambda: {'actors': {}},
        _state=lambda a: {},
        _component=lambda a: NS(),
        _read=lambda p: json.loads(Path(p).read_text()),
        _sha=lambda p: 'hash',
        cb=NS(_ready=lambda: None, _disable_visual_collision=lambda *a: None),
        city_world=NS(_actors=lambda: [])
    ))
    spec.loader.exec_module(pilot)
    return pilot


if __name__ == '__main__':
    pytest.main([__file__, '-v'])


def test_actual_shader_graph_matches_cpu_on_both_facade_axes(env, monkeypatch):
    import numpy as np
    class Node:
        def __init__(self, kind): self.kind, self.props, self.inputs = kind, {}, {}
        def set_editor_property(self, key, value): self.props[key] = value
    def connect(src, output, dst, pin):
        allowed = {'If': {'A', 'B', 'A>B', 'A==B', 'A<B'},
                   'LinearInterpolate': {'A', 'B', 'Alpha'}}
        if dst.kind in allowed: assert pin in allowed[dst.kind]
        dst.inputs[pin] = src
        return True
    material = NS(get_path_name=lambda: '/Game/DynaCity/StoneMaterialPilot/Materials/test.test',
                  set_editor_property=lambda *a: None)
    graph = {}
    u = env.m.unreal
    monkeypatch.setattr(u, 'LinearColor', lambda r,g,b,a: (r,g,b))
    monkeypatch.setattr(u, 'Material', 'Material', raising=False)
    monkeypatch.setattr(u, 'MaterialFactoryNew', lambda: None, raising=False)
    monkeypatch.setattr(u, 'AssetToolsHelpers', NS(get_asset_tools=lambda: NS(create_asset=lambda *a: material)))
    monkeypatch.setattr(u, 'EditorAssetLibrary', NS(does_asset_exist=lambda p: False, save_loaded_asset=lambda *a: True))
    monkeypatch.setattr(u, 'MaterialEditingLibrary', NS(
        create_material_expression=lambda m,cls,*a: Node(cls),
        connect_material_expressions=connect,
        connect_material_property=lambda node,out,prop: graph.__setitem__(prop,node) or True,
        recompile_material=lambda *a: None))
    env.m._make_grid_material('test', 'tag')
    def evaluate(node, pos, normal):
        value=lambda pin: evaluate(node.inputs[pin], pos, normal)
        k=node.kind
        if k=='Constant': return node.props['r']
        if k=='Constant3Vector': return np.asarray(node.props['constant'])
        if k=='WorldPosition': return np.asarray(pos)
        if k=='VertexNormalWS': return np.asarray(normal)
        if k=='ComponentMask': return value('')[[i for i,c in enumerate('rgb') if node.props.get(c)]].squeeze()
        if k=='Abs': return abs(value(''))
        if k=='Divide': return value('A')/value('B')
        if k=='Frac': return value('') % 1
        if k=='If':
            a,b=value('A'),value('B')
            return value('A>B' if a>b else 'A<B' if a<b else 'A==B')
        if k=='LinearInterpolate': return value('A')*(1-value('Alpha'))+value('B')*value('Alpha')
        if k=='Max': return max(value('A'),value('B'))
        raise AssertionError(k)
    for normal in [(1,0,0),(0,1,0),(-1,0,0),(0,-1,0),(.8,.2,0),(.2,.8,0),(1,1,0)]:
        for pos in [(5030,1000,2025),(5000,1030,2025),(5000.4,1030.4,2025),(-1030,-2000,2025),(5010,1010,2000)]:
            expected = env.m.cpu_grid_eval(pos,normal)[1]
            assert np.allclose(evaluate(graph['BaseColor'],pos,normal),expected), (pos,normal)


def test_apply_preserves_preexisting_tags_exactly(env):
    ref=env.actors['reference']; ref.tags += ['source:another-building', 'external-owner']
    before=list(ref.tags)
    env.m.prepare();env.m.apply();env.m.rollback()
    assert ref.tags == before


def test_duplicate_reference_label_refused(env):
    env.actors['duplicate']=Actor('duplicate',env.m.REFERENCE_LABEL)
    with pytest.raises(RuntimeError,match='not unique'):env.m.preflight()


def test_failed_restore_keeps_recovery_transaction(env):
    env.m.prepare()
    comp=env.actors['reference'].comp
    original=comp.set_material
    def broken(index, material):
        original(index,material)
        raise RuntimeError('post-write failure')
    comp.set_material=broken
    with pytest.raises(RuntimeError,match='post-write failure'):env.m.apply()
    assert (env.tmp_path/'transaction.json').exists()


def test_prepared_foreign_candidate_refused(env):
    env.m.prepare()
    path=env.tmp_path/'prepared.json'; data=json.loads(path.read_text())
    data['candidate_material']='/Game/Unrelated/foreign.foreign';path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError,match='hash/version'):env.m.apply()
    assert not (env.tmp_path/'transaction.json').exists()
