"""Project-specific operations exposed through the official Unreal MCP plugin."""
import json
from pathlib import Path
import unreal
import toolset_registry


def _actors():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()


def _basis():
    markers={}
    for name in ('ORIGIN','EAST','NORTH','UP'):
        matches=[a for a in _actors() if a.get_actor_label().startswith('DYNACITY_BASIS_'+name)]
        if len(matches)!=1:raise RuntimeError('Missing/duplicate coordinate marker '+name)
        markers[name]=matches[0].get_actor_location()
    origin=markers['ORIGIN']
    axes=[(markers[name]-origin)/10 for name in ('EAST','NORTH','UP')]
    if any(not 99<axis.length()<101 for axis in axes):raise RuntimeError('Expected 100 cm per source metre')
    return origin,*axes


@unreal.uclass()
class DynaCityTools(unreal.ToolsetDefinition):
    """Import, validate and inspect the private Beirut prototype and SUMO replay."""

    @toolset_registry.tool_call
    @staticmethod
    def status() -> str:
        """Return scene counts, import state and sample replay positions."""
        import preview_traffic
        actors=_actors()
        sample=[{'id':key,'xyz_cm':[actor.get_actor_location().x,actor.get_actor_location().y,actor.get_actor_location().z]}
                for key,actor in list(preview_traffic._cars.items())[:3]]
        return json.dumps({'importing':unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active(),
            'actors':len(actors),'buildings':sum(a.get_actor_label().startswith('beirut001_') for a in actors),
            'replay_running':preview_traffic._callback is not None,'replay_paused':preview_traffic._paused_at is not None,'vehicles':len(preview_traffic._cars),
            'signals':len(preview_traffic._signals),'sample_vehicles':sample})

    @toolset_registry.tool_call
    @staticmethod
    def import_scene() -> str:
        """Load the prototype map and start its asynchronous GLB import if empty."""
        if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():return 'Import already active'
        import import_neighbourhood
        levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        exists=unreal.EditorAssetLibrary.does_asset_exist('/Game/Maps/BeirutPrototype')
        if exists:
            levels.load_level('/Game/Maps/BeirutPrototype')
            count=sum(a.get_actor_label().startswith('beirut001_') for a in _actors())
            if count==629:return 'Saved source scene loaded'
            if count:raise RuntimeError('Partial source scene; refusing a duplicate import')
        import_neighbourhood.build_from_config(reuse_level=exists)
        return 'Import started; query status before saving'

    @toolset_registry.tool_call
    @staticmethod
    def save_and_frame() -> str:
        """Validate all source buildings and metre scale, then save assets and map."""
        if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():raise RuntimeError('Import still active')
        if sum(a.get_actor_label().startswith('beirut001_') for a in _actors())!=629:raise RuntimeError('Expected 629 source buildings')
        origin,east,north,up=_basis()
        camera=origin+east*450-north*550+up*450
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(camera,unreal.MathLibrary.make_rot_from_x(origin+up*15-camera))
        if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():raise RuntimeError('Map save failed')
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(False,True)
        config=Path(__file__).resolve().parents[2]/'Config'/'DefaultEngine.ini'
        text=config.read_text(encoding='utf-8')
        if '[/Script/EngineSettings.GameMapsSettings]' not in text:
            config.write_text(text+'\n[/Script/EngineSettings.GameMapsSettings]\nEditorStartupMap=/Game/Maps/BeirutPrototype\nGameDefaultMap=/Game/Maps/BeirutPrototype\n',encoding='utf-8')
        return json.dumps({'saved':True,'buildings':629,'basis_cm_per_m':[[a.x,a.y,a.z] for a in (east,north,up)]})

    @toolset_registry.tool_call
    @staticmethod
    def start_replay() -> str:
        """Start recorded SUMO editor replay with transient placeholder actors."""
        import preview_traffic
        if preview_traffic._paused_at is not None:preview_traffic.resume()
        elif preview_traffic._callback is None:preview_traffic.start_from_config()
        return 'Recorded SUMO replay running; editor playback, not live traffic'

    @toolset_registry.tool_call
    @staticmethod
    def stop_replay() -> str:
        """Remove only the transient DynaCITY replay actors and callback."""
        import preview_traffic
        preview_traffic.stop()
        return 'Replay stopped'

    @toolset_registry.tool_call
    @staticmethod
    def capture_viewport() -> str:
        """Schedule a screenshot in this project's private Saved directory."""
        output=Path(unreal.Paths.project_saved_dir()).resolve()/'Screenshots'/'beirut-prototype.png'
        output.parent.mkdir(parents=True,exist_ok=True)
        unreal.AutomationLibrary.take_high_res_screenshot(1280,720,str(output))
        return str(output)

    @toolset_registry.tool_call
    @staticmethod
    def point_cloud_status() -> str:
        """Inspect loaded captures and visibility without loading source files."""
        import point_cloud_layer
        return json.dumps(point_cloud_layer.status())

    @toolset_registry.tool_call
    @staticmethod
    def set_point_cloud_visible(visible: bool) -> str:
        """Show/hide the selected original-RGB capture, importing it only on first use."""
        import point_cloud_layer
        return json.dumps(point_cloud_layer.set_visible(visible))

    @toolset_registry.tool_call
    @staticmethod
    def focus_point_cloud() -> str:
        """Frame the selected capture; the capture can lie outside the city crop."""
        import point_cloud_layer
        point_cloud_layer.focus()
        return 'Camera at selected capture; source vertical datum unverified'
