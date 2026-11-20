"""MCP tools for the 2026-11-20 open-data run: corridor canopy trees, FABDEM terrain extension, Open-Meteo weather.

Each tool dispatches to ONE fixed repository module and only to actions that module lists in its ACTIONS table.
Modules are reloaded per call so edits take effect without restarting the editor. No arbitrary code or paths are run.
"""
import importlib
import json
import traceback

import toolset_registry
import unreal

MODULES = {'canopy': 'corridor_canopy_estimate', 'terrain': 'terrain_fabdem_extension', 'weather': 'weather_open_meteo'}


def _dispatch(key, action, args_json):
    try:
        module = importlib.reload(importlib.import_module(MODULES[key]))
        if action not in module.ACTIONS:
            raise ValueError('Unsupported %s action %r; allowed: %s' % (key, action, sorted(module.ACTIONS)))
        kwargs = json.loads(args_json) if args_json else {}
        return json.dumps({'ok': True, 'result': module.ACTIONS[action](**kwargs)}, default=str)
    except Exception as error:
        return json.dumps({'ok': False, 'error': repr(error), 'trace': traceback.format_exc()[-3000:]})


@unreal.uclass()
class OpenDataTools(unreal.ToolsetDefinition):
    """Corridor canopy, FABDEM terrain extension and Open-Meteo weather operations (fixed action tables)."""

    @toolset_registry.tool_call
    @staticmethod
    def canopy(action: str, args_json: str) -> str:
        """Run one action of corridor_canopy_estimate (see its ACTIONS table)."""
        return _dispatch('canopy', action, args_json)

    @toolset_registry.tool_call
    @staticmethod
    def terrain(action: str, args_json: str) -> str:
        """Run one action of terrain_fabdem_extension (see its ACTIONS table)."""
        return _dispatch('terrain', action, args_json)

    @toolset_registry.tool_call
    @staticmethod
    def weather(action: str, args_json: str) -> str:
        """Run one action of weather_open_meteo (see its ACTIONS table)."""
        return _dispatch('weather', action, args_json)


def register():
    from toolset_registry.registration import Registration
    Registration([OpenDataTools]).register()
