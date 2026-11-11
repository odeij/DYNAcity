"""Scoped reference-facade visual test with original collision kept intact."""
import hashlib
import json
from pathlib import Path
import unreal
import city_world
import explore_city

DATA=Path('C:/private/dynacity-data/engine/facade-reference-20261030')
ROOT='/Game/DynaCity/ReferenceFacade'
TAG='DYNACITY_REFERENCE_FACADE_20261030'


def material(name,kind,data):
    path=ROOT+'/Materials/'+name
    lib=unreal.MaterialEditingLibrary
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        mat=unreal.load_asset(path);lib.delete_all_material_expressions(mat)
    else:mat=unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,ROOT+'/Materials',unreal.Material,unreal.MaterialFactoryNew())
    mat.set_editor_property('used_with_instanced_static_meshes',True)
    if kind=='glass':
        mat.set_editor_property('blend_mode',unreal.BlendMode.BLEND_TRANSLUCENT)
        mat.set_editor_property('two_sided',True)
        mat.set_editor_property('translucency_lighting_mode',unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
    custom=lib.create_material_expression(mat,unreal.MaterialExpressionCustom,0,0)
    custom.set_editor_property('output_type',unreal.CustomMaterialOutputType.CMOT_FLOAT4)
    inputs=[]
    for name in ['Position','Normal','Albedo','Rough']:
        pin=unreal.CustomInput();pin.set_editor_property('input_name',name);inputs.append(pin)
    custom.set_editor_property('inputs',inputs)
    bottom=data['source']['record']['local_bounds_m'][0][2]*100
    step=data['floor_height_m']*100
    code=f'''float3 n=normalize(Normal);float wall=1-step(.55,abs(n.z));
float v=frac((Position.z-{bottom})/{step});
float along=dot(Position.xy,normalize(float2(-n.y,n.x)+float2(.00001,0)));
float u=frac(along/320.0);float pane=step(.12,u)*(1-step(.88,u))*step(.23,v)*(1-step(.90,v))*wall;
float band=(1-step(.18,v))*wall;
float3 weights=pow(abs(n),8);weights/=max(weights.x+weights.y+weights.z,.0001);
float3 p=Position/180.0;
float3 tex=Texture2DSample(Albedo,AlbedoSampler,p.yz).rgb*weights.x+Texture2DSample(Albedo,AlbedoSampler,p.xz).rgb*weights.y+Texture2DSample(Albedo,AlbedoSampler,p.xy).rgb*weights.z;
float rough=Texture2DSample(Rough,RoughSampler,p.yz).r*weights.x+Texture2DSample(Rough,RoughSampler,p.xz).r*weights.y+Texture2DSample(Rough,RoughSampler,p.xy).r*weights.z;
float3 c=lerp(float3(.55,.51,.42),float3(.14,.055,.035),band)*lerp(float3(.8,.8,.8),float3(1,1,1),tex);
return float4(lerp(c,float3(.055,.085,.105),pane),lerp(clamp(rough,.55,.95),.2,pane));'''
    if kind=='brown':code='return float4(.14,.055,.035,.72);'
    if kind=='glass':code='return float4(.12,.22,.25,.15);'
    custom.set_editor_property('code',code)
    for cls,pin in [(unreal.MaterialExpressionWorldPosition,'Position'),(unreal.MaterialExpressionVertexNormalWS,'Normal')]:
        node=lib.create_material_expression(mat,cls,-400,0);lib.connect_material_expressions(node,'',custom,pin)
    import style_city
    folder=Path('C:/private/dynacity-data/assets-inbox/ambientCG/Plaster001')
    for filename,pin,linear in [('Plaster001_1K-JPG_Color.jpg','Albedo',False),('Plaster001_1K-JPG_Roughness.jpg','Rough',True)]:
        texture=style_city._texture(folder/filename,linear)
        node=lib.create_material_expression(mat,unreal.MaterialExpressionTextureObject,-500,200)
        node.set_editor_property('texture',texture);lib.connect_material_expressions(node,'',custom,pin)
    for channels,prop in [((True,True,True,False),unreal.MaterialProperty.MP_BASE_COLOR),((False,False,False,True),unreal.MaterialProperty.MP_ROUGHNESS)]:
        mask=lib.create_material_expression(mat,unreal.MaterialExpressionComponentMask,300,0)
        for channel,value in zip(('r','g','b','a'),channels):mask.set_editor_property(channel,value)
        lib.connect_material_expressions(custom,'',mask,'');lib.connect_material_property(mask,'',prop)
    def scalar(value,prop):
        node=lib.create_material_expression(mat,unreal.MaterialExpressionConstant,-200,300)
        node.set_editor_property('r',value);lib.connect_material_property(node,'',prop)
    if kind=='glass':scalar(.2,unreal.MaterialProperty.MP_OPACITY)
    lib.recompile_material(mat);unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def apply():
    city_world._current();data=json.loads((DATA/'reference-layout.json').read_text())
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():raise RuntimeError('End Play first')
    for file,key in [('source_asset','source_sha256'),('source_index','source_index_sha256')]:
        if hashlib.sha256(Path(data['source'][file]).read_bytes()).hexdigest()!=data[key]:raise ValueError('Changed source bytes')
    before=json.loads((DATA/'native-before.json').read_text())
    if len(before['source_actors'])!=1:raise ValueError('Ambiguous source actor')
    source=unreal.load_object(None,before['source_actors'][0]['actor'])
    if source is None:raise RuntimeError('Original source actor unavailable')
    source_component=source.get_component_by_class(unreal.StaticMeshComponent)
    if source_component.static_mesh.get_path_name()!=before['source_actors'][0]['mesh']:raise ValueError('Original mesh changed')
    existing=[a for a in city_world._actors() if TAG in a.tags]
    if existing:raise RuntimeError('Reference scope already exists; verify instead of duplicating')
    if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():raise RuntimeError('Wait for other imports')
    meshes={};records={r['id']:r for r in data['prepared']['assets']}
    for kind,record in records.items():
        if hashlib.sha256(Path(record['fbx']).read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Changed reference export')
        name=kind+'_'+record['sha256'][:10];path=ROOT+'/Meshes/'+name
        if not unreal.EditorAssetLibrary.does_asset_exist(path):
            task=unreal.AssetImportTask();task.filename=record['fbx'];task.destination_path=ROOT+'/Meshes';task.destination_name=name
            task.automated=True;task.save=True;options=unreal.FbxImportUI();options.import_mesh=True
            options.import_materials=False;options.import_textures=False;options.import_as_skeletal=False
            options.static_mesh_import_data.combine_meshes=True;options.static_mesh_import_data.auto_generate_collision=False
            options.static_mesh_import_data.generate_lightmap_u_vs=False;task.options=options
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        mesh=unreal.load_asset(path)
        if mesh is None:raise RuntimeError('Reference mesh import failed')
        box=mesh.get_bounding_box();expected=record['bounds_m']
        actual=[box.max.x-box.min.x,box.max.y-box.min.y,box.max.z-box.min.z]
        dims=[(b-a)*100 for a,b in zip(*expected)]
        if any(abs(a-b)>max(.1,b*.001) for a,b in zip(actual,dims)):raise ValueError('Import dimensions changed: '+kind+str(actual))
        if mesh.get_num_triangles(0)!=record['triangles']:raise ValueError('Triangle count differs: '+kind)
        meshes[kind]=mesh
    cream=material('M_ReferenceBody','body',data);brown=material('M_ReferenceBand','brown',data);glass=material('M_ReferenceGlass','glass',data)
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem);made=[]
    try:
        actor=subsystem.spawn_actor_from_class(unreal.StaticMeshActor,unreal.Vector());made.append(actor)
        actor.set_actor_label('Reference visual source batch - LGB historical facade');actor.tags=[TAG,'SOURCE_GEOMETRY_VISUAL_COPY']
        actor.set_actor_transform(source.get_actor_transform(),False,True)
        component=actor.get_component_by_class(unreal.StaticMeshComponent);component.set_static_mesh(meshes['source_visual_batch'])
        for index,slot in enumerate(meshes['source_visual_batch'].get_editor_property('static_materials')):
            slot_name=str(slot.material_slot_name)
            component.set_material(index,cream if 'ReferenceFacade' in slot_name else source_component.get_material(0))
        component.set_editor_property('use_default_collision',False);component.set_collision_profile_name('NoCollision')
        component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION);actor.set_actor_enable_collision(False)
        actor.set_editor_property('is_spatially_loaded',source.get_editor_property('is_spatially_loaded'))
        for kind,mat in [('cornice',brown),('glass',glass)]:
            cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances')
            detail=subsystem.spawn_actor_from_class(cls,unreal.Vector());made.append(detail)
            detail.set_actor_label('Reference balcony '+kind+' - LGB historical approximation');detail.tags=[TAG,'ESTIMATED_ARCHITECTURE']
            detail.set_street_mesh(meshes[kind]);detail.set_street_collision(False);detail.set_street_cull_distance(15000,25000)
            comp=detail.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
            for slot in range(comp.get_num_materials()):comp.set_material(slot,mat)
            for item in data['placements']:
                if item['module']!=kind:continue
                transform=unreal.Transform(location=explore_city._world(item['position_m']),
                    rotation=unreal.Rotator(pitch=0,yaw=-item['yaw_deg'],roll=0),scale=unreal.Vector(*item['scale']))
                if detail.add_street_instance(transform,item['id'])<0:raise RuntimeError('Detail placement failed')
        source.modify();source_component.modify();source_component.set_visibility(False,True)
        packages=[a.get_outermost() for a in [*made,source]]
        if not unreal.EditorLoadingAndSavingUtils.save_packages(packages,True):raise RuntimeError('Scoped save failed')
    except Exception:
        source_component.set_visibility(before['source_actors'][0]['visible'],True)
        for actor in made:subsystem.destroy_actor(actor)
        raise
    report={'status':'saved','source_id':data['observation']['source_id'],'actors':[a.get_path_name() for a in made],
        'source_actor':source.get_path_name(),'original_mesh':source_component.static_mesh.get_path_name(),
        'original_collision':str(source_component.get_collision_enabled()),'source_geometry_triangles':records['source_visual_batch']['triangles'],
        'detail_instances':len(data['placements']),'materials':[m.get_path_name() for m in [cream,brown,glass]],
        'limitations':data['limits'],'reload_verified':False}
    (DATA/'native-install.json').write_text(json.dumps(report,indent=2))
    return report
