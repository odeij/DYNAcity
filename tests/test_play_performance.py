"""Editor throttling overrides must not overwrite the user's saved preferences."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.mark.parametrize('initial', [True, False])
def test_play_override_restores_original_settings(monkeypatch, initial):
    values = {'bThrottleCPUWhenNotForeground': initial, 'bAllowSlateThrottling': True}
    settings = types.SimpleNamespace(
        get_editor_property=values.__getitem__, set_editor_property=values.__setitem__)
    monkeypatch.setitem(sys.modules, 'unreal', types.SimpleNamespace(
        load_class=lambda *_: object(), get_default_object=lambda _: settings))
    path = Path(__file__).parents[1] / 'engine/unreal/Content/Python/play_performance.py'
    spec = importlib.util.spec_from_file_location('play_performance_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.set_playing(True)
    module.set_playing(True)  # Repeated ticks must retain the original values.
    assert not any(values.values())
    module.set_playing(False)
    assert values == {'bThrottleCPUWhenNotForeground': initial, 'bAllowSlateThrottling': True}
    assert not module.status()['play_throttling_override']
