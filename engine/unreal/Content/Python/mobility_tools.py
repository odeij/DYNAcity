"""Bounded native mobility verification through Epic's local MCP server."""
import json
import unreal
import toolset_registry


@unreal.uclass()
class MobilityTools(unreal.ToolsetDefinition):
    @toolset_registry.tool_call
    @staticmethod
    def install_real_traffic_fleet(folder: str) -> str:
        import traffic_fleet
        return json.dumps(traffic_fleet.install_real(folder))

    @toolset_registry.tool_call
    @staticmethod
    def install_traffic_fleet(folder: str) -> str:
        import traffic_fleet
        return json.dumps(traffic_fleet.install(folder))

    @toolset_registry.tool_call
    @staticmethod
    def install_library_assets(folder: str) -> str:
        import importlib,library_assets
        importlib.reload(library_assets)
        result=library_assets.install(folder)
        return json.dumps({'vehicle':result['vehicle']['mesh'],'replaced_groups':len(result['replaced']),
                           'retired_groups':result['retired_groups'],'manifest':result['active_manifest']})

    @toolset_registry.tool_call
    @staticmethod
    def status() -> str:
        import mobility_runtime,control_panel
        import library_assets
        try:
            car=library_assets.active()['vehicle']
            assets={'vehicle':car['mesh'],'handedness':car['vehicle']['handedness'],
                    'steering_y_cm':car['vehicle']['steering_ground_cm'][1],
                    'eye_y_cm':car['vehicle']['eye_chassis_cm'][1],'source_triangles':car['source_triangles']}
            world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
            pawn=unreal.GameplayStatics.get_player_pawn(world,0) if world else None
            if pawn:
                scale=pawn.cockpit.get_editor_property('relative_scale3d')
                assets['cockpit_scale']=[scale.x,scale.y,scale.z]
        except (OSError,RuntimeError):assets={}
        return json.dumps({'mobility':mobility_runtime.status(),'controls':control_panel.state(),'assets':assets})

    @toolset_registry.tool_call
    @staticmethod
    def build_reference(asset_folder: str) -> str:
        import importlib,reference_street
        importlib.reload(reference_street)
        return json.dumps(reference_street.build(asset_folder))

    @toolset_registry.tool_call
    @staticmethod
    def night(enabled: bool) -> str:
        from driving_tools import _pawn
        pawn=_pawn();pawn.set_night_mode(enabled)
        return pawn.get_drive_status()

    @toolset_registry.tool_call
    @staticmethod
    def minimap(mode: str, heading_up: bool) -> str:
        """Set the same native HUD options as M and B, without touching traffic."""
        from driving_tools import _pawn
        if mode not in ('street', 'satellite', 'hybrid'):
            raise ValueError('Choose street, satellite or hybrid.')
        pawn = _pawn()
        if pawn.mobility is None:
            raise RuntimeError('Wait for the geographic map to initialize.')
        import control_panel
        control_panel.set_map_settings({'mode': mode, 'heading_up': heading_up}, pawn)
        return pawn.mobility.status()

    @toolset_registry.tool_call
    @staticmethod
    def map_settings(mode: str, heading_up: bool) -> str:
        """Use the real panel action, including saving while Play is stopped."""
        import control_panel
        return json.dumps(control_panel.execute(json.dumps({
            'action': 'map_settings', 'mode': mode, 'heading_up': heading_up})))

    @toolset_registry.tool_call
    @staticmethod
    def traffic(enabled: bool) -> str:
        import mobility_runtime
        if enabled:mobility_runtime.start()
        else:mobility_runtime.stop()
        return json.dumps(mobility_runtime.status())


def register():
    global _registration
    from toolset_registry.registration import Registration
    _registration=Registration([MobilityTools]);_registration.register()
