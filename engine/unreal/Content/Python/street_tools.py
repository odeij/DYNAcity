"""Local Unreal tools for source-identifiable mapped street layers."""
import json
import unreal
import toolset_registry


@unreal.uclass()
class StreetTools(unreal.ToolsetDefinition):
    @toolset_registry.tool_call
    @staticmethod
    def profile_detail(samples: int) -> str:
        """Start asynchronous hidden/visible editor frame cadence comparison."""
        import street_detail
        return json.dumps(street_detail.profile_frames(samples))

    @toolset_registry.tool_call
    @staticmethod
    def detail_profile_status() -> str:
        import street_detail
        return json.dumps(street_detail.profile_status())

    @toolset_registry.tool_call
    @staticmethod
    def apply_detail(filename: str) -> str:
        """Regenerate only the isolated finished-module street-detail layer."""
        import street_detail
        return json.dumps(street_detail.apply(filename))

    @toolset_registry.tool_call
    @staticmethod
    def verify_detail(filename: str) -> str:
        """Save/reload verification of generated detail stable IDs."""
        import street_detail
        return json.dumps(street_detail.verify_reload(filename))

    @toolset_registry.tool_call
    @staticmethod
    def prepare_reference_drive() -> str:
        import street_context
        return json.dumps(street_context.prepare_reference_drive())

    @toolset_registry.tool_call
    @staticmethod
    def import_props(filename: str) -> str:
        import street_context
        return json.dumps(street_context.import_props(filename))

    @toolset_registry.tool_call
    @staticmethod
    def place_points(filename: str) -> str:
        import street_context
        return json.dumps(street_context.place_points(filename))

    @toolset_registry.tool_call
    @staticmethod
    def prepare_covers(filename: str) -> str:
        import street_context
        data=street_context.prepare_covers(filename)
        return json.dumps({'packages':len(data['packages']),'completed':len(data['completed'])})

    @toolset_registry.tool_call
    @staticmethod
    def advance_covers() -> str:
        import street_context
        return json.dumps(street_context.advance_covers())

    @toolset_registry.tool_call
    @staticmethod
    def status() -> str:
        import street_context
        return json.dumps(street_context.status())

    @toolset_registry.tool_call
    @staticmethod
    def focus_reference(filename: str) -> str:
        import street_context
        return json.dumps(street_context.focus_reference(filename))


def register():
    global _registration
    from toolset_registry.registration import Registration
    _registration=Registration([StreetTools]);_registration.register()
