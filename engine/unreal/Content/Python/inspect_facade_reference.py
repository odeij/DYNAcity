"""Read-only source/material check for the LGB historical facade test."""
import json
from pathlib import Path
import unreal
import city_world

out=Path('C:/private/dynacity-data/engine/facade-reference-20261030')
out.mkdir(parents=True,exist_ok=True)
city_world._current()
label='buildings_remeil_material_9'
descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs
    if str(d.label)==label or str(d.label).startswith('FacadePilot_')])
actors=[]
for actor in city_world._actors():
    if actor.get_actor_label()!=label:continue
    component=actor.get_component_by_class(unreal.StaticMeshComponent)
    actors.append({'actor':actor.get_path_name(),'label':actor.get_actor_label(),
        'mesh':component.static_mesh.get_path_name(),'visible':component.is_visible(),
        'collision':str(component.get_collision_enabled()),
        'materials':[component.get_material(i).get_path_name() if component.get_material(i) else None
                     for i in range(component.get_num_materials())],
        'transform':str(actor.get_actor_transform())})
result={'source_actors':actors,'dirty_content':[p.get_path_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()],
    'dirty_maps':[p.get_path_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages()],
    'source_actor_count':len(actors)}
(out/'native-before.json').write_text(json.dumps(result,indent=2))
