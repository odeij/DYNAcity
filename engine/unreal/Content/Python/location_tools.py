"""Local MCP verification for the same search and navigation used by the native UI."""
import json
import unreal
import toolset_registry


@unreal.uclass()
class LocationTools(unreal.ToolsetDefinition):
    @toolset_registry.tool_call
    @staticmethod
    def open_search() -> str:
        import location_search
        location_search.open_search()
        return json.dumps(location_search.status())

    @toolset_registry.tool_call
    @staticmethod
    def search(query: str) -> str:
        import location_search
        return json.dumps(location_search.search(query))

    @toolset_registry.tool_call
    @staticmethod
    def go_to(index: int) -> str:
        import location_search
        return json.dumps(location_search.go_to(index))

    @toolset_registry.tool_call
    @staticmethod
    def status() -> str:
        import location_search
        return json.dumps(location_search.status())


def register():
    global _registration
    from toolset_registry.registration import Registration
    _registration = Registration([LocationTools]); _registration.register()
    import location_search
    location_search.initialize()
