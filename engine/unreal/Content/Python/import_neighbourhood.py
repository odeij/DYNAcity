"""Run in the Unreal editor: import_neighbourhood.build(r'.../blender/neighbourhood.glb').

This is an editor import, not a packaged runtime glTF loader. The source GLB
contains metres and standard glTF axes; Interchange handles Unreal conversion.
Do not close the editor until its asynchronous import has completed.
"""
from pathlib import Path
import json
import unreal


def build(filename,*,reuse_level=False):
    path=Path(filename).resolve()
    if not path.is_file():raise FileNotFoundError(path)
    levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if unreal.EditorAssetLibrary.does_asset_exist('/Game/Maps/BeirutPrototype') and not reuse_level:
        raise RuntimeError('Prototype map already exists; open it instead of replacing it')
    if not reuse_level:levels.new_level('/Game/Maps/BeirutPrototype')
    actors=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    def get_or_spawn(cls,position):
        found=[actor for actor in actors.get_all_level_actors() if isinstance(actor,cls)]
        return found[0] if found else actors.spawn_actor_from_class(cls,position)
    sun=get_or_spawn(unreal.DirectionalLight,unreal.Vector(0,0,50000))
    sun.set_actor_rotation(unreal.Rotator(-35,-30,0),False)
    sunlight=sun.get_component_by_class(unreal.DirectionalLightComponent)
    sunlight.set_mobility(unreal.ComponentMobility.MOVABLE)
    # Controlled preview lighting; physical daylight needs a matching EV range.
    sunlight.set_intensity(10)
    sunlight.set_editor_property('atmosphere_sun_light',True)
    get_or_spawn(unreal.SkyAtmosphere,unreal.Vector(0,0,0))
    sky=get_or_spawn(unreal.SkyLight,unreal.Vector(0,0,10000))
    skylight=sky.get_component_by_class(unreal.SkyLightComponent)
    skylight.set_mobility(unreal.ComponentMobility.MOVABLE)
    skylight.set_editor_property('real_time_capture',True)
    exposure=get_or_spawn(unreal.PostProcessVolume,unreal.Vector(0,0,0))
    exposure.set_actor_label('Beirut preview exposure')
    exposure.set_editor_property('unbound',True)
    settings=exposure.get_editor_property('settings')
    settings.set_editor_property('override_auto_exposure_method',True)
    settings.set_editor_property('auto_exposure_method',unreal.AutoExposureMethod.AEM_HISTOGRAM)
    settings.set_editor_property('override_auto_exposure_apply_physical_camera_exposure',True)
    settings.set_editor_property('auto_exposure_apply_physical_camera_exposure',False)
    settings.set_editor_property('override_auto_exposure_bias',True)
    settings.set_editor_property('auto_exposure_bias',0)
    exposure.set_editor_property('settings',settings)
    get_or_spawn(unreal.PlayerStart,unreal.Vector(0,0,18000))
    parameters=unreal.ImportAssetParameters()
    parameters.is_automated=True
    # Only the private generated import directory is targeted. Reuse source IDs
    # without blocking an automated rebuild on one override dialog per asset.
    parameters.replace_existing=True
    manager=unreal.InterchangeManager.get_interchange_manager_scripted()
    source=unreal.InterchangeManager.create_source_data(str(path))
    # UE 5.8 can report a false return while its asynchronous import is active.
    # Validate the manager/created scene after completion rather than retrying
    # here and starting a duplicate import.
    manager.scripted_import_scene_async('/Game/Beirut',source,parameters)
    unreal.log('Beirut import started. Wait for the import queue, then save all assets and the map.')


def build_from_config(*,reuse_level=False):
    config=Path(__file__).resolve().parents[2]/'prototype_config.json'
    build(json.loads(config.read_text(encoding='utf-8'))['scene_glb'],reuse_level=reuse_level)

