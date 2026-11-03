"""Saved map choices are shared by the native HUD, atlas and editor-only panel."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PYTHON = Path(__file__).resolve().parents[1] / 'engine/unreal/Content/Python'
sys.path.insert(0, str(PYTHON))
import map_preferences


def test_default_is_crisp_street_without_creating_a_file(tmp_path):
    path = tmp_path / 'map-preferences.json'
    assert map_preferences.load(path) == {'version': 1, 'mode': 'street', 'heading_up': True}
    assert not path.exists()


def test_round_trip_reopen_and_partial_updates(tmp_path):
    path = tmp_path / 'DynaCity/map-preferences.json'
    first = map_preferences.merge(map_preferences.load(path), {'mode': 'hybrid'})
    map_preferences.save(path, first)
    assert map_preferences.load(path) == first
    second = map_preferences.merge(map_preferences.load(path), {'heading_up': False})
    map_preferences.save(path, second)
    assert json.loads(path.read_text()) == {'version': 1, 'mode': 'hybrid', 'heading_up': False}
    assert not list(path.parent.glob('*.tmp'))


@pytest.mark.parametrize('patch', [{}, {'mode': 'aerial'}, {'heading_up': 1}, {'mode': None},
                                   {'heading_up': 'false'}, {'path': 'elsewhere'}, {'version': 2}])
def test_invalid_patch_is_rejected(patch):
    with pytest.raises(ValueError):
        map_preferences.merge(map_preferences.defaults(), patch)


def test_corrupt_or_future_file_is_not_silently_reported_as_saved(tmp_path):
    path = tmp_path / 'map-preferences.json'
    for text in ('{', 'null', '{"version":2,"mode":"street","heading_up":true}'):
        path.write_text(text)
        with pytest.raises(ValueError):
            map_preferences.load(path)


def test_failed_atomic_replace_keeps_previous_file_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path / 'map-preferences.json'
    map_preferences.save(path, map_preferences.defaults())
    old = path.read_bytes()
    def denied(*args):
        raise PermissionError('Disk is read-only')
    monkeypatch.setattr(map_preferences.os, 'replace', denied)
    with pytest.raises(PermissionError):
        map_preferences.save(path, {**map_preferences.defaults(), 'mode': 'hybrid'})
    assert path.read_bytes() == old
    assert list(tmp_path.iterdir()) == [path]


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'unreal', SimpleNamespace())
    spec = importlib.util.spec_from_file_location('control_panel_preferences_test', PYTHON / 'control_panel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_map_preferences_path', lambda: tmp_path / 'map-preferences.json')
    monkeypatch.setattr(module, '_game', lambda: (None, None))
    for name in ('explore_city', 'city_world', 'location_search', 'setup_driving'):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace())
    return module


def test_real_panel_action_saves_when_not_playing_and_reloads(panel):
    result = panel.execute(json.dumps({'action': 'map_settings', 'mode': 'satellite', 'heading_up': False}))
    assert result['map_settings'] == {'version': 1, 'mode': 'satellite', 'heading_up': False}
    assert panel.map_settings() == (result['map_settings'], '')
    panel.execute(json.dumps({'action': 'map_settings', 'mode': 'street'}))
    assert panel.map_settings()[0]['heading_up'] is False
    with pytest.raises(ValueError):
        panel.execute(json.dumps({'action': 'map_settings', 'mode': 'street', 'url': 'https://invalid'}))


def test_panel_uses_native_atomic_setter_once_and_reads_keyboard_changes(panel):
    calls = []
    current = {'mode': 'street', 'heading_up': True, 'preferences_error': ''}
    def save(mode, heading_up):
        calls.append((mode, heading_up))
        current.update(mode=mode, heading_up=heading_up)
        return True
    native = SimpleNamespace(status=lambda: json.dumps(current), set_preferences=save)
    pawn = SimpleNamespace(mobility=SimpleNamespace(minimap=native))
    assert panel.set_map_settings({'mode': 'hybrid'}, pawn)['mode'] == 'hybrid'
    assert calls == [('hybrid', True)]
    current.update(mode='satellite', heading_up=False)
    assert panel.map_settings(pawn)[0] == {'version': 1, 'mode': 'satellite', 'heading_up': False}
    assert len(calls) == 1


def test_failed_native_save_does_not_claim_success(panel):
    current = {'mode': 'street', 'heading_up': True, 'preferences_error': 'Cannot write preferences'}
    native = SimpleNamespace(status=lambda: json.dumps(current), set_preferences=lambda *args: False)
    pawn = SimpleNamespace(mobility=SimpleNamespace(minimap=native))
    with pytest.raises(RuntimeError, match='Cannot write preferences'):
        panel.set_map_settings({'mode': 'hybrid'}, pawn)
    assert panel.map_settings(pawn)[0]['mode'] == 'street'
    assert not panel._map_preferences_path().exists()


def test_panel_surfaces_corrupt_file_but_allows_user_to_repair(panel):
    panel._map_preferences_path().write_text('broken')
    selected, error = panel.map_settings()
    assert selected['mode'] == 'street'
    assert error.startswith('Could not load saved map settings:')
    panel.set_map_settings({'mode': 'hybrid'})
    assert panel.map_settings()[1] == ''
