"""Reversible city surface finishes using local licensed PBR assets.

Source geometry, collision, source IDs and measured heights are untouched.
Window patterns and material assignment are illustrative, not surveyed Beirut.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import unreal

ROOT='/Game/Beirut/CityFinishes'
PALETTE=[('Limestone',(.72,.65,.51)),('WarmPlaster',(.68,.60,.48)),
         ('Ivory',(.81,.77,.66)),('Concrete',(.47,.48,.46)),
         ('Sand',(.62,.49,.34)),('FadedRose',(.65,.46,.40))]


def _geometry_signature(actors):
    rows=[]
    for actor in actors:
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        if component:
            mesh=component.get_editor_property('static_mesh')
            location=actor.get_actor_location();rotation=actor.get_actor_rotation();scale=actor.get_actor_scale3d()
            transform=[location.x,location.y,location.z,rotation.pitch,rotation.yaw,rotation.roll,scale.x,scale.y,scale.z]
            rows.append((actor.get_path_name(),transform,
                         mesh.get_path_name() if mesh else None,
                         str(component.get_collision_enabled()),str(component.get_collision_profile_name())))
    return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()


def _texture(filename,linear=False):
    filename=Path(filename)
    name=filename.stem.replace('-','_')
    path=ROOT+'/Textures/'+name
    if not unreal.EditorAssetLibrary.does_asset_exist(path):
        task=unreal.AssetImportTask()
        task.set_editor_property('filename',str(filename))
        task.set_editor_property('destination_path',ROOT+'/Textures')
        task.set_editor_property('destination_name',name)
        task.set_editor_property('automated',True)
        task.set_editor_property('save',True)
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    texture=unreal.load_asset(path)
    if texture is None:raise RuntimeError('Texture import failed: '+str(filename))
    texture.set_editor_property('srgb',not linear)
    unreal.EditorAssetLibrary.save_loaded_asset(texture)
    return texture


def _material(name,colour,roughness,*,facade=False,tint=(1,1,1),scale=300,roof=None):
    lib=unreal.MaterialEditingLibrary
    path=ROOT+'/'+name
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        mat=unreal.load_asset(path)
        lib.delete_all_material_expressions(mat)
    else:mat=unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,ROOT,unreal.Material,unreal.MaterialFactoryNew())
    custom=lib.create_material_expression(mat,unreal.MaterialExpressionCustom,0,0)
    custom.set_editor_property('output_type',unreal.CustomMaterialOutputType.CMOT_FLOAT4)
    inputs=[]
    for key in ('Position','Normal','Albedo','Rough','Tint','Roof'):
        item=unreal.CustomInput();item.set_editor_property('input_name',key);inputs.append(item)
    custom.set_editor_property('inputs',inputs)
    code='''
float3 n=normalize(Normal);
float3 w=pow(abs(n),8); w/=max(w.x+w.y+w.z,.0001);
float3 p=Position/SCALE;
float3 c=Texture2DSample(Albedo,AlbedoSampler,p.yz).rgb*w.x
        +Texture2DSample(Albedo,AlbedoSampler,p.xz).rgb*w.y
        +Texture2DSample(Albedo,AlbedoSampler,p.xy).rgb*w.z;
float r=Texture2DSample(Rough,RoughSampler,p.yz).r*w.x
       +Texture2DSample(Rough,RoughSampler,p.xz).r*w.y
       +Texture2DSample(Rough,RoughSampler,p.xy).r*w.z;
c*=Tint;
'''.replace('SCALE',str(float(scale)))
    if facade:
        code+='''
float wall=1-step(.55,abs(n.z));
float along=dot(Position.xy,normalize(float2(-n.y,n.x)+float2(.00001,0)));
float u=frac(along/310.0), v=frac(Position.z/320.0);
float outer=step(.17,u)*(1-step(.68,u))*step(.26,v)*(1-step(.79,v))*wall;
float pane=step(.20,u)*(1-step(.65,u))*step(.29,v)*(1-step(.76,v))*wall;
float mullion=step(.416,u)*(1-step(.432,u))*pane;
float sill=step(.235,v)*(1-step(.26,v))*wall;
float variation=.5+.5*sin(floor(along/310.0)*7.13+floor(Position.z/320.0)*13.71);
float3 glass=lerp(float3(.025,.039,.046),float3(.105,.15,.17),variation);
c=lerp(c,float3(.22,.21,.19),outer);
c=lerp(c,glass,pane*(1-mullion));
c*=1-.18*sill;
// Roofs remain distinct from walls. These patterns are presentation defaults.
c=lerp(c,Texture2DSample(Roof,RoofSampler,Position.xy/350.0).rgb*float3(.60,.57,.51),1-wall);
r=lerp(r,.27,pane*(1-mullion));
'''
    code+='return float4(c,clamp(r,.22,.98));'
    custom.set_editor_property('code',code)
    for cls,key in ((unreal.MaterialExpressionWorldPosition,'Position'),(unreal.MaterialExpressionVertexNormalWS,'Normal')):
        node=lib.create_material_expression(mat,cls,-600,0)
        lib.connect_material_expressions(node,'',custom,key)
    for tex,key in ((colour,'Albedo'),(roughness,'Rough'),(roof or colour,'Roof')):
        node=lib.create_material_expression(mat,unreal.MaterialExpressionTextureObject,-600,200)
        node.set_editor_property('texture',tex)
        lib.connect_material_expressions(node,'',custom,key)
    paint=lib.create_material_expression(mat,unreal.MaterialExpressionConstant3Vector,-600,400)
    paint.set_editor_property('constant',unreal.LinearColor(*tint,1))
    lib.connect_material_expressions(paint,'',custom,'Tint')
    for channels,prop in (((True,True,True,False),unreal.MaterialProperty.MP_BASE_COLOR),
                          ((False,False,False,True),unreal.MaterialProperty.MP_ROUGHNESS)):
        mask=lib.create_material_expression(mat,unreal.MaterialExpressionComponentMask,350,0)
        for key,value in zip(('r','g','b','a'),channels):mask.set_editor_property(key,value)
        if not lib.connect_material_expressions(custom,'',mask,''):
            raise RuntimeError('Could not connect generated material output mask')
        lib.connect_material_property(mask,'',prop)
    lib.recompile_material(mat);unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def apply(asset_folder):
    """Use verified local ambientCG surface packages, then save reversible overrides."""
    import city_world
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before applying city finishes')
    folder=Path(asset_folder)
    manifest=json.loads((folder/'manifest.json').read_text())
    assets={a['id']:a for a in manifest['assets']}
    textures={}
    for name in ('Asphalt012','Plaster001','Concrete030'):
        if assets[name]['license']!='CC0 1.0':raise ValueError('Unexpected asset license')
        files=list((folder/name).glob('*.jpg'))
        def choose(suffix):return next(p for p in files if suffix in p.stem)
        textures[name]=(_texture(choose('_Color')),_texture(choose('_Roughness'),True))
    stone_manifest=folder.parent/'PolyHaven'/'plastered_stone_wall'/'manifest.json'
    stone=None
    if stone_manifest.is_file():
        source=json.loads(stone_manifest.read_text())
        if source['license']!='CC0 1.0':raise ValueError('Unexpected stone asset license')
        maps={record['role']:record['path'] for record in source['files']}
        stone=(_texture(maps['Diffuse']),_texture(maps['Rough'],True))
    facades=[_material('M_Facade_'+name,*(stone if name=='Limestone' and stone else textures['Plaster001']),
                       facade=True,tint=colour,scale=180,roof=textures['Concrete030'][0]) for name,colour in PALETTE]
    asphalt=_material('M_Asphalt',*textures['Asphalt012'],scale=400)
    concrete=_material('M_Concrete',*textures['Concrete030'],tint=(.78,.75,.68),scale=300)
    pavement=_material('M_Pavement',*textures['Concrete030'],tint=(1.35,1.35,1.36),scale=200)
    paint=_material('M_LaneMarking',*textures['Concrete030'],tint=(1.6,1.6,1.55),scale=200)
    ground=_material('M_UnclassifiedGround',*textures['Concrete030'],tint=(.49,.46,.40),scale=500)
    backup=city_world._root()/'Saved'/'DynaCity'/'city-finish-backup.json'
    previous=json.loads(backup.read_text()) if backup.is_file() else {}
    changes=[];actors=city_world._actors();before=_geometry_signature(actors)
    for actor in actors:
        label=actor.get_actor_label()
        if label.startswith('buildings_'):
            material=facades[int(label.rsplit('_',1)[1])%len(facades)]
        # Road batches carry asphalt as material 1 and pavements/kerbs as material 2.
        elif label.startswith('roads_'):
            # Material 10 is lane paint (shared-city-surface/3); 2 is pavement.
            material=paint if label.endswith('_material_10') else pavement if label.endswith('_material_2') else asphalt
        elif label.startswith('source-ground') or label in ('StreetCover_TerrainExtension','StreetCover_TerrainGapRepair'):material=ground
        elif label.startswith('beirut001_transport'):material=concrete
        else:continue
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        if not component:continue
        actor.modify();component.modify()
        key=actor.get_path_name()
        if key not in previous:
            previous[key]=[m.get_path_name() if m else None for m in component.get_editor_property('override_materials')]
        for slot in range(component.get_num_materials()):component.set_material(slot,material)
        changes.append({'actor':key,'label':label,'material':material.get_path_name()})
    backup.write_text(json.dumps(previous,indent=2))
    if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():raise RuntimeError('Could not save city materials')
    if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True):raise RuntimeError('Could not save external actor materials')
    after=_geometry_signature(actors)
    if before!=after:raise RuntimeError('Actor geometry/collision signature changed unexpectedly')
    report={'schema':'dynacity-city-finishes/1','actors':changes,'asset_manifest':str(folder/'manifest.json'),
        'geometry_signature_before':before,'geometry_signature_after':after,
        'stone_asset_manifest':str(stone_manifest) if stone else None,
        'limits':['Generic licensed surface finishes; not sampled from Beirut imagery.',
                  'Window positions and facade colours are illustrative. Ground classification is not inferred.',
                  'Geometry, heights, collision and source identities are unchanged.']}
    (backup.parent/'city-finishes.json').write_text(json.dumps(report,indent=2))
    return {'actors':len(changes),'report':str(backup.parent/'city-finishes.json')}


def restore():
    import city_world
    city_world._current()
    path=city_world._root()/'Saved'/'DynaCity'/'city-finish-backup.json'
    records=json.loads(path.read_text());count=0
    for actor in city_world._actors():
        if actor.get_path_name() not in records:continue
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        component.set_editor_property('override_materials',[unreal.load_asset(p) if p else None for p in records[actor.get_path_name()]])
        count+=1
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()
    return count


def daylight():
    """Illustrative clear daylight balanced for this scene's exposure settings."""
    import city_world
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before adjusting scene lighting')
    actors=city_world._actors()
    sun=next(a for a in actors if isinstance(a,unreal.DirectionalLight))
    sky=next(a for a in actors if isinstance(a,unreal.SkyLight))
    volume=next(a for a in actors if isinstance(a,unreal.PostProcessVolume))
    light=sun.get_component_by_class(unreal.DirectionalLightComponent)
    fill=sky.get_component_by_class(unreal.SkyLightComponent)
    settings=volume.get_editor_property('settings')
    keys=('auto_exposure_bias','auto_exposure_min_brightness','auto_exposure_max_brightness',
          'override_auto_exposure_bias','override_auto_exposure_min_brightness','override_auto_exposure_max_brightness')
    backup=city_world._root()/'Saved'/'DynaCity'/'city-daylight-backup.json'
    if not backup.is_file():
        backup.write_text(json.dumps({'sun_intensity':light.get_editor_property('intensity'),
            'sun_rotation':[sun.get_actor_rotation().pitch,sun.get_actor_rotation().yaw,sun.get_actor_rotation().roll],
            'sky_intensity':fill.get_editor_property('intensity'),
            'exposure':{key:settings.get_editor_property(key) for key in keys}},indent=2))
    light.set_intensity(10)
    sun.set_actor_rotation(unreal.Rotator(pitch=-55,yaw=-35,roll=0),False)
    fill.set_intensity(8)
    fill.recapture_sky()
    for key,value in {'auto_exposure_method':unreal.AutoExposureMethod.AEM_MANUAL,
                      'override_auto_exposure_method':True,
                      'auto_exposure_bias':-.5,'override_auto_exposure_bias':True,
                      'override_auto_exposure_min_brightness':False,
                      'override_auto_exposure_max_brightness':False}.items():settings.set_editor_property(key,value)
    volume.set_editor_property('settings',settings)
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()
    return {'sun_intensity':10,'sky_intensity':8,'presentation':'Illustrative daylight, not a historical weather or solar-position reconstruction'}


def restore_daylight():
    import city_world
    city_world._current()
    data=json.loads((city_world._root()/'Saved'/'DynaCity'/'city-daylight-backup.json').read_text())
    actors=city_world._actors()
    sun=next(a for a in actors if isinstance(a,unreal.DirectionalLight))
    sky=next(a for a in actors if isinstance(a,unreal.SkyLight))
    volume=next(a for a in actors if isinstance(a,unreal.PostProcessVolume))
    sun.get_component_by_class(unreal.DirectionalLightComponent).set_intensity(data['sun_intensity'])
    pitch,yaw,roll=data['sun_rotation']
    sun.set_actor_rotation(unreal.Rotator(pitch=pitch,yaw=yaw,roll=roll),False)
    sky.get_component_by_class(unreal.SkyLightComponent).set_intensity(data['sky_intensity'])
    settings=volume.get_editor_property('settings')
    for key,value in data['exposure'].items():settings.set_editor_property(key,value)
    volume.set_editor_property('settings',settings)
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()


# ---- Mapped parking cover: keeps its flat imported GLB colour (linear .20, about 3x brighter than the asphalt beside it) ----
# Minimal, scoped assignment of the EXISTING M_Asphalt to named StreetCover_<area>_parking_<hash8> actors. Does not create or
# recompile any material, touch other actors, or save anything but the touched actors' own packages.
PARKING_COVER=re.compile(r'StreetCover_.+_parking_[0-9a-f]{8}')


def is_parking_cover_label(label):
    return PARKING_COVER.fullmatch(label) is not None


def _parking_actors(labels):
    import city_world
    found=[a for a in city_world._actors() if is_parking_cover_label(a.get_actor_label()) and a.get_component_by_class(unreal.StaticMeshComponent)]
    if labels is not None:
        found=[a for a in found if a.get_actor_label() in set(labels)]
        if sorted(a.get_actor_label() for a in found)!=sorted(set(labels)):raise RuntimeError('Parking cover actor labels not all found')
    return found


def apply_parking_cover(labels,backup_dir):
    """Assign /Game/Beirut/CityFinishes/M_Asphalt to the listed parking cover actors (labels=None means every one)."""
    import city_world
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():raise RuntimeError('End Play before applying parking cover')
    asphalt=unreal.load_asset(ROOT+'/M_Asphalt')
    if asphalt is None:raise RuntimeError('M_Asphalt is missing; run the city finishes first')
    actors=_parking_actors(labels)
    backup_dir=Path(backup_dir);backup_dir.mkdir(parents=True,exist_ok=True)
    record_path=backup_dir/'parking-cover-backup.json'
    records=json.loads(record_path.read_text()) if record_path.is_file() else {}
    before=_geometry_signature(actors);changed=[]
    for actor in actors:
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        key=actor.get_path_name()
        if key not in records:
            records[key]={'label':actor.get_actor_label(),'package':actor.get_outermost().get_path_name(),
                'slot_materials':[component.get_material(i).get_path_name() if component.get_material(i) else None for i in range(component.get_num_materials())],
                'override_materials':[m.get_path_name() if m else None for m in component.get_editor_property('override_materials')]}
            name=records[key]['package']
            source=Path(unreal.Paths.project_content_dir())/(name[6:]+'.uasset') if name.startswith('/Game/') else None
            if source and source.is_file():
                target=backup_dir/'package-backup'/(name[6:]+'.uasset');target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists():shutil.copy2(source,target)
        actor.modify();component.modify()
        for slot in range(component.get_num_materials()):component.set_material(slot,asphalt)
        changed.append(actor.get_actor_label())
    record_path.write_text(json.dumps(records,indent=1))
    if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in actors}),True):raise RuntimeError('Parking cover package save failed')
    if before!=_geometry_signature(actors):raise RuntimeError('Parking cover geometry/collision signature changed unexpectedly')
    return {'changed':changed,'material':asphalt.get_path_name(),'geometry_signature':before,'backup':str(record_path)}


def restore_parking_cover(backup_dir,labels=None):
    """Put the saved original material assignment back (override list cleared when there was none) and save only those packages."""
    record_path=Path(backup_dir)/'parking-cover-backup.json'
    records=json.loads(record_path.read_text())
    actors=[a for a in _parking_actors(None) if a.get_path_name() in records and (labels is None or a.get_actor_label() in set(labels))]
    before=_geometry_signature(actors);restored=[]
    for actor in actors:
        record=records[actor.get_path_name()];component=actor.get_component_by_class(unreal.StaticMeshComponent)
        actor.modify();component.modify()
        if not any(record['override_materials']):component.set_editor_property('override_materials',[])
        else:
            for slot,path in enumerate(record['override_materials']):
                if path:component.set_material(slot,unreal.load_asset(path))
        restored.append(actor.get_actor_label())
    if actors and not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in actors}),True):raise RuntimeError('Parking cover package save failed')
    if before!=_geometry_signature(actors):raise RuntimeError('Parking cover geometry/collision signature changed unexpectedly')
    return {'restored':restored}
