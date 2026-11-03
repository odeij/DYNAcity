"""Finished-model import and source-authored PBR adaptation. No mesh authoring."""
import hashlib
import json
from pathlib import Path
import re
import unreal

ROOT='/Game/Beirut/LibraryAssets'


# Dense photogrammetry scans (>300k source triangles) use Nanite like the tree.
NANITE_KEYS=('tree_small_02','fountain_scan_durants_park','chimney_scan_historic_remnant','water_tower_scan_essen_steele')


def _name(value):return re.sub('[^A-Za-z0-9_]','_',value)


def _texture(filename,role):
    filename=Path(filename);digest=hashlib.sha256(filename.read_bytes()).hexdigest()[:12]
    name='T_'+digest+'_'+role;path=ROOT+'/Textures/'+name
    if not unreal.EditorAssetLibrary.does_asset_exist(path):
        task=unreal.AssetImportTask();task.filename=str(filename);task.destination_path=ROOT+'/Textures'
        task.destination_name=name;task.automated=True;task.save=True
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    texture=unreal.load_asset(path)
    if texture is None:raise RuntimeError('Library texture import failed: '+str(filename))
    texture.set_editor_property('srgb',role in ('color','emissive'))
    if role=='normal':
        texture.set_editor_property('compression_settings',unreal.TextureCompressionSettings.TC_NORMALMAP)
        texture.set_editor_property('flip_green_channel',True)
    elif role=='masks':texture.set_editor_property('compression_settings',unreal.TextureCompressionSettings.TC_MASKS)
    unreal.EditorAssetLibrary.save_loaded_asset(texture)
    return texture


def _material(data,definition,name):
    if definition.get('dynacity_traffic_paint'):name+='_paint_v2'
    lib=unreal.MaterialEditingLibrary;path=ROOT+'/Materials/'+name
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        mat=unreal.load_asset(path)
        if not mat.get_editor_property('used_with_instanced_static_meshes'):
            mat.set_editor_property('used_with_instanced_static_meshes',True)
            lib.recompile_material(mat);unreal.EditorAssetLibrary.save_loaded_asset(mat)
        return mat
    mat=unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,ROOT+'/Materials',unreal.Material,unreal.MaterialFactoryNew())
    mat.set_editor_property('used_with_instanced_static_meshes',True)
    mat.set_editor_property('two_sided',definition.get('doubleSided',False))
    pbr=definition.get('pbrMetallicRoughness',{});color=pbr.get('baseColorFactor',[1,1,1,1])
    alpha=definition.get('alphaMode','OPAQUE')
    if alpha=='BLEND' and 'leaves' in definition.get('name','').lower():alpha='MASK'
    # The upstream three.js showcase uses clear transparent glass. Adapt that
    # published treatment to Unreal instead of leaving its FBX glass opaque.
    if definition.get('name')=='Glass_Gray':alpha='BLEND';color=[*color[:3],.12]
    if alpha=='MASK':
        mat.set_editor_property('blend_mode',unreal.BlendMode.BLEND_MASKED)
        mat.set_editor_property('opacity_mask_clip_value',definition.get('alphaCutoff',.5))
    elif alpha=='BLEND':mat.set_editor_property('blend_mode',unreal.BlendMode.BLEND_TRANSLUCENT)
    def constant(value):
        if isinstance(value,(tuple,list)):
            node=lib.create_material_expression(mat,unreal.MaterialExpressionConstant3Vector,-600,0)
            node.set_editor_property('constant',unreal.LinearColor(*value[:3],1))
        else:
            node=lib.create_material_expression(mat,unreal.MaterialExpressionConstant,-600,0)
            node.set_editor_property('r',float(value))
        return node
    def sample(info,role):
        from library_asset_core import texture_source_index
        filename=data['images'][texture_source_index(data['textures'][info['index']])]
        node=lib.create_material_expression(mat,unreal.MaterialExpressionTextureSample,-600,150)
        node.set_editor_property('texture',_texture(filename,role))
        if role=='normal':node.set_editor_property('sampler_type',unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        elif role=='masks':node.set_editor_property('sampler_type',unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        if info.get('texCoord',0):
            uv=lib.create_material_expression(mat,unreal.MaterialExpressionTextureCoordinate,-800,150)
            uv.set_editor_property('coordinate_index',info['texCoord']);lib.connect_material_expressions(uv,'',node,'UVs')
        return node
    def multiply(node,channel,value):
        if value==1:return node,channel
        result=lib.create_material_expression(mat,unreal.MaterialExpressionMultiply,-200,0)
        lib.connect_material_expressions(node,channel,result,'A');lib.connect_material_expressions(constant(value),'',result,'B')
        return result,''
    base=constant(color)
    if 'baseColorTexture' in pbr:
        tex=sample(pbr['baseColorTexture'],'color')
        combined=lib.create_material_expression(mat,unreal.MaterialExpressionMultiply,-200,0)
        lib.connect_material_expressions(tex,'RGB',combined,'A');lib.connect_material_expressions(base,'',combined,'B');base=combined
        if alpha!='OPAQUE':
            opacity,channel=multiply(tex,'A',color[3]);lib.connect_material_property(opacity,channel,
                unreal.MaterialProperty.MP_OPACITY_MASK if alpha=='MASK' else unreal.MaterialProperty.MP_OPACITY)
    elif alpha!='OPAQUE':lib.connect_material_property(constant(color[3]),'',unreal.MaterialProperty.MP_OPACITY)
    if definition.get('dynacity_traffic_paint'):
        # Only explicitly reviewed body slots receive paint. Glass, lamps,
        # tyres, plates and interiors retain their authored materials.
        channels=[]
        for index in range(4):
            node=lib.create_material_expression(mat,unreal.MaterialExpressionPerInstanceCustomData,-700,400+index*80)
            node.set_editor_property('data_index',index)
            node.set_editor_property('const_default_value',0.0)
            # Custom instance data is read in the vertex stage; interpolate it
            # before using it in the pixel-stage body paint expression.
            interp=lib.create_material_expression(mat,unreal.MaterialExpressionVertexInterpolator,-500,400+index*80)
            if not lib.connect_material_expressions(node,'',interp,'VS'):
                raise RuntimeError('Could not connect per-instance traffic colour to its vertex interpolator')
            channels.append(interp)
        rg=lib.create_material_expression(mat,unreal.MaterialExpressionAppendVector,-300,400)
        rgb=lib.create_material_expression(mat,unreal.MaterialExpressionAppendVector,-200,400)
        lib.connect_material_expressions(channels[0],'',rg,'A');lib.connect_material_expressions(channels[1],'',rg,'B')
        lib.connect_material_expressions(rg,'',rgb,'A');lib.connect_material_expressions(channels[2],'',rgb,'B')
        blend=lib.create_material_expression(mat,unreal.MaterialExpressionLinearInterpolate,-100,100)
        lib.connect_material_expressions(base,'',blend,'A');lib.connect_material_expressions(rgb,'',blend,'B')
        lib.connect_material_expressions(channels[3],'',blend,'Alpha');base=blend
    lib.connect_material_property(base,'',unreal.MaterialProperty.MP_BASE_COLOR)
    mr=sample(pbr['metallicRoughnessTexture'],'masks') if 'metallicRoughnessTexture' in pbr else None
    for factor,channel,prop in ((pbr.get('metallicFactor',1),'B',unreal.MaterialProperty.MP_METALLIC),
                                (pbr.get('roughnessFactor',1),'G',unreal.MaterialProperty.MP_ROUGHNESS)):
        if definition.get('name')=='Glass_Gray' and prop==unreal.MaterialProperty.MP_ROUGHNESS:factor=.05
        node,out=multiply(mr,channel,factor) if mr else (constant(factor),'')
        lib.connect_material_property(node,out,prop)
    if 'normalTexture' in definition:lib.connect_material_property(sample(definition['normalTexture'],'normal'),'RGB',unreal.MaterialProperty.MP_NORMAL)
    if 'occlusionTexture' in definition:lib.connect_material_property(sample(definition['occlusionTexture'],'masks'),'R',unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
    if 'emissiveTexture' in definition:
        lib.connect_material_property(sample(definition['emissiveTexture'],'emissive'),'RGB',unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    elif any(definition.get('emissiveFactor',[0,0,0])):
        lib.connect_material_property(constant(definition['emissiveFactor']),'',unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    lib.recompile_material(mat);unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def import_prepared(folder,key):
    from library_asset_core import read_prepared
    folder=Path(folder);data=read_prepared(folder)
    version=data['fbx_sha256'][:10];name=_name(key)+'_'+version;path=ROOT+'/Meshes/'+name
    if not unreal.EditorAssetLibrary.does_asset_exist(path):
        task=unreal.AssetImportTask();task.filename=data['fbx'];task.destination_path=ROOT+'/Meshes';task.destination_name=name
        task.automated=True;task.save=True
        options=unreal.FbxImportUI();options.import_mesh=True;options.import_materials=False;options.import_textures=False
        options.import_as_skeletal=False;options.static_mesh_import_data.combine_meshes=True
        options.static_mesh_import_data.generate_lightmap_u_vs=False;task.options=options
        options.static_mesh_import_data.auto_generate_collision=False
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    mesh=unreal.load_asset(path)
    if mesh is None:raise RuntimeError('Library mesh import failed: '+key)
    from library_asset_core import material_definition
    materials={}
    for i,slot in enumerate(mesh.get_editor_property('static_materials')):
        slot_name=str(slot.material_slot_name)
        normalized=_name(slot_name)
        definition=material_definition(slot_name,data['materials'])
        material=_material(data,definition,_name(key)+'_'+version+'_'+normalized)
        mesh.set_material(i,material);materials[slot_name]=material.get_path_name()
    if key in NANITE_KEYS:
        editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        settings=editor.get_nanite_settings(mesh);settings.set_editor_property('enabled',True)
        if not editor.get_nanite_settings(mesh).get_editor_property('enabled'):
            editor.set_nanite_settings(mesh,settings,True)
    unreal.EditorAssetLibrary.save_loaded_asset(mesh)
    bounds=mesh.get_bounding_box();size=bounds.max-bounds.min
    expected=[b-a for a,b in zip(data['bounds_blender_cm']['min'],data['bounds_blender_cm']['max'])]
    if any(abs(a-b)>max(1,b*.01) for a,b in zip((size.x,size.y,size.z),expected)):
        raise ValueError('Imported library model dimensions changed unexpectedly: '+key)
    return {'mesh':mesh.get_path_name(),'prepared':str(folder/'prepared.json'),'materials':materials,
            'dimensions_cm':[size.x,size.y,size.z],'source_triangles':data['triangles'],
            'vehicle':data['vehicle'],'provenance':data['provenance']}


def active():
    import city_world
    path=city_world._root()/'Saved/DynaCity/library-assets-active.json'
    if not path.is_file():raise RuntimeError('Import approved finished vehicle/street assets before driving.')
    return json.loads(path.read_text(encoding='utf-8'))


def vehicle_mesh():
    mesh=unreal.load_asset(active()['vehicle']['mesh'])
    if mesh is None:raise RuntimeError('The approved vehicle asset is missing.')
    return mesh


def configure_vehicle(pawn):
    data=active()['vehicle'];source=data['vehicle'];eye=source['eye_chassis_cm']
    from library_asset_core import validate_vehicle
    validate_vehicle(source)
    mesh=vehicle_mesh();pawn.body.set_static_mesh(mesh);pawn.cockpit.set_static_mesh(mesh)
    pawn.cockpit.set_relative_scale3d(unreal.Vector(1,1,1))
    pawn.set_editor_property('driver_eye_cm',unreal.Vector(*eye))
    return active()


def enforce_scene_policy():
    """Hide retired procedural props; retain their source identities for replacement."""
    import city_world
    data=active();approved=data['street'];count=0
    for actor in city_world._actors():
        tags={str(t) for t in actor.tags}
        if 'DYNACITY_STREET_COLLISION' in tags:
            # Old aggregates also contain poles whose placeholder mesh is now
            # hidden. Retire that aggregate to avoid invisible obstacles.
            actor.modify();actor.set_actor_enable_collision(False)
            continue
        if not ('DYNACITY_STREET_CONTEXT' in tags or 'DYNACITY_RETIRED_PLACEHOLDER' in tags):continue
        if 'DYNACITY_USER_VISIBLE' in tags:continue
        kind=next((t.split(':',1)[1] for t in tags if t.startswith('street_kind:')),None)
        if kind in approved and 'DYNACITY_RETIRED_PLACEHOLDER' not in tags:continue
        actor.modify();actor.set_actor_hidden_in_game(True);actor.set_is_temporarily_hidden_in_editor(True)
        if 'DYNACITY_RETIRED_PLACEHOLDER' not in tags:actor.tags=[*actor.tags,'DYNACITY_RETIRED_PLACEHOLDER']
        for component in actor.get_components_by_class(unreal.PrimitiveComponent):
            component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        count+=1
    return count


def install(folder):
    import city_world,street_context
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():raise RuntimeError('End Play before installing library assets.')
    folder=Path(folder);city_world.load_editor_geometry();street_context.load_editor_geometry()
    records={key:import_prepared(folder/'prepared'/key,key) for key in
             ('modular_street_seating','metal_trash_can','tree_small_02','street_lamp_01')}
    car=import_prepared(folder/'vehicle','Ferrari458Italia')
    if car['vehicle']['handedness']!='left' or car['vehicle']['steering_ground_cm'][1]>=-15:
        raise ValueError('The approved source car does not have a left-side steering wheel.')
    existing=json.loads((city_world._root()/'Saved/DynaCity/street-prop-import.json').read_text(encoding='utf-8'))
    lamp=records['street_lamp_01']
    data={'schema':'dynacity-library-assets/1','vehicle':car,'street':{
        'bench':records['modular_street_seating'],'waste_basket':records['metal_trash_can'],
        'tree_broadleaf':records['tree_small_02'],'street_lamp':lamp},
        'traffic_vehicle_limit':150,'traffic_visibility_radius_m':250,'traffic_view_radius_m':350,
        'policy':'Finished licensed library models only. Procedural visible props and cabin are retired.',
        'vehicle_attribution':'Ferrari 458 Italia by vicent091036, CC BY 4.0; adapted from three.js example asset.',
        'library_credit':'Powered by Poly Haven'}
    saved=city_world._root()/'Saved/DynaCity';path=saved/'library-assets-active.json'
    if path.exists():
        previous=json.loads(path.read_text(encoding='utf-8'))
        data.update({key:value for key,value in previous.items() if key.startswith('traffic_')})
    if path.exists() and not (saved/'library-assets-active.previous.json').exists():
        (saved/'library-assets-active.previous.json').write_bytes(path.read_bytes())
    path.write_text(json.dumps(data,indent=2),encoding='utf-8')
    backup=saved/'library-replacement-backup.json'
    before=json.loads(backup.read_text(encoding='utf-8')) if backup.exists() else {}
    replaced=[]
    for actor in city_world._actors():
        tags={str(t) for t in actor.tags}
        if 'DYNACITY_DRIVE_START' in tags:
            actor.modify();actor.set_editor_property('vehicle_mesh',unreal.load_asset(car['mesh']))
        if 'DYNACITY_STREET_CONTEXT' not in tags:continue
        kind=next((t.split(':',1)[1] for t in tags if t.startswith('street_kind:')),None)
        if kind not in data['street']:continue
        component=actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        if not component:continue
        mesh=component.static_mesh;new=unreal.load_asset(data['street'][kind]['mesh'])
        old_height=mesh.get_bounding_box().max.z-mesh.get_bounding_box().min.z
        new_height=new.get_bounding_box().max.z-new.get_bounding_box().min.z
        key=actor.get_path_name()
        if key not in before:before[key]={'mesh':mesh.get_path_name(),'hidden':bool(actor.get_editor_property('hidden'))}
        actor.modify();component.modify()
        if mesh!=new:
            transforms=[component.get_instance_transform(i,world_space=True) for i in range(component.get_instance_count())]
            actor.set_street_mesh(new)
            # Source sizes are retained for trees. Furniture uses the library's
            # authored real-world scale, not the old placeholder dimensions.
            for i,transform in enumerate(transforms):
                transform.scale3d=transform.scale3d*(old_height/new_height) if kind.startswith('tree') else unreal.Vector(1,1,1)
                component.update_instance_transform(i,transform,world_space=True,mark_render_state_dirty=i==len(transforms)-1,teleport=True)
        actor.set_actor_hidden_in_game(False);actor.set_is_temporarily_hidden_in_editor(False)
        replaced.append({'actor':key,'kind':kind,'mesh':new.get_path_name()})
    backup.write_text(json.dumps(before,indent=2),encoding='utf-8')
    retired=enforce_scene_policy()
    if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True):raise RuntimeError('Could not save library replacements.')
    report={'vehicle':car,'replaced':replaced,'retired_groups':retired,'active_manifest':str(path)}
    (saved/'library-install-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
