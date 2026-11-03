"""Exercise editor-side fleet setup without loading the Unreal editor."""
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def runtime(monkeypatch):
    unreal = types.ModuleType('unreal')
    unreal.StaticMesh = type('StaticMesh', (), {})
    unreal.load_asset = lambda path: unreal.StaticMesh() if path == '/Game/car' else None
    monkeypatch.setitem(sys.modules, 'unreal', unreal)
    for name in ('location_search', 'explore_city', 'library_assets', 'play_performance', 'ambient_effects'):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules['play_performance'].set_playing = Mock()
    sys.modules['ambient_effects'].configure = Mock()
    sys.modules['location_search']._data = lambda: {}
    path = Path(__file__).resolve().parents[1] / 'engine/unreal/Content/Python/mobility_runtime.py'
    spec = importlib.util.spec_from_file_location('test_mobility_runtime', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._map = '{}'
    module.world_basis = lambda: (1, 2, 3, 4)
    yield module
    module._executor.shutdown(wait=True)


def attach(runtime, config):
    sys.modules['library_assets'].configure_vehicle = lambda pawn: config
    renderer = Mock()
    pawn = Mock()
    pawn.create_mobility_world.return_value = renderer
    runtime.attach(pawn)
    return renderer


def test_default_and_configured_fleet(runtime):
    renderer = attach(runtime, {})
    renderer.configure_traffic_fleet.assert_not_called()
    renderer = attach(runtime, {'traffic_fleet_meshes': ['/Game/car']})
    meshes = renderer.configure_traffic_fleet.call_args.args[0]
    assert len(meshes) == 1 and isinstance(meshes[0], sys.modules['unreal'].StaticMesh)
    renderer = attach(runtime, {'traffic_fleet_meshes': ['/Game/car'],
                                'traffic_type_groups': {'car': 0, 'bus': 1}})
    renderer.configure_traffic_types.assert_called_once_with({'car': 0, 'bus': 1})


@pytest.mark.parametrize('paths', ['', None, ['/Other/car'], ['/Game/missing'], ['/Game/car'] * 16])
def test_rejects_invalid_fleet(runtime, paths):
    with pytest.raises(ValueError):
        attach(runtime, {'traffic_fleet_meshes': paths})


@pytest.mark.parametrize('groups', [[], {'bus': 1}, {'bus': True}, {'': 0}])
def test_rejects_invalid_type_mapping(runtime, groups):
    with pytest.raises(ValueError):
        attach(runtime, {'traffic_type_groups': groups})


def test_background_mesh_can_differ_from_driven_vehicle(runtime):
    renderer = attach(runtime, {'traffic_default_mesh': '/Game/car'})
    assert isinstance(renderer.configure.call_args.args[-1], sys.modules['unreal'].StaticMesh)


@pytest.mark.parametrize('path', ['', 7, '/Other/car', '/Game/missing'])
def test_rejects_invalid_background_mesh(runtime, path):
    with pytest.raises(ValueError):
        attach(runtime, {'traffic_default_mesh': path})
