"""Bounded city import operations exposed to the local official Unreal MCP."""
import json
import unreal
import toolset_registry


@unreal.uclass()
class CityTools(unreal.ToolsetDefinition):
    @toolset_registry.tool_call
    @staticmethod
    def status() -> str:
        import city_world
        return json.dumps(city_world.status())

    @toolset_registry.tool_call
    @staticmethod
    def advance() -> str:
        """Finalize a finished package, then start the next. Busy imports are untouched."""
        import city_world
        city_world._current()
        if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
            return json.dumps(city_world.status())
        city_world.finalize()
        city_world.start_next()
        return json.dumps(city_world.status())

    @toolset_registry.tool_call
    @staticmethod
    def replace_roads(build_manifest: str, surface_manifest: str) -> str:
        """Remove imported road batches and queue rebuilt ones; continue with advance."""
        import city_world
        return json.dumps(city_world.replace_roads(build_manifest,surface_manifest))

    @toolset_registry.tool_call
    @staticmethod
    def style(asset_folder: str) -> str:
        """Reapply the licensed surface finishes to the loaded city."""
        import style_city
        return json.dumps(style_city.apply(asset_folder))

    @toolset_registry.tool_call
    @staticmethod
    def collision() -> str:
        import city_world,city_collision
        city_world._current()
        result=city_collision.configure_and_save()
        return json.dumps(result)


def register():
    global _registration
    from toolset_registry.registration import Registration
    _registration=Registration([CityTools]);_registration.register()
