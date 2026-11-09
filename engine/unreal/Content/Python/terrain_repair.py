"""Import a small source-attributed visual terrain patch without replacing the city."""
import json
from pathlib import Path
import unreal

LABEL='StreetCover_TerrainGapRepair'


def start(report_file):
    import city_world
    city_world._current();city_world.load_editor_geometry()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End driving before importing terrain')
    if any(a.get_actor_label()==LABEL for a in city_world._actors()):
        raise RuntimeError('Terrain patch already exists; inspect before replacing')
    report=json.loads(Path(report_file).read_text())
    manager=unreal.InterchangeManager.get_interchange_manager_scripted()
    if manager.is_interchange_active():raise RuntimeError('Another import is active')
    params=unreal.ImportAssetParameters();params.is_automated=True;params.replace_existing=False
    source=unreal.InterchangeManager.create_source_data(report['asset'])
    manager.scripted_import_scene_async('/Game/Beirut/TerrainGapRepair',source,params)


def finish(report_file):
    import city_world
    manager=unreal.InterchangeManager.get_interchange_manager_scripted()
    if manager.is_interchange_active():return {'importing':True}
    actor=next(a for a in city_world._actors() if a.get_actor_label()==LABEL)
    actor.modify()
    parent=actor.get_attach_parent_actor()
    actor.detach_from_actor(unreal.DetachmentRule.KEEP_WORLD,unreal.DetachmentRule.KEEP_WORLD,unreal.DetachmentRule.KEEP_WORLD)
    actor.tags=list(dict.fromkeys([str(t) for t in actor.tags]+['DYNACITY_STREET_COVER','cover_kind:terrain_gap_repair']))
    if parent and parent.get_class().get_name()=='InterchangeSceneActor':
        unreal.get_editor_subsystem(unreal.EditorActorSubsystem).destroy_actor(parent)
    actor.set_folder_path('DynaCity/Mapped ground/Shared city terrain')
    actor.set_editor_property('is_spatially_loaded',True)
    component=actor.get_component_by_class(unreal.StaticMeshComponent)
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION);component.set_cast_shadow(False)
    city_world.match_ground_finish()
    report=json.loads(Path(report_file).read_text());report['actor']=actor.get_path_name()
    report['material']=component.get_material(0).get_path_name()
    if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True):raise RuntimeError('Terrain patch save failed')
    Path(report_file).write_text(json.dumps(report,indent=2))
    return report
