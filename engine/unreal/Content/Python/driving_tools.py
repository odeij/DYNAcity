"""Local MCP controls for verifying the actual runtime exploration vehicle."""
import json
import unreal
import toolset_registry


def _pawn():
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    pawn=unreal.GameplayStatics.get_player_pawn(world,0) if world else None
    if pawn is None or not hasattr(pawn,'get_drive_status'):
        raise RuntimeError('Start the configured Beirut driving game first')
    return pawn


@unreal.uclass()
class DrivingTools(unreal.ToolsetDefinition):
    @toolset_registry.tool_call
    @staticmethod
    def status() -> str:
        import setup_driving
        return json.dumps(setup_driving.status())

    @toolset_registry.tool_call
    @staticmethod
    def prepare(filename: str) -> str:
        import setup_driving
        return json.dumps(setup_driving.prepare(filename))

    @toolset_registry.tool_call
    @staticmethod
    def play() -> str:
        import setup_driving
        return json.dumps(setup_driving.play())

    @toolset_registry.tool_call
    @staticmethod
    def stop() -> str:
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_end_play()
        return 'End Play requested'

    @toolset_registry.tool_call
    @staticmethod
    def input(throttle: float, steering: float, brake: bool) -> str:
        pawn=_pawn();pawn.set_drive_inputs(throttle,steering,brake)
        return pawn.get_drive_status()

    @toolset_registry.tool_call
    @staticmethod
    def fly(enabled: bool) -> str:
        """Enable free flight, or return to the parked car at departure (not land)."""
        pawn=_pawn();pawn.set_fly_mode(enabled)
        return pawn.get_drive_status()

    @toolset_registry.tool_call
    @staticmethod
    def fly_input(forward: float, right: float, up: float, yaw: float, pitch: float, boost: float) -> str:
        """Set view-relative flight inputs; yaw/pitch are absolute angles in degrees."""
        pawn=_pawn();pawn.set_fly_inputs(forward,right,up,yaw,pitch,boost)
        return pawn.get_drive_status()

    @toolset_registry.tool_call
    @staticmethod
    def recover() -> str:
        """Recover to a recent safe ground checkpoint near the car, or fall back to start."""
        pawn=_pawn();pawn.recover_vehicle()
        return pawn.get_drive_status()

    @toolset_registry.tool_call
    @staticmethod
    def release() -> str:
        """Clear both external driving and flight inputs."""
        pawn=_pawn();pawn.release_drive_inputs()
        return pawn.get_drive_status()

    @toolset_registry.tool_call
    @staticmethod
    def reset() -> str:
        pawn=_pawn();pawn.reset_to_start()
        return pawn.get_drive_status()

    @toolset_registry.tool_call
    @staticmethod
    def next_area() -> str:
        pawn=_pawn();pawn.next_area()
        return pawn.get_drive_status()

    @toolset_registry.tool_call
    @staticmethod
    def camera() -> str:
        pawn=_pawn();pawn.cycle_camera()
        return pawn.get_drive_status()


def register():
    global _registration
    from toolset_registry.registration import Registration
    _registration=Registration([DrivingTools]);_registration.register()
