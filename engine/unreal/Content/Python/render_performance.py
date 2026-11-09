"""Reversible rendering reductions for the current Unreal editor session."""
import unreal

# Runtime settings: no project renderer or shader permutation changes.
PROFILE = {
    'sg.ShadowQuality': 1,
    'sg.GlobalIlluminationQuality': 0,
    'sg.ReflectionQuality': 0,
    'sg.PostProcessQuality': 0,
    'sg.EffectsQuality': 1,
    'sg.FoliageQuality': 1,
    'r.ScreenPercentage': 80,
    'r.MotionBlurQuality': 0,
    'r.DepthOfFieldQuality': 0,
    'r.BloomQuality': 0,
    'r.VolumetricFog': 0,
}
_original = {}


def apply():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    if world is None:
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    for name, value in PROFILE.items():
        if name not in _original:
            _original[name] = unreal.SystemLibrary.get_console_variable_float_value(name)
        unreal.SystemLibrary.execute_console_command(world, f'{name} {value}')
    actual = {name: unreal.SystemLibrary.get_console_variable_float_value(name) for name in PROFILE}
    return {'scope': 'current editor session', 'requested': PROFILE, 'actual': actual,
            'verified': all(abs(actual[k] - v) < 0.01 for k, v in PROFILE.items()),
            'previous': dict(_original)}


def restore():
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    if world is None:
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    for name, value in _original.items():
        unreal.SystemLibrary.execute_console_command(world, f'{name} {value}')
    _original.clear()
