"""Verify saved pilot actors across separate editor ticks of unload/reload."""
import hashlib
import json
from pathlib import Path
import time
import unreal

BASE = Path('C:/private/dynacity-data/engine/street-twin-20261029')
OUT = BASE/'efasim-finish-persistence.json'
_check = None


def snapshot(refs):
    result = {}
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        path = actor.get_path_name()
        if path not in refs:
            continue
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        p,r,s = actor.get_actor_location(), actor.get_actor_rotation(), actor.get_actor_scale3d()
        row = {'transform':[p.x,p.y,p.z,r.pitch,r.yaw,r.roll,s.x,s.y,s.z],
               'mesh':component.static_mesh.get_path_name(),
               'materials':[m.get_path_name() if m else None for m in component.get_materials()],
               'collision':str(component.get_collision_enabled()),
               'profile':str(component.get_collision_profile_name()),
               'visible':component.is_visible(), 'actor_collision':actor.get_actor_enable_collision()}
        if isinstance(component,unreal.InstancedStaticMeshComponent):
            values = []
            for i in range(component.get_instance_count()):
                t=component.get_instance_transform(i,world_space=True)
                values.append([t.translation.x,t.translation.y,t.translation.z,
                    t.rotation.x,t.rotation.y,t.rotation.z,t.rotation.w,
                    t.scale3d.x,t.scale3d.y,t.scale3d.z])
            row['instance_count']=len(values)
            row['instance_hash']=hashlib.sha256(json.dumps(values).encode()).hexdigest()
            row['source_ids']=[str(v) for v in actor.get_editor_property('source_ids')]
        result[path]=row
    return result


def tick(delta):
    global _check
    if time.monotonic()-_check['changed'] < 1:
        return
    if _check['phase']=='unloaded':
        remaining = snapshot(_check['refs'])
        _check['remained_loaded'] = list(remaining)
        unreal.WorldPartitionBlueprintLibrary.load_actors(_check['guids'])
        _check.update(phase='reloaded',changed=time.monotonic())
        return
    after=snapshot(_check['refs'])
    report={'status':'pass' if after==_check['before'] and not _check['remained_loaded'] else 'partial',
            'requested':len(_check['refs']),'after_reload':len(after),
            'fully_unloaded':len(_check['refs'])-len(_check['remained_loaded']),
            'remained_loaded':_check['remained_loaded'],
            'state_match':after==_check['before'],
            'actors':after}
    OUT.write_text(json.dumps(report,indent=2))
    unreal.unregister_slate_post_tick_callback(_check['callback'])
    _check=None


def start():
    global _check
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('Finish the owned Play check first')
    med=json.loads((BASE/'marmikhael/finish/pilot-finish-install.json').read_text())
    charles=json.loads((BASE/'charles-malek/finish/native-finish-install.json').read_text())
    facade=json.loads(Path('C:/private/dynacity-data/engine/facade-modules-20261029/facade-install.json').read_text())
    refs={r['actor']['refPath'] for r in med['changes']}|{med['paint_actor']['refPath']}
    refs.update(r['actor'] for r in charles['source_materials']+charles['paint'])
    refs.update(r['actor'] for r in facade['actors'])
    actors=[a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors() if a.get_path_name() in refs]
    guids=[d.guid for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs_for_actors(actors)]
    before=snapshot(refs)
    if len(before)!=len(refs) or len(guids)!=len(refs):
        raise RuntimeError('Expected all saved pilot actors loaded before verification')
    actors=None
    _check={'refs':refs,'guids':guids,'before':before,'phase':'unloaded','changed':time.monotonic()}
    unreal.WorldPartitionBlueprintLibrary.unload_actors(guids)
    _check['callback']=unreal.register_slate_post_tick_callback(tick)


start()
