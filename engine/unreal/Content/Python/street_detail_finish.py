"""Scoped paving finishes for the generated street layer.

Uses cached CC0 concrete maps; does not alter source geometry.
Joint size and colours are illustrative rather than surveyed street materials.
"""
import hashlib
import json
from pathlib import Path

import unreal

ROOT='/Game/Beirut/StreetDetailFinishes'
TAG='DYNACITY_STREET_DETAIL_V1'


def _material(kind, colour, rough):
    lib=unreal.MaterialEditingLibrary
    version=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:10]
    name='M_'+kind+'_'+version;path=ROOT+'/'+name
    if unreal.EditorAssetLibrary.does_asset_exist(path):return unreal.load_asset(path)
    mat=unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,ROOT,unreal.Material,unreal.MaterialFactoryNew())
    shader=lib.create_material_expression(mat,unreal.MaterialExpressionCustom,0,0)
    shader.set_editor_property('output_type',unreal.CustomMaterialOutputType.CMOT_FLOAT4)
    inputs=[]
    for key in ('Position','Albedo','Rough'):
        item=unreal.CustomInput();item.set_editor_property('input_name',key);inputs.append(item)
    shader.set_editor_property('inputs',inputs)
    code='''
float2 uv=Position.xy/140.;
float3 aggregate=Texture2DSample(Albedo,AlbedoSampler,uv).rgb;
float roughness=Texture2DSample(Rough,RoughSampler,uv).r;
'''
    if kind=='sidewalk':
        code+='''
float row=floor(Position.y/40.);
float2 cell=frac(float2(Position.x/60.+fmod(abs(row),2.)*.5,Position.y/40.));
float edge=min(min(cell.x,1.-cell.x)*60.,min(cell.y,1.-cell.y)*40.);
float joint=1.-smoothstep(.16,.42,edge);
float variation=.93+.09*frac(sin(dot(floor(float2(Position.x/60.+fmod(abs(row),2.)*.5,Position.y/40.)),float2(12.9898,78.233)))*43758.5453);
float3 c=aggregate*float3(1.08,1.02,.87)*variation;
c=lerp(c,float3(.075,.068,.057),joint*.85);
return float4(c,clamp(roughness,.78,.96));
'''
    else:
        code+='''
float3 c=aggregate*float3(.79,.77,.71);
return float4(c,clamp(roughness,.70,.95));
'''
    shader.set_editor_property('code',code)
    pos=lib.create_material_expression(mat,unreal.MaterialExpressionWorldPosition,-500,0)
    lib.connect_material_expressions(pos,'',shader,'Position')
    for key,texture in (('Albedo',colour),('Rough',rough)):
        node=lib.create_material_expression(mat,unreal.MaterialExpressionTextureObject,-500,200)
        node.set_editor_property('texture',texture)
        lib.connect_material_expressions(node,'',shader,key)
    for channels,prop in (((True,True,True,False),unreal.MaterialProperty.MP_BASE_COLOR),
                          ((False,False,False,True),unreal.MaterialProperty.MP_ROUGHNESS)):
        mask=lib.create_material_expression(mat,unreal.MaterialExpressionComponentMask,350,0)
        for key,value in zip(('r','g','b','a'),channels):mask.set_editor_property(key,value)
        lib.connect_material_expressions(shader,'',mask,'')
        lib.connect_material_property(mask,'',prop)
    lib.recompile_material(mat)
    if not unreal.EditorAssetLibrary.save_loaded_asset(mat):raise RuntimeError('Paving material save failed')
    return mat


def apply(folder, persist=True):
    import city_world,style_city
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before saving generated street finishes')
    city_world._current()
    folder=Path(folder);source=json.loads((folder/'manifest.json').read_text())
    record=next(a for a in source['assets'] if a['id']=='Concrete030')
    if record['license']!='CC0 1.0':raise ValueError('Expected cached licensed concrete')
    archive=folder/'Concrete030_1K-JPG.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Concrete source archive changed')
    maps=folder/'Concrete030'
    colour=style_city._texture(maps/'Concrete030_1K-JPG_Color.jpg')
    rough=style_city._texture(maps/'Concrete030_1K-JPG_Roughness.jpg',True)
    materials={kind:_material(kind,colour,rough) for kind in ('sidewalk','curb')}
    actors=[a for a in city_world._actors() if TAG in a.tags]
    game=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    live=[a for a in unreal.GameplayStatics.get_all_actors_of_class(game,unreal.Actor) if TAG in a.tags] if game else []
    before={a.get_path_name():[a.get_street_instance_count(),list(a.get_editor_property('source_ids'))] for a in actors}
    backup=city_world._root()/'Saved/DynaCity/street-detail-finish-backup.json'
    if not backup.exists():
        backup.write_text(json.dumps({a.get_path_name():[str(m.get_path_name()) if m else None for m in a.get_editor_property('instances').get_editor_property('override_materials')] for a in actors},indent=2))
    for actor in actors+live:
        kind=str(actor.get_editor_property('layer_kind'))
        if kind not in materials:continue
        if actor in actors:actor.modify()
        actor.get_editor_property('instances').set_material(0,materials[kind])
    after={a.get_path_name():[a.get_street_instance_count(),list(a.get_editor_property('source_ids'))] for a in actors}
    if before!=after:raise RuntimeError('Surface finish changed source identities')
    if persist and actors:
        packages=list({a.get_outermost() for a in actors})
        if not unreal.EditorLoadingAndSavingUtils.save_packages(packages,True):raise RuntimeError('Generated actor finish save failed')
    report={'editor_groups':len(actors),'live_groups':len(live),'instances':sum(a.get_street_instance_count() for a in actors),
            'materials':{k:m.get_path_name() for k,m in materials.items()},'geometry_and_ids_unchanged':before==after,
            'saved':persist,'play_interrupted':False,'source':'ambientCG Concrete030, CC0 1.0',
            'limits':'Paving joints and colours are illustrative; layout gaps remain separate review items.'}
    (city_world._root()/'Saved/DynaCity/street-detail-finish.json').write_text(json.dumps(report,indent=2))
    return report
