"""Read native source signatures, bounds, IDs, transforms and collision."""
import hashlib
import json
import os
from pathlib import Path
import re
import unreal
import city_world
import explore_city

ROOT=Path('C:/private/dynacity-data/engine/facade-reference-20261030')
data=json.loads((ROOT/'reference-layout.json').read_text());report=json.loads((ROOT/'native-install.json').read_text())
descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs if 'Reference ' in str(d.label) or str(d.label)=='buildings_remeil_material_9'])
errors=[];expected={item['id']:item for item in data['placements']};seen=set();counts={};max_position=0;max_rotation=0;max_scale=0
source=unreal.load_object(None,report['source_actor'])
if source is None:raise RuntimeError('Missing original source actor')
original=source.get_component_by_class(unreal.StaticMeshComponent)
if original.static_mesh.get_path_name()!=report['original_mesh']:errors.append('Original mesh changed')
if str(original.get_collision_enabled())!=report['original_collision']:errors.append('Original collision changed')
if original.is_visible():errors.append('Original visual batch is visible beneath replacement')
before=json.loads((ROOT/'native-before.json').read_text())['source_actors'][0]
values=lambda text:[float(v) for v in re.findall(r'[xyzw]: (-?\d+\.\d+)',text)]
if values(str(source.get_actor_transform()))!=values(before['transform']):errors.append('Original transform changed')
for asset in [data['source']['source_asset'],data['source']['source_index']]:
    key='source_sha256' if asset.endswith('.glb') else 'source_index_sha256'
    if hashlib.sha256(Path(asset).read_bytes()).hexdigest()!=data[key]:errors.append('Source file bytes changed')
for path in report['actors']:
    actor=unreal.load_object(None,path)
    if actor is None:errors.append('Missing owned actor');continue
    comp=actor.get_component_by_class(unreal.StaticMeshComponent)
    if comp.get_collision_enabled()!=unreal.CollisionEnabled.NO_COLLISION or comp.get_editor_property('use_default_collision') or actor.get_actor_enable_collision():
        errors.append('Owned visual collision active')
    if not comp.is_visible() or comp.get_editor_property('hidden_in_game'):errors.append('Reference visual hidden')
    if 'visual source batch' in actor.get_actor_label():
        record=next(r for r in data['prepared']['assets'] if r['id']=='source_visual_batch')
        if comp.static_mesh.get_num_triangles(0)!=record['triangles']:errors.append('Source copy triangles differ')
        box=comp.static_mesh.get_bounding_box();low,high=record['bounds_m']
        target_low=[low[0]*100,-high[1]*100,low[2]*100];target_high=[high[0]*100,-low[1]*100,high[2]*100]
        actual=[box.min.x,box.min.y,box.min.z,box.max.x,box.max.y,box.max.z]
        if max(abs(a-b) for a,b in zip(actual,target_low+target_high))>1:errors.append('Source copy absolute bounds differ')
        slots={str(slot.material_slot_name):comp.get_material(i).get_path_name() for i,slot in enumerate(comp.static_mesh.get_editor_property('static_materials'))}
        if not any('ReferenceFacade' in k and 'M_ReferenceBody' in v for k,v in slots.items()):errors.append('Target material missing')
        if not any('SourceExisting' in k and v==original.get_material(0).get_path_name() for k,v in slots.items()):errors.append('Other buildings material changed')
        counts['source_visual_triangles']=record['triangles'];continue
    kind='glass' if 'glass' in actor.get_actor_label() else 'cornice'
    ids=[str(v) for v in actor.get_editor_property('source_ids')]
    if comp.get_instance_count()!=len(ids):errors.append('Instance count differs')
    counts[kind]=len(ids)
    for i,identity in enumerate(ids):
        if identity in seen or identity not in expected:errors.append('Duplicate or unknown reference ID');continue
        seen.add(identity);item=expected[identity];t=comp.get_instance_transform(i,world_space=True)
        max_position=max(max_position,(t.translation-explore_city._world(item['position_m'])).length())
        r=t.rotation.rotator();max_rotation=max(max_rotation,abs((r.yaw+item['yaw_deg']+180)%360-180),abs(r.pitch),abs(r.roll))
        max_scale=max(max_scale,max(abs(a-b) for a,b in zip([t.scale3d.x,t.scale3d.y,t.scale3d.z],item['scale'])))
if seen!=set(expected):errors.append('Expected reference instance IDs missing')
if max_position>.1 or max_rotation>.01 or max_scale>.0001:errors.append('Reference transform differs')
result={'status':'pass' if not errors else 'failed','editor_pid':os.getpid(),'errors':errors,'counts':counts,
        'position_error_max_cm':max_position,'rotation_error_max_deg':max_rotation,'scale_error_max':max_scale,
        'source_geometry_and_collision_unchanged':not any('Original' in e or 'Source file' in e for e in errors),
        'target_source_triangles':data['prepared']['assets'][0]['material_triangles']['ReferenceFacade'],
        'other_source_triangles':data['prepared']['assets'][0]['material_triangles']['SourceExisting']}
(ROOT/'native-verification.json').write_text(json.dumps(result,indent=2))
if errors:raise RuntimeError(str(errors))
