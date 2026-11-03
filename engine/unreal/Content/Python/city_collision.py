"""Static source-mesh collision for walking and future simulated vehicles.

Complex-as-simple uses the actual surface rather than a bounding box sealing
streets and courtyards. These meshes remain static, never simulated rigid bodies.
"""
from pathlib import Path
import json
import unreal


def configure(actors=None,*,exact_fallback=True,recook=False):
    actors=list(actors) if actors is not None else list(unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors())
    changed=[];failed=[];configured=[];seen=set();rebuilt=[];recooked=[]
    editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    for actor in actors:
        label=actor.get_actor_label()
        if not (label.startswith(('road_','roads_','buildings_','beirut001_','source-ground'))):continue
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        if not component or not component.static_mesh:continue
        mesh=component.static_mesh
        try:
            if mesh.get_path_name() not in seen:
                rebuild_physics=recook
                body=mesh.get_editor_property('body_setup')
                if not body:raise RuntimeError('No imported physics body')
                desired=unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE
                if body.get_editor_property('collision_trace_flag')!=desired:
                    mesh.modify();body.modify()
                    body.set_editor_property('collision_trace_flag',desired)
                    changed.append(mesh.get_path_name())
                if exact_fallback:
                    settings=editor.get_nanite_settings(mesh)
                    if settings.get_editor_property('enabled') and (
                        settings.get_editor_property('fallback_target')!=unreal.NaniteFallbackTarget.PERCENT_TRIANGLES or
                        settings.get_editor_property('fallback_percent_triangles')!=1.0 or
                        settings.get_editor_property('fallback_relative_error')!=0.0):
                        # Auto fallback uses a size-dependent heuristic: thin road
                        # strips can disappear, and terrain can shift by metres.
                        # Explicit percentage is required; zero error with Auto
                        # still invokes Unreal's automatic simplification target.
                        settings.set_editor_property('fallback_target',unreal.NaniteFallbackTarget.PERCENT_TRIANGLES)
                        settings.set_editor_property('fallback_percent_triangles',1.0)
                        settings.set_editor_property('fallback_relative_error',0.0)
                        editor.set_nanite_settings(mesh,settings,True)
                        rebuilt.append(mesh.get_path_name())
                        rebuild_physics=True
                if rebuild_physics:
                    # RemoveSimpleCollision invalidates cached triangle physics
                    # too. Source meshes use complex-as-simple, so no generated
                    # convex/box proxies are needed. Rebuild after fallback edits.
                    if not editor.remove_collisions(mesh):raise RuntimeError('Collision recook failed')
                    recooked.append(mesh.get_path_name())
                seen.add(mesh.get_path_name())
            component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            component.set_collision_profile_name('BlockAll')
            component.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
            actor.set_actor_enable_collision(True)
            configured.append(label)
        except Exception as exc:failed.append({'actor':label,'error':str(exc)})
    result={'configured':len(configured),'changed_meshes':len(changed),'exact_fallback_rebuilt_meshes':len(rebuilt),
            'physics_recooked_meshes':len(recooked),'failed':failed,
            'requires_saved_map_reload':bool(changed or rebuilt or recooked),
            'mode':'complex-as-simple, static surfaces only'}
    path=Path(unreal.Paths.project_saved_dir())/'DynaCity'/'collision-configuration.json'
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,indent=2))
    if failed:raise RuntimeError('Some source surfaces lack collision: '+str(failed[:3]))
    return result


def configure_and_save(*,recook=False):
    """Persist collision edits and replace any cached world physics with saved data."""
    import preview_traffic,explore_city,city_world,point_cloud_layer
    levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if levels.is_in_play_in_editor():raise RuntimeError('End Play before rebuilding collision')
    if preview_traffic._callback is not None or preview_traffic._paused_at is not None:
        raise RuntimeError('Stop the editor traffic replay before rebuilding collision')
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    map_path=world.get_path_name().split('.')[0]
    camera,rotation=unreal.EditorLevelLibrary.get_level_viewport_camera_info()
    cloud_visible=point_cloud_layer.visible()
    explore_city.stop_following()
    result=configure(recook=recook)
    if not levels.save_current_level():raise RuntimeError('Collision map save failed')
    if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(False,True):
        raise RuntimeError('Collision asset save failed')
    if result['requires_saved_map_reload']:
        if not levels.load_level(map_path):raise RuntimeError('Could not reload saved collision world')
        if map_path==city_world.MAP:city_world.load_editor_geometry()
        if cloud_visible:point_cloud_layer.set_visible(True)
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(camera,rotation)
        result['saved_map_reloaded']=True
    return result


def trace_at_source(point):
    """Use a simple visibility trace, exercising complex-as-simple collision."""
    import explore_city
    position=explore_city._world(point)
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    result=unreal.SystemLibrary.line_trace_single(world,position+unreal.Vector(0,0,10000),
        position-unreal.Vector(0,0,10000),unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,False,[],
        unreal.DrawDebugTrace.NONE,True)
    return result
