"""Keep editor-hosted Play responsive while its control window has focus."""
import unreal

_settings = None
_original = {}
_properties = ('bThrottleCPUWhenNotForeground', 'bAllowSlateThrottling')


def set_playing(playing):
    global _settings, _original
    if playing and not _original:
        cls = unreal.load_class(None, '/Script/UnrealEd.EditorPerformanceSettings')
        _settings = unreal.get_default_object(cls)
        # Read both before changing either, preserving the user's preferences.
        _original = {key: _settings.get_editor_property(key) for key in _properties}
        for key in _original:
            _settings.set_editor_property(key, False)
    elif not playing and _original:
        for key, value in _original.items():
            _settings.set_editor_property(key, value)
        _original = {}
        _settings = None


def status():
    return {'play_throttling_override': bool(_original), 'restores_on_end_play': True}
