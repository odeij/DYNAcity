"""Play must never rebuild assets because editor metadata is unavailable in PIE."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest


def load(monkeypatch, missing=None):
    material_class = type('MaterialInterface', (), {})
    materials = {name: material_class() for name in ('Clouds', 'Rear', 'Brake', 'Front', 'Indicator')}
    fake = types.ModuleType('unreal')
    fake.MaterialInterface = material_class
    fake.UnrealEditorSubsystem = object()
    fake.get_editor_subsystem = lambda _: types.SimpleNamespace(get_game_world=lambda: object())
    fake.load_asset = lambda path: None if path.rsplit('/', 1)[-1] == missing else materials[path.rsplit('/', 1)[-1]]
    # No MaterialEditingLibrary, AssetToolsHelpers or EditorAssetLibrary: touching
    # any editor-only API makes this test fail instead of silently accepting it.
    monkeypatch.setitem(sys.modules, 'unreal', fake)
    path = Path(__file__).parents[1]/'engine/unreal/Content/Python/ambient_effects.py'
    spec = importlib.util.spec_from_file_location('ambient_effects_runtime_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, materials


def test_play_loads_prepared_materials_without_editor_apis(monkeypatch):
    module, materials = load(monkeypatch)
    clouds, lamps = module.prepare_assets()
    assert clouds is materials['Clouds']
    assert lamps == [materials[name] for name in ('Rear', 'Brake', 'Front', 'Indicator')]


@pytest.mark.parametrize('missing', ['Clouds', 'Rear', 'Brake', 'Front', 'Indicator'])
def test_missing_play_assets_fail_without_creating_or_recompiling(monkeypatch, missing):
    module, _ = load(monkeypatch, missing)
    with pytest.raises(RuntimeError, match='outside Play'):
        module.prepare_assets()


def test_editor_reuses_versioned_assets_without_rebuilding(monkeypatch):
    module, materials = load(monkeypatch)
    fake = sys.modules['unreal']
    fake.get_editor_subsystem = lambda _: types.SimpleNamespace(get_game_world=lambda: None)
    fake.MaterialEditingLibrary = object()
    fake.AssetToolsHelpers = types.SimpleNamespace(get_asset_tools=lambda: object())
    calls = []
    fake.EditorAssetLibrary = types.SimpleNamespace(
        get_metadata_tag=lambda mat, key: calls.append((mat, key)) or module.CLOUD_VERSION)
    clouds, lamps = module.prepare_assets()
    assert clouds is materials['Clouds'] and len(lamps) == 4
    assert calls == [(clouds, 'DynaCityCloudVersion')]
