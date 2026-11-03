"""Bounded exposure for inspecting source-RGB captures without changing the city."""
import unreal

TAG='DYNACITY_CAPTURE_INSPECTION'


def _component(subsystem,parent,cls):
    handle,reason=subsystem.add_new_subobject(unreal.AddNewSubobjectParams(parent_handle=parent,new_class=cls))
    lib=unreal.SubobjectDataBlueprintFunctionLibrary
    obj=lib.get_object(lib.get_data(handle))
    if obj is None:raise RuntimeError(str(reason))
    return handle,obj


def configure(cloud_actor):
    capture=next(str(t) for t in cloud_actor.tags if str(t).startswith('capture:'))
    actors=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor=next((a for a in actors.get_all_level_actors() if TAG in [str(t) for t in a.tags] and capture in [str(t) for t in a.tags]),None)
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if actor is None and world.get_world_settings().get_editor_property('world_partition') is not None:
        label='Capture inspection exposure - '+capture.split(':',1)[1]
        descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
        matches=[d.guid for d in descs if str(d.label)==label]
        if len(matches)>1:raise RuntimeError('Duplicate saved capture exposure actors')
        if matches:
            unreal.WorldPartitionBlueprintLibrary.load_actors(matches)
            actor=next((a for a in actors.get_all_level_actors() if a.get_actor_label()==label),None)
            if actor is None:raise RuntimeError('Saved capture exposure actor did not load')
    if actor is None:
        actor=actors.spawn_actor_from_class(unreal.Actor,unreal.Vector())
        actor.tags=[TAG,capture]
        actor.set_actor_label('Capture inspection exposure - '+capture.split(':',1)[1])
        actor.set_folder_path('DynaCity/Point clouds')
        subsystem=unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
        root=subsystem.k2_gather_subobject_data_for_instance(actor)[0]
        box_handle,box=_component(subsystem,root,unreal.BoxComponent)
        _,post=_component(subsystem,box_handle,unreal.PostProcessComponent)
        box.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        box.set_visibility(False,False)
    else:
        box=actor.get_component_by_class(unreal.BoxComponent)
        post=actor.get_component_by_class(unreal.PostProcessComponent)
    centre,extent=cloud_actor.get_actor_bounds(False)
    actor.set_actor_location(centre,False,False)
    box.set_box_extent(extent+unreal.Vector(50000,50000,100000),False)
    post.set_editor_property('unbound',False)
    post.set_editor_property('priority',200)
    post.set_editor_property('blend_radius',0)
    settings=post.get_editor_property('settings')
    for name,value in [('override_auto_exposure_method',True),('auto_exposure_method',unreal.AutoExposureMethod.AEM_MANUAL),
                       ('override_auto_exposure_apply_physical_camera_exposure',True),('auto_exposure_apply_physical_camera_exposure',False),
                       ('override_auto_exposure_bias',True),('auto_exposure_bias',0)]:
        settings.set_editor_property(name,value)
    post.set_editor_property('settings',settings)
    return post


def set_enabled(cloud_actor,enabled):
    configure(cloud_actor).set_editor_property('enabled',enabled)
