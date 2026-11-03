"""Save only the explicitly installed pilot actor packages, inside Unreal."""
import json
from pathlib import Path

import unreal


def save(report_path):
    path = Path(report_path)
    report = json.loads(path.read_text())
    if report['status'] != 'awaiting_scoped_save':
        raise ValueError('Pilot installation must complete before saving')
    refs = [r['actor'] for r in report['changes']] + [report['paint_actor']]
    actors = [unreal.load_object(None, r['refPath']) for r in refs]
    if any(a is None for a in actors):
        raise RuntimeError('An installed pilot actor is not loaded')
    paint = unreal.load_object(None, report['paint_actor']['refPath'])
    paint.modify()
    paint.set_editor_property('is_spatially_loaded', True)
    packages = list({a.get_package().get_path_name(): a.get_package() for a in actors}.values())
    if not unreal.EditorLoadingAndSavingUtils.save_packages(packages, True):
        raise RuntimeError('Scoped external actor save failed')
    dirty = {p.get_path_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()}
    unsaved = [p.get_path_name() for p in packages if p.get_path_name() in dirty]
    if unsaved:
        raise RuntimeError('Pilot actor packages still dirty: ' + str(unsaved))
    report.update(status='pass', saved=True, saved_actor_packages=[p.get_path_name() for p in packages],
                  unsaved_actor_packages=[], reload_verified=False)
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps({'pilot_finish_saved':True,'actor_packages':len(packages),'report':str(path)}))


def verify_reload(report_path):
    path = Path(report_path)
    report = json.loads(path.read_text())
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    refs = {row['actor']['refPath'] for row in report['changes']} | {report['paint_actor']['refPath']}
    def snapshot():
        result = {}
        for actor in subsystem.get_all_level_actors():
            if actor.get_path_name() not in refs:
                continue
            component = actor.get_component_by_class(unreal.StaticMeshComponent)
            mesh = component.get_editor_property('static_mesh')
            location, rotation, scale = actor.get_actor_location(), actor.get_actor_rotation(), actor.get_actor_scale3d()
            result[actor.get_path_name()] = {'mesh':mesh.get_path_name(),
                'materials':[m.get_path_name() if m else None for m in component.get_materials()],
                'collision':str(component.get_collision_enabled()),'visible':component.is_visible(),
                'transform':[location.x,location.y,location.z,rotation.pitch,rotation.yaw,rotation.roll,scale.x,scale.y,scale.z],
                'tags':sorted(str(t) for t in actor.tags)}
        return result
    before = snapshot()
    selected = [a for a in subsystem.get_all_level_actors() if a.get_path_name() in refs]
    descs = unreal.WorldPartitionBlueprintLibrary.get_actor_descs_for_actors(selected) or []
    guids = [d.guid for d in descs]
    selected = None
    unreal.WorldPartitionBlueprintLibrary.unload_actors(guids)
    unreal.SystemLibrary.collect_garbage()
    remaining = len(snapshot())
    unreal.WorldPartitionBlueprintLibrary.load_actors(guids)
    after = snapshot()
    report.update(reload_requested_actor_count=len(guids), after_reload_actor_count=len(after),
                  remained_loaded_after_unload=remaining,
                  saved_state_match_after_reload=before == after,
                  reload_verified=len(guids)==len(refs) and remaining==0 and before==after)
    path.write_text(json.dumps(report,indent=2))
    print(json.dumps({'pilot_finish_reload':report['reload_verified'],'state_match':before==after,
                      'actor_count':len(after),'remained_loaded':remaining}))


if __name__ == '__main__':
    target='C:/private/dynacity-data/engine/street-twin-20261029/marmikhael/finish/pilot-finish-install.json'
    if json.loads(Path(target).read_text())['status']=='awaiting_scoped_save':
        save(target)
    verify_reload(target)
