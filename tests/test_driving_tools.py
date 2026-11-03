"""Python controls forward to native driving APIs without running Unreal."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PYTHON = Path(__file__).resolve().parents[1] / 'engine/unreal/Content/Python'


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, PYTHON / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def controls(monkeypatch):
    pawn = Mock()
    pawn.get_drive_status.return_value = json.dumps({
        'fly_mode': True, 'recovery_available': False,
        'blocked_reason': 'No ground support',
    })
    world = object()
    subsystem = SimpleNamespace(get_game_world=lambda: world)
    unreal = SimpleNamespace(
        uclass=lambda: lambda cls: cls,
        ToolsetDefinition=object,
        UnrealEditorSubsystem=object,
        get_editor_subsystem=lambda cls: subsystem,
        GameplayStatics=SimpleNamespace(get_player_pawn=lambda current, index: pawn),
    )
    monkeypatch.setitem(sys.modules, 'unreal', unreal)
    monkeypatch.setitem(sys.modules, 'toolset_registry', SimpleNamespace(tool_call=lambda fn: fn))
    for name in ('explore_city', 'city_world', 'location_search', 'setup_driving'):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace())
    tools = load_module('driving_tools_test', 'driving_tools.py')
    panel = load_module('control_panel_driving_test', 'control_panel.py')
    return tools, panel, pawn, unreal


@pytest.mark.parametrize('enabled', [True, False])
def test_mcp_fly_sets_explicit_mode_and_preserves_native_status(controls, enabled):
    tools, _, pawn, _ = controls
    result = tools.DrivingTools.fly(enabled)
    pawn.set_fly_mode.assert_called_once_with(enabled)
    pawn.toggle_fly_mode.assert_not_called()
    assert result == pawn.get_drive_status.return_value
    assert json.loads(result) == {
        'fly_mode': True, 'recovery_available': False,
        'blocked_reason': 'No ground support',
    }


def test_mcp_fly_inputs_forward_floats_and_absolute_angles_unchanged(controls):
    tools, _, pawn, _ = controls
    values = (0.75, -0.5, 0.25, 271.5, -32.25, 1.0)
    assert tools.DrivingTools.fly_input(*values) == pawn.get_drive_status.return_value
    pawn.set_fly_inputs.assert_called_once_with(*values)


@pytest.mark.parametrize('action, method', [
    ('recover', 'recover_vehicle'), ('release', 'release_drive_inputs'),
    ('reset', 'reset_to_start'), ('next_area', 'next_area'),
])
def test_mcp_actions_use_native_methods_and_return_status(controls, action, method):
    tools, _, pawn, _ = controls
    assert getattr(tools.DrivingTools, action)() == pawn.get_drive_status.return_value
    getattr(pawn, method).assert_called_once_with()
    pawn.get_drive_status.assert_called_once_with()


@pytest.mark.parametrize('action, method', [
    ('fly', 'toggle_fly_mode'), ('recover', 'recover_vehicle'),
    ('reset', 'reset_to_start'), ('next_area', 'next_area'),
    ('camera', 'cycle_camera'),
])
def test_panel_actions_follow_existing_vehicle_action_style(controls, action, method):
    _, panel, pawn, _ = controls
    result = panel.execute(json.dumps({'action': action}))
    getattr(pawn, method).assert_called_once_with()
    assert result['message']
    assert [call[0] for call in pawn.method_calls] == [method]


@pytest.mark.parametrize('action', ['fly', 'recover'])
def test_panel_requires_active_pawn(controls, monkeypatch, action):
    _, panel, pawn, _ = controls
    monkeypatch.setattr(panel, '_game', lambda: (None, None))
    with pytest.raises(ValueError, match='Start driving'):
        panel.execute(json.dumps({'action': action}))
    assert not pawn.method_calls


@pytest.mark.parametrize('action, values', [
    ('fly', (True,)), ('fly_input', (0., 0., 0., 90., -10., 0.)),
    ('recover', ()), ('release', ()),
])
def test_mcp_requires_active_pawn(controls, action, values):
    tools, _, pawn, unreal = controls
    unreal.GameplayStatics.get_player_pawn = lambda world, index: None
    with pytest.raises(RuntimeError, match='Start the configured Beirut driving game'):
        getattr(tools.DrivingTools, action)(*values)
    assert not pawn.method_calls
