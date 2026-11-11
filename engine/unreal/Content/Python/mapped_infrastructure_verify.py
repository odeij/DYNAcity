"""Verify source identities in saved, reloaded World Partition instance actors."""
import json
from pathlib import Path
import unreal

def verify(kind):
    import city_world
    prefix={'camera':'Street_MappedCamera_','power-tower':'Street_MappedPowerTower_', 'recycling':'Street_MappedRecycling_', 'fixed-camera':'Street_MappedFixedCamera_'}[kind]
    root=city_world._root()/'Saved/DynaCity'
    report=json.loads((root/('mapped-'+kind+'-import.json')).read_text(encoding='utf8'))
    expected=set(report['source_ids'])
    descriptors=[d for d in unreal.WorldPartitionBlueprintLibrary.get_actor_descs() if str(d.label).startswith(prefix)]
    if len(descriptors)!=len(report['actors']):raise RuntimeError('Saved descriptor count differs')
    guids=[d.guid for d in descriptors]
    unreal.WorldPartitionBlueprintLibrary.unload_actors(guids)
    unreal.WorldPartitionBlueprintLibrary.load_actors(guids)
    actors=[a for a in city_world._actors() if a.get_actor_label().startswith(prefix)]
    ids=[];groups=[]
    for actor in actors:
        source=list(actor.get_editor_property('source_ids'));count=actor.get_street_instance_count()
        if count!=len(source):raise RuntimeError('Source ID and instance counts differ')
        ids.extend(source);groups.append({'actor':actor.get_path_name(),'label':actor.get_actor_label(),'source_ids':source,'count':count})
    if len(ids)!=len(expected) or set(ids)!=expected:raise RuntimeError('Reloaded identities differ')
    result={'groups':groups,'total':len(ids),'reloaded_external_actors':True}
    (root/('mapped-'+kind+'-verification.json')).write_text(json.dumps(result,indent=2),encoding='utf8')
    return result
