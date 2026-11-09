"""Load the existing saved reference street dressing and verify native counts."""
import json
from pathlib import Path
import traceback
import unreal

OUT = Path('C:/private/dynacity-data/engine/street-twin-20261029/street-context-verification.json')
try:
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before loading reference context')
    if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
        raise RuntimeError('Wait for active asset import')
    descriptors = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    selected = [d for d in descriptors if str(d.label).startswith(('Street detail | ', 'Reference preview -', 'Reference lamp illumination'))]
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in selected])
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    rows = []
    for actor in actors:
        if not actor.get_actor_label().startswith(('Street detail | ', 'Reference preview -')):
            continue
        component = actor.get_component_by_class(unreal.InstancedStaticMeshComponent)
        if component is None:
            continue
        rows.append({'actor': actor.get_path_name(), 'label': actor.get_actor_label(),
                     'instances': component.get_instance_count(), 'visible': component.is_visible(),
                     'hidden': actor.is_hidden_ed(),
                     'mesh': component.static_mesh.get_path_name(),
                     'materials': [m.get_path_name() if m else None for m in component.get_materials()]})
    import street_detail
    verification = street_detail.verify_reload('C:/private/dynacity-data/engine/street-detail/pilot/manifest.json')
    OUT.write_text(json.dumps({'loaded_descriptors': len(selected), 'groups': rows,
                              'detail_verification': verification}, indent=2))
except Exception:
    OUT.write_text(json.dumps({'error': traceback.format_exc()}, indent=2))
    raise
