"""Lazy native point-cloud layer and editor menu for the Beirut prototype.

Private source paths and a common UTM origin live in prototype_config.json.
Only display offsets change; original LAS/RGB are never rewritten. The shared
vertical datum is unverified. Editor UI is separate from a future runtime HUD.
"""
import json
from pathlib import Path
import unreal

_entries=[]
_owner='DynaCityPointClouds'
_tag='DYNACITY_POINT_CLOUD'
_selected=0


def _config():
    path=Path(__file__).resolve().parents[2]/'prototype_config.json'
    return json.loads(path.read_text(encoding='utf-8'))


def _sources():return _config().get('point_clouds',[])


def _actors():
    return [a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
            if _tag in [str(t) for t in a.tags]]


def visible():
    return any(a.get_component_by_class(unreal.LidarPointCloudComponent).is_visible() for a in _actors())


def _load(index,reimport=False):
    sources=_sources()
    if not 0<=index<len(sources):raise RuntimeError('No configured point-cloud capture')
    source=sources[index]
    name=source['id'];asset_path='/Game/Beirut/PointClouds/'+name
    actor=next((a for a in _actors() if 'capture:'+name in [str(t) for t in a.tags]),None)
    # A saved World Partition cloud may be unloaded. Load its descriptor before
    # deciding it is absent, otherwise toggling after reopen duplicates actors.
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if actor is None and world.get_world_settings().get_editor_property('world_partition') is not None:
        descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
        matches=[d.guid for d in descs if str(d.label).startswith('Point cloud - '+name+' (')]
        if len(matches)>1:raise RuntimeError('Duplicate saved cloud actors: '+name)
        if matches:
            unreal.WorldPartitionBlueprintLibrary.load_actors(matches)
            actor=next((a for a in _actors() if 'capture:'+name in [str(t) for t in a.tags]),None)
            if actor is None:raise RuntimeError('Saved cloud actor did not load: '+name)
    cloud=actor.get_point_cloud() if actor else (unreal.load_asset(asset_path) if unreal.EditorAssetLibrary.does_asset_exist(asset_path) else None)
    if cloud is None or reimport:
        path=Path(source['path'])
        if not path.is_file():raise FileNotFoundError(path)
        # Avoid hydrating multi-GB OneDrive placeholders just by toggling a layer.
        attributes=getattr(path.stat(),'st_file_attributes',0)
        if attributes & (0x1000|0x400000):raise RuntimeError('Capture is online-only. Download it locally before selecting it: '+name)
        task=unreal.AssetImportTask()
        task.filename=str(path);task.destination_path='/Game/Beirut/PointClouds'
        task.destination_name=name;task.automated=True;task.save=True
        task.replace_existing=reimport
        settings=unreal.get_default_object(unreal.load_class(None,'/Script/LidarPointCloudRuntime.LidarPointCloudSettings'))
        old_async=settings.get_editor_property('bUseAsyncImport')
        old_duplicates=settings.get_editor_property('DuplicateHandling')
        try:
            # Finish before positioning/saving. Otherwise async completion resets
            # the offset and can leave an incomplete asset saved on disk.
            settings.set_editor_property('bUseAsyncImport',False)
            settings.set_editor_property('DuplicateHandling',unreal.LidarPointCloudDuplicateHandling.IGNORE)
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        finally:
            settings.set_editor_property('bUseAsyncImport',old_async)
            settings.set_editor_property('DuplicateHandling',old_duplicates)
        cloud=unreal.load_asset(asset_path)
        if cloud is None:raise RuntimeError('Point-cloud import did not produce '+asset_path)
    origin=_config()['point_cloud_origin_utm_m']
    absolute=cloud.get_editor_property('original_coordinates')
    # Native LAS import already flips Y and converts metres to centimetres.
    world_origin=unreal.Vector(origin[0]*100,-origin[1]*100,origin[2]*100)
    offset=absolute-world_origin
    expected_min=cloud.get_bounds(True).min-world_origin
    if (cloud.get_bounds(False).min-expected_min).length()>.001:
        cloud.set_location_offset(offset)
        unreal.EditorAssetLibrary.save_loaded_asset(cloud)
    if actor:
        actor.set_point_cloud(cloud)
        return actor
    actor=unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(unreal.LidarPointCloudActor,unreal.Vector())
    actor.set_point_cloud(cloud)
    kind=source.get('display_kind','all source points')
    actor.set_actor_label('Point cloud - '+name+' ('+kind+'; source RGB; datum unverified)')
    actor.tags=[_tag,'capture:'+name]
    actor.set_folder_path('DynaCity/Point clouds')
    component=actor.get_component_by_class(unreal.LidarPointCloudComponent)
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    component.set_editor_property('point_size',1.0)
    component.set_visibility(False,True)
    return actor


def set_visible(show):
    import capture_inspection
    if show:
        actor=_load(_selected)
        for item in _actors():
            component=item.get_component_by_class(unreal.LidarPointCloudComponent)
            component.set_visibility(item==actor,True)
            item.set_actor_hidden_in_game(item!=actor)
            capture_inspection.set_enabled(item,item==actor)
    else:
        for item in _actors():
            item.get_component_by_class(unreal.LidarPointCloudComponent).set_visibility(False,True)
            item.set_actor_hidden_in_game(True)
            capture_inspection.set_enabled(item,False)
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_invalidate_viewports()
    return status()


def select(index):
    global _selected
    if not 0<=index<len(_sources()):raise IndexError(index)
    previous=_selected
    _selected=index
    try:
        set_visible(True)
        focus()
    except Exception:
        _selected=previous
        raise


def focus():
    import explore_city
    explore_city.stop_following()
    set_visible(True)
    actor=_load(_selected)
    centre,extent=actor.get_actor_bounds(False)
    distance=max(extent.x,extent.y,1000)*1.15
    camera=centre+unreal.Vector(distance,-distance,distance*.85)
    explore_city._look_at(camera,centre)


def return_to_city():
    import explore_city
    explore_city.stop_following()
    import dynacity_tools
    origin,east,north,up=dynacity_tools._basis()
    camera=origin+east*450-north*550+up*450
    unreal.EditorLevelLibrary.set_level_viewport_camera_info(camera,unreal.MathLibrary.make_rot_from_x(origin+up*15-camera))


def status():
    sources={s['id']:s for s in _sources()}
    def source_info(actor):
        name=next(str(t).split(':',1)[1] for t in actor.tags if str(t).startswith('capture:'))
        source=sources.get(name,{})
        return {'display_kind':source.get('display_kind','all source points'),
                'source_points':source.get('source_point_count',actor.get_point_cloud().get_num_points())}
    return {'visible':visible(),'selected':_sources()[_selected]['id'] if _sources() else None,
            'loaded':[{'name':a.get_actor_label(),'visible':a.get_component_by_class(unreal.LidarPointCloudComponent).is_visible(),
                       'points':a.get_point_cloud().get_num_points(),**source_info(a)} for a in _actors()]}


@unreal.uclass()
class CloudToggle(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self,context):
        try:set_visible(not visible())
        except Exception as error:unreal.log_error(str(error))

    @unreal.ufunction(override=True)
    def get_check_state(self,context):
        return unreal.CheckBoxState.CHECKED if visible() else unreal.CheckBoxState.UNCHECKED


def register_menu():
    menus=unreal.ToolMenus.get();menus.unregister_owner_by_name(_owner)
    menu=menus.extend_menu('LevelEditor.MainMenu.Tools').add_sub_menu(_owner,'DynaCity','DynaCityPointClouds','Point clouds','Captured points; original RGB, vertical datum unverified')
    toggle=CloudToggle()
    toggle.init_entry(_owner,menu.menu_name,'Layer','Visible','Show point clouds','Toggle the selected capture independently of city geometry')
    data=toggle.data;advanced=data.advanced
    advanced.user_interface_action_type=unreal.UserInterfaceActionType.TOGGLE_BUTTON
    data.advanced=advanced;toggle.data=data
    toggle.register_menu_entry();_entries.append(toggle)
    def command(name,label,code):
        entry=unreal.ToolMenuEntry(name=name,type=unreal.MultiBlockType.MENU_ENTRY)
        entry.set_label(label)
        entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'',code)
        menu.add_menu_entry('Captures',entry)
    command('Focus','Go to selected capture','import point_cloud_layer; point_cloud_layer.focus()')
    command('City','Return to city overview','import point_cloud_layer; point_cloud_layer.return_to_city()')
    for index,source in enumerate(_sources()):
        label='Show '+source['id']+(' (sampled preview)' if source.get('display_kind')=='sampled preview' else '')
        command('Capture'+str(index),label,'import point_cloud_layer; point_cloud_layer.select('+str(index)+')')
    menus.refresh_all_widgets()
