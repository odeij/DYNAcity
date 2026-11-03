"""Update only the owned reference test after a reviewed asset refinement."""
import importlib.util
import hashlib
import json
from pathlib import Path
import traceback
import unreal
import explore_city

ROOT=Path('C:/private/dynacity-data/engine/facade-reference-20261030')
try:
    spec=importlib.util.spec_from_file_location('reference_installer',Path('C:/Users/PC/DYNAcity/engine/unreal/Content/Python/install_reference_facade.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    data=json.loads((ROOT/'reference-layout.json').read_text());report=json.loads((ROOT/'native-install.json').read_text())
    assets={}
    for record in data['prepared']['assets']:
        if hashlib.sha256(Path(record['fbx']).read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError('Reference export bytes changed; regenerate the layout first')
        name=record['id']+'_'+record['sha256'][:10];path=module.ROOT+'/Meshes/'+name
        if not unreal.EditorAssetLibrary.does_asset_exist(path):
            task=unreal.AssetImportTask();task.filename=record['fbx'];task.destination_path=module.ROOT+'/Meshes';task.destination_name=name
            task.automated=True;task.save=True;options=unreal.FbxImportUI();options.import_mesh=True;options.import_materials=False
            options.import_textures=False;options.import_as_skeletal=False;options.static_mesh_import_data.combine_meshes=True
            options.static_mesh_import_data.auto_generate_collision=False;options.static_mesh_import_data.generate_lightmap_u_vs=False
            task.options=options;unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        mesh=unreal.load_asset(path)
        if mesh is None or mesh.get_num_triangles(0)!=record['triangles']:raise ValueError('Refined mesh triangle mismatch')
        assets[record['id']]=mesh
    materials={kind:module.material(name,kind,data) for name,kind in
               [('M_ReferenceBody','body'),('M_ReferenceBand','brown'),('M_ReferenceGlass','glass')]}
    actors=[unreal.load_object(None,path) for path in report['actors']]
    source=unreal.load_object(None,report['source_actor']);original=source.get_component_by_class(unreal.StaticMeshComponent)
    if any(actor is None for actor in actors):raise RuntimeError('Missing owned reference actor')
    for actor in actors:
        actor.modify();comp=actor.get_component_by_class(unreal.StaticMeshComponent)
        comp.modify();comp.set_editor_property('use_default_collision',False)
        comp.set_collision_profile_name('NoCollision');comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        actor.set_actor_enable_collision(False)
        if 'visual source batch' in actor.get_actor_label():
            comp.set_static_mesh(assets['source_visual_batch'])
            for i,slot in enumerate(assets['source_visual_batch'].get_editor_property('static_materials')):
                comp.set_material(i,materials['body'] if 'ReferenceFacade' in str(slot.material_slot_name) else original.get_material(0))
            continue
        kind='glass' if 'glass' in actor.get_actor_label() else 'cornice'
        actor.set_street_mesh(assets[kind])
        for i in range(comp.get_num_materials()):comp.set_material(i,materials['glass' if kind=='glass' else 'brown'])
        expected={r['id']:r for r in data['placements'] if r['module']==kind}
        ids=[str(v) for v in actor.get_editor_property('source_ids')]
        if set(ids)!=set(expected):raise ValueError('Reference identities changed')
        for i,identity in enumerate(ids):
            item=expected[identity]
            transform=unreal.Transform(location=explore_city._world(item['position_m']),
                rotation=unreal.Rotator(pitch=0,yaw=-item['yaw_deg'],roll=0),scale=unreal.Vector(*item['scale']))
            if not comp.update_instance_transform(i,transform,world_space=True,mark_render_state_dirty=i==len(ids)-1,teleport=True):
                raise RuntimeError('Could not update reference transform')
    if not unreal.EditorLoadingAndSavingUtils.save_packages([a.get_outermost() for a in actors],True):raise RuntimeError('Refinement save failed')
    report.update(assets={k:v.get_path_name() for k,v in assets.items()},refinement='flat authored ledges; plaster albedo/roughness; surface glass',
                  material_triangle_counts=data['prepared']['assets'][0]['material_triangles'])
    (ROOT/'native-install.json').write_text(json.dumps(report,indent=2))
    (ROOT/'refinement-result.json').write_text(json.dumps({'status':'pass','actors':len(actors)},indent=2))
except Exception:
    (ROOT/'refinement-result.json').write_text(json.dumps({'status':'failed','error':traceback.format_exc()},indent=2))
    raise
