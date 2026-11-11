"""Persist the intended visual-only collision policy on the Ac_726 copies."""
import json
from pathlib import Path
import traceback
import unreal

OUT = Path('C:/private/dynacity-data/engine/street-twin-20261029/capture-visual-collision-repair.json')


def run():
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End the owned PIE check first')
    root = Path(unreal.Paths.project_dir()) / 'Saved/DynaCity'
    source = json.loads((root/'captured-building-backup-Ac_726.json').read_text())
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    descriptors = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descriptors if
        str(d.label).startswith(('Captured visual - Ac_726', 'Captured building - Ac_726')) or
        str(d.label) in {r['label'] for r in source['actors']}])
    def originals():
        values = {}
        for row in source['actors']:
            actor = unreal.load_object(None,row['actor'])
            component = actor.get_component_by_class(unreal.StaticMeshComponent)
            values[row['actor']] = {'mesh':component.static_mesh.get_path_name(),
                'collision':str(component.get_collision_enabled()),
                'profile':str(component.get_collision_profile_name())}
        return values
    before = originals()
    selected = [a for a in subsystem.get_all_level_actors() if
        'DYNACITY_CAPTURED_BUILDING' in a.tags and 'capture:Ac_726' in a.tags]
    if len(selected) != 2:
        raise RuntimeError('Expected the two existing visual replacement actors')
    rows = []
    for actor in selected:
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        row = {'actor':actor.get_path_name(),'label':actor.get_actor_label(),
               'before':{'default_collision':component.get_editor_property('use_default_collision'),
                         'enabled':str(component.get_collision_enabled()),
                         'profile':str(component.get_collision_profile_name()),
                         'actor_collision':actor.get_actor_enable_collision()}}
        actor.modify(); component.modify()
        component.set_editor_property('use_default_collision',False)
        component.set_collision_profile_name('NoCollision')
        component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        actor.set_actor_enable_collision(False)
        row['after'] = {'default_collision':component.get_editor_property('use_default_collision'),
                        'enabled':str(component.get_collision_enabled()),
                        'profile':str(component.get_collision_profile_name()),
                        'actor_collision':actor.get_actor_enable_collision()}
        if row['after']['default_collision'] or row['after']['actor_collision']:
            raise RuntimeError('Visual-only collision did not disable')
        rows.append(row)
    if before != originals():
        raise RuntimeError('Original source collision changed')
    if not unreal.EditorLoadingAndSavingUtils.save_packages([a.get_package() for a in selected],True):
        raise RuntimeError('Scoped visual-copy save failed')
    result = {'status':'saved','changes':rows,'source_collision_unchanged':True,'source_collision':before,
              'reason':'Visual-only copies inherited mesh default collision during PIE; source batches remain collision authority.'}
    OUT.write_text(json.dumps(result,indent=2))
    return result


try:
    print(json.dumps(run()))
except Exception:
    OUT.write_text(json.dumps({'status':'failed','error':traceback.format_exc()},indent=2))
    raise
