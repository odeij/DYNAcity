"""Illustrative facade shading on existing source meshes; geometry is preserved.

Window spacing, colours and weathering are presentation defaults, not surveys.
Only beirut001_ labelled actors receive the reversible material override.
"""
import hashlib
import unreal

ROOT='/Game/Beirut/Prototype/Facades'
PALETTE=[('WarmConcrete',(.47,.43,.36)),('PaleStone',(.65,.60,.50)),
         ('OffWhite',(.75,.72,.65)),('GreyConcrete',(.4,.41,.4)),
         ('MutedSand',(.57,.47,.34)),('FadedRose',(.56,.43,.39))]


def _master():
    path=ROOT+'/M_IllustrativeFacade'
    if unreal.EditorAssetLibrary.does_asset_exist(path):return unreal.load_asset(path)
    mat=unreal.AssetToolsHelpers.get_asset_tools().create_asset('M_IllustrativeFacade',ROOT,unreal.Material,unreal.MaterialFactoryNew())
    lib=unreal.MaterialEditingLibrary
    code=lib.create_material_expression(mat,unreal.MaterialExpressionCustom,0,0)
    code.set_editor_property('output_type',unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    inputs=[]
    for name in ['Position','Normal','Centre','Paint']:
        item=unreal.CustomInput();item.set_editor_property('input_name',name);inputs.append(item)
    code.set_editor_property('inputs',inputs)
    code.set_editor_property('code','''
float3 n = normalize(Normal);
float3 p = Position - Centre;
float along = dot(p.xy, normalize(float2(-n.y,n.x)+float2(.00001,0)));
float u = frac(along / 300.0);
float v = frac(p.z / 320.0);
float wall = 1.0-step(.5,abs(n.z));
float window = step(.18,u)*(1.0-step(.65,u))*step(.27,v)*(1.0-step(.78,v))*wall;
float sill = step(.23,v)*(1.0-step(.27,v))*wall;
float grime = .94 + .06*sin(p.z*.003 + sin(along*.004));
float3 surface = Paint*grime;
surface *= 1.0-.13*sill;
float pane = .5+.5*sin(floor(along/300.0)*7.13+floor(p.z/320.0)*13.71);
return lerp(surface,lerp(float3(.028,.04,.047),float3(.10,.13,.14),pane),window);
''')
    nodes=[(unreal.MaterialExpressionWorldPosition,'Position'),
           (unreal.MaterialExpressionVertexNormalWS,'Normal'),
           (unreal.MaterialExpressionObjectPositionWS,'Centre')]
    for cls,name in nodes:
        node=lib.create_material_expression(mat,cls,-500,0)
        lib.connect_material_expressions(node,'',code,name)
    paint=lib.create_material_expression(mat,unreal.MaterialExpressionVectorParameter,-500,250)
    paint.set_editor_property('parameter_name','FacadePaint')
    paint.set_editor_property('default_value',unreal.LinearColor(.65,.60,.50,1))
    lib.connect_material_expressions(paint,'',code,'Paint')
    roughness=lib.create_material_expression(mat,unreal.MaterialExpressionConstant,-200,400)
    roughness.set_editor_property('r',.86)
    lib.connect_material_property(roughness,'',unreal.MaterialProperty.MP_ROUGHNESS)
    lib.connect_material_property(code,'',unreal.MaterialProperty.MP_BASE_COLOR)
    lib.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def apply(*,include_batches=False):
    master=_master();materials=[]
    for name,colour in PALETTE:
        path=ROOT+'/MI_'+name
        mat=unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
        if mat is None:
            mat=unreal.AssetToolsHelpers.get_asset_tools().create_asset('MI_'+name,ROOT,unreal.MaterialInstanceConstant,unreal.MaterialInstanceConstantFactoryNew())
        unreal.MaterialEditingLibrary.set_material_instance_parent(mat,master)
        unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(mat,'FacadePaint',unreal.LinearColor(*colour,1))
        unreal.EditorAssetLibrary.save_loaded_asset(mat);materials.append(mat)
    count=0
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        label=actor.get_actor_label()
        if not ((label.startswith('beirut001_') and not label.startswith('beirut001_transport'))
                or include_batches and label.startswith('buildings_')):continue
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        if not component:continue
        index=(int(label.rsplit('_',1)[1])%len(materials) if label.startswith('buildings_') else
               int.from_bytes(hashlib.sha256(label.encode()).digest()[:2],'little')%len(materials))
        for slot in range(component.get_num_materials()):component.set_material(slot,materials[index])
        count+=1
    unreal.log('Illustrative facade materials applied to '+str(count)+' source buildings; windows/colours are not surveyed.')
    return count


def restore():
    """Remove the overrides and return to each mesh's imported materials."""
    for actor in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        if not actor.get_actor_label().startswith('beirut001_'):continue
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        if component:component.set_editor_property('override_materials',[])
