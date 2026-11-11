"""Install an identified photographic capture as a reversible visual replacement.

Original source-batch geometry remains hidden for its existing collision.
Only the visual copy omits the indexed placeholder; other buildings are intact.
"""
import hashlib
import json
from pathlib import Path

import unreal
import toolset_registry

ROOT='/Game/Beirut/CapturedBuildings'
TAG='DYNACITY_CAPTURED_BUILDING'


def _root():
    import city_world
    return city_world._root()/'Saved/DynaCity'


def _read(path):return json.loads(Path(path).read_text(encoding='utf8'))


def _disable_visual_collision(actor, component):
    """Keep mesh-default collision from returning when PIE recreates physics.

    A patched district mesh has a very broad simple collision box. The
    untouched, hidden source actor remains the collision authority; this copy
    and the photographic capture must never supply wheel or chassis support.
    """
    component.set_editor_property('use_default_collision',False)
    component.set_collision_profile_name('NoCollision')
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    actor.set_actor_enable_collision(False)


def _visual_collision_disabled(actor):
    component=actor.get_component_by_class(unreal.StaticMeshComponent)
    return bool(component and not actor.get_actor_enable_collision()
                and not component.get_editor_property('use_default_collision')
                and component.get_collision_enabled()==unreal.CollisionEnabled.NO_COLLISION)


def _ready():
    import city_world
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before installing a building replacement')
    if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
        raise RuntimeError('Wait for the existing import to finish')


def _capture_display(mesh,folder,sid,version,triangles):
    """Keep source triangles/UVs and the original baked photographic RGB."""
    editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    nanite=editor.get_nanite_settings(mesh)
    if nanite.get_editor_property('enabled'):
        nanite.set_editor_property('enabled',False);editor.set_nanite_settings(mesh,nanite,True)
    settings=editor.get_lod_build_settings(mesh,0)
    if not settings.get_editor_property('use_full_precision_u_vs'):
        settings.set_editor_property('use_full_precision_u_vs',True)
        settings.set_editor_property('use_high_precision_tangent_basis',True)
        editor.set_lod_build_settings(mesh,0,settings)
    if mesh.get_num_triangles(0)!=triangles:raise ValueError('Native capture triangle count differs from prepared source')
    textures=[unreal.load_asset(p) for p in unreal.EditorAssetLibrary.list_assets(folder,recursive=True,include_folder=False)]
    textures=[t for t in textures if isinstance(t,unreal.Texture2D)]
    if len(textures)!=1:raise ValueError('Expected one photographic texture atlas')
    texture=textures[0]
    if texture.get_editor_property('max_texture_size')!=4096 or not texture.get_editor_property('never_stream'):
        texture.modify();texture.set_editor_property('max_texture_size',4096)
        texture.set_editor_property('mip_gen_settings',unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
        texture.set_editor_property('never_stream',True);texture.set_editor_property('lod_bias',0)
        texture.set_editor_property('srgb',True)
        unreal.EditorAssetLibrary.save_loaded_asset(texture)
    name='M_Photo_'+version;path=ROOT+'/'+sid+'/'+name
    if unreal.EditorAssetLibrary.does_asset_exist(path):material=unreal.load_asset(path)
    else:
        material=unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,ROOT+'/'+sid,unreal.Material,unreal.MaterialFactoryNew())
        material.set_editor_property('shading_model',unreal.MaterialShadingModel.MSM_UNLIT)
        material.set_editor_property('two_sided',True)
        lib=unreal.MaterialEditingLibrary
        node=lib.create_material_expression(material,unreal.MaterialExpressionTextureSample,-300,0)
        node.set_editor_property('texture',texture)
        node.set_editor_property('sampler_type',unreal.MaterialSamplerType.SAMPLERTYPE_COLOR)
        uv=lib.create_material_expression(material,unreal.MaterialExpressionTextureCoordinate,-550,0)
        uv.set_editor_property('coordinate_index',0)
        lib.connect_material_expressions(uv,'',node,'UVs')
        lib.connect_material_property(node,'RGB',unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        lib.recompile_material(material);unreal.EditorAssetLibrary.save_loaded_asset(material)
    unreal.EditorAssetLibrary.save_loaded_asset(mesh)
    return material.get_path_name()


def prepare_assets(filename):
    _ready();data=_read(filename);records={}
    if data['schema']!='dynacity-captured-building/1':raise ValueError('Unexpected capture schema')
    entries=[('capture',data['asset'],data['asset_sha256'])]
    entries.extend(('patch'+str(i),p['asset'],p['sha256']) for i,p in enumerate(data['patches']))
    for kind,asset,sha in entries:
        if hashlib.sha256(Path(asset).read_bytes()).hexdigest()!=sha:raise ValueError('Changed capture/patch bytes')
        folder=ROOT+'/'+data['source_id']+'/'+kind+'_'+sha[:12]
        paths=unreal.EditorAssetLibrary.list_assets(folder,recursive=True,include_folder=False)
        meshes=[p for p in paths if isinstance(unreal.load_asset(p),unreal.StaticMesh)]
        if not meshes:
            task=unreal.AssetImportTask();task.filename=asset;task.destination_path=folder
            task.destination_name=kind;task.automated=True;task.save=True;task.replace_existing=False
            unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
            if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
                return {'importing':True,'kind':kind,'retry':'Call prepare_assets again after import completes'}
            paths=unreal.EditorAssetLibrary.list_assets(folder,recursive=True,include_folder=False)
            meshes=[p for p in paths if isinstance(unreal.load_asset(p),unreal.StaticMesh)]
        if len(meshes)!=1:raise RuntimeError('Expected one combined mesh for '+kind+': '+str(meshes))
        records[kind]=meshes[0]
    mesh=unreal.load_asset(records['capture']);box=mesh.get_bounding_box()
    low,high=data['expected_local_bounds_m']
    expected_low=[low[0]*100,-high[1]*100,low[2]*100]
    expected_high=[high[0]*100,-low[1]*100,high[2]*100]
    actual_low=[box.min.x,box.min.y,box.min.z];actual_high=[box.max.x,box.max.y,box.max.z]
    error=max(abs(a-b) for a,b in zip(actual_low+actual_high,expected_low+expected_high))
    if error>10:raise ValueError('Capture import axis/scale differs by '+str(error)+' cm')
    photo=_capture_display(mesh,records['capture'].split('/StaticMeshes/')[0],data['source_id'],data['asset_sha256'][:12],data['export_triangles'])
    result={'manifest':str(filename),'source_id':data['source_id'],'assets':records,'bounds_error_cm':error,'photo_material':photo,
            'display':'full source LOD0, precise UVs, resident 4K original RGB atlas; baked lighting'}
    (_root()/('captured-building-assets-'+data['source_id']+'.json')).write_text(json.dumps(result,indent=2))
    return result


def install(filename):
    import city_world
    _ready();data=_read(filename);sid=data['source_id']
    prepared=_read(_root()/('captured-building-assets-'+sid+'.json'));assets=prepared['assets']
    labels={p['removed'][0]['batch'] for p in data['patches']}
    descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs if str(d.label) in labels or str(d.label).startswith(('Captured building - '+sid,'Captured visual - '+sid))])
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors=city_world._actors();old=[a for a in actors if TAG in a.tags and ('capture:'+sid) in a.tags]
    backups=_root()/('captured-building-backup-'+sid+'.json')
    backup=_read(backups) if backups.exists() else {'source_id':sid,'actors':[]}
    sources=[]
    for i,p in enumerate(data['patches']):
        label=p['removed'][0]['batch'];matches=[a for a in actors if a.get_actor_label()==label]
        if len(matches)!=1:raise RuntimeError('Missing or duplicate original batch '+label)
        actor=matches[0];component=actor.get_component_by_class(unreal.StaticMeshComponent)
        if not any(r['actor']==actor.get_path_name() for r in backup['actors']):
            backup['actors'].append({'actor':actor.get_path_name(),'label':label,
                                    'visible':component.is_visible(),'mesh':component.static_mesh.get_path_name()})
        sources.append((actor,component,assets['patch'+str(i)]))
    backups.write_text(json.dumps(backup,indent=2))
    created=[];changed=[];used=[]
    try:
        matches=[a for a in old if a.get_actor_label().startswith('Captured building - ')]
        if len(matches)>1:raise RuntimeError('Duplicate capture actors require review')
        capture=matches[0] if matches else subsystem.spawn_actor_from_class(unreal.StaticMeshActor,unreal.Vector())
        if not matches:created.append(capture)
        capture.modify();used.append(capture)
        capture.set_actor_label('Captured building - '+sid+' (damaged source; estimated placement)')
        capture.tags=[TAG,'capture:'+sid,'CAPTURED_CONDITION_NOT_CURRENT_DAY']
        capture.set_folder_path('DynaCity/Real building replacements/'+sid)
        capture.set_editor_property('is_spatially_loaded',True)
        component=capture.get_component_by_class(unreal.StaticMeshComponent)
        component.set_static_mesh(unreal.load_asset(assets['capture']))
        component.set_material(0,unreal.load_asset(prepared['photo_material']))
        component.set_forced_lod_model(1)
        _disable_visual_collision(capture,component)
        for actor,component,mesh_path in sources:
            label='Captured visual - '+sid+' - '+actor.get_actor_label()
            matches=[a for a in old if a.get_actor_label()==label]
            if len(matches)>1:raise RuntimeError('Duplicate patched visual actors require review')
            visual=matches[0] if matches else subsystem.spawn_actor_from_class(unreal.StaticMeshActor,unreal.Vector())
            if not matches:created.append(visual)
            visual.modify();used.append(visual);visual.set_actor_label(label)
            visual.tags=[TAG,'capture:'+sid,'VISUAL_BATCH_WITH_PLACEHOLDER_OMITTED']
            visual.set_folder_path('DynaCity/Real building replacements/'+sid)
            visual.set_editor_property('is_spatially_loaded',True)
            visual.set_actor_transform(actor.get_actor_transform(),False,True)
            replacement=visual.get_component_by_class(unreal.StaticMeshComponent)
            replacement.set_static_mesh(unreal.load_asset(mesh_path))
            replacement.set_editor_property('override_materials',list(component.get_editor_property('override_materials')))
            _disable_visual_collision(visual,replacement)
            actor.modify();component.modify();component.set_visibility(False,True);changed.append(actor)
        if any(a not in used for a in old):raise RuntimeError('Stale capture actors require an explicit restore before changing replacement scope')
        if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in used+changed}),True):
            raise RuntimeError('Could not save replacement actor packages')
    except Exception:
        for actor in created:subsystem.destroy_actor(actor)
        for actor,component,_ in sources:
            previous=next(r for r in backup['actors'] if r['actor']==actor.get_path_name())
            component.set_visibility(previous['visible'],True)
        raise
    report={'source_id':sid,'manifest':str(filename),'capture_mesh':assets['capture'],
            'actors':[a.get_path_name() for a in used],'new_actors':len(created),'placeholder_source_ids':data['replace_source_ids'],
            'original_collision_and_mesh_preserved':True,'original_visibility':'hidden only for this replaced visual batch',
            'other_visual_geometry':'exact remaining source triangles; see patch index hash',
            'condition':data['source_condition'],'placement_rms_m':data['control_rms_m'],
            'photo_material':prepared['photo_material'],'saved':True,'reload_verified':False}
    report['visual_collision_policy']='NoCollision at actor and component; mesh-default collision disabled'
    (_root()/('captured-building-install-'+sid+'.json')).write_text(json.dumps(report,indent=2))
    return report


def verify(filename):
    import city_world
    _ready();data=_read(filename);sid=data['source_id'];expected=_read(_root()/('captured-building-install-'+sid+'.json'))
    descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    original_labels={r['label'] for r in _read(_root()/('captured-building-backup-'+sid+'.json'))['actors']}
    guids=[d.guid for d in descs if str(d.label).startswith(('Captured building - '+sid,'Captured visual - '+sid)) or str(d.label) in original_labels]
    unreal.WorldPartitionBlueprintLibrary.unload_actors(guids);unreal.WorldPartitionBlueprintLibrary.load_actors(guids)
    actors=[a for a in city_world._actors() if TAG in a.tags and ('capture:'+sid) in a.tags]
    names=[a.get_path_name() for a in actors];capture=[a for a in actors if a.get_actor_label().startswith('Captured building - ')]
    original_ok=True
    for entry in _read(_root()/('captured-building-backup-'+sid+'.json'))['actors']:
        actor=next((a for a in city_world._actors() if a.get_path_name()==entry['actor']),None)
        component=actor.get_component_by_class(unreal.StaticMeshComponent) if actor else None
        original_ok &= bool(component and component.static_mesh.get_path_name()==entry['mesh'] and not component.is_visible())
    photo_ok=bool(len(capture)==1 and capture[0].get_component_by_class(unreal.StaticMeshComponent).get_material(0).get_path_name()==expected.get('photo_material'))
    mesh=capture[0].get_component_by_class(unreal.StaticMeshComponent).static_mesh if len(capture)==1 else None
    triangles=mesh.get_num_triangles(0) if mesh else 0
    collision_ok=bool(actors) and all(_visual_collision_disabled(a) for a in actors)
    result={'source_id':sid,'actors':len(actors),'capture_actors':len(capture),
            'reload_verified':set(names)==set(expected['actors']) and len(capture)==1 and original_ok and photo_ok and collision_ok and triangles==data['export_triangles'],
            'photo_material_verified':photo_ok,'native_triangles':triangles,
            'visual_collision_verified':collision_ok,
            'original_mesh_preserved_and_hidden':original_ok,'condition':data['source_condition']}
    (_root()/('captured-building-verification-'+sid+'.json')).write_text(json.dumps(result,indent=2))
    return result


def preview(source_id):
    """Focus the real capture; in Play, park safely and use the existing fly camera."""
    import city_world,explore_city
    installed=_read(_root()/('captured-building-install-'+source_id+'.json'))
    data=_read(installed['manifest'])
    camera=data['inspection_camera_local_m'];target=data['inspection_target_local_m']
    game=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_game_world()
    if game:
        import location_search,location_core,driving_tools
        # Reuse the existing road/surface/vehicle-clearance checks before parking.
        centre=data['target_ties_utm_m'][0]
        lon,lat=location_core.utm_to_lonlat(*centre)
        previous=location_search._results
        try:
            location_search._results=[{'id':'capture/'+source_id,'name':source_id+' captured-condition preview','lat':lat,'lon':lon}]
            location_search.go_to(0)
        finally:location_search._results=previous
        if not location_search._last_navigation or location_search._last_navigation.get('name')!=source_id+' captured-condition preview':
            raise RuntimeError('No safe nearby road for the captured-building preview')
        markers={}
        for a in unreal.GameplayStatics.get_all_actors_of_class(game,unreal.Actor):
            label=a.get_actor_label()
            for key in ('ORIGIN','EAST','NORTH','UP'):
                if label.startswith('DYNACITY_BASIS_'+key):markers[key]=a.get_actor_location()
        if len(markers)!=4:raise RuntimeError('Missing game coordinate basis')
        origin=markers['ORIGIN'];axes=[(markers[k]-origin)/10 for k in ('EAST','NORTH','UP')]
        def world(p):return origin+axes[0]*p[0]+axes[1]*p[1]+axes[2]*p[2]
        eye=world(camera);look=world(target);rotation=unreal.MathLibrary.make_rot_from_x(look-eye)
        pawn=driving_tools._pawn();pawn.set_fly_mode(True)
        pawn.set_actor_location(eye,False,True)
        pawn.set_fly_inputs(0,0,0,rotation.yaw,rotation.pitch,False);pawn.release_drive_inputs()
        mode='free-flight preview; F returns to the safely parked car'
    else:
        city_world._current();explore_city._look_at(explore_city._world(camera),explore_city._world(target))
        mode='editor preview'
    result={'source_id':source_id,'mode':mode,'condition':data['source_condition']}
    (_root()/('captured-building-preview-'+source_id+'.json')).write_text(json.dumps(result,indent=2))
    return result


@unreal.uclass()
class CapturedBuildingTools(unreal.ToolsetDefinition):
    @toolset_registry.tool_call
    @staticmethod
    def preview_capture(source_id: str) -> str:return json.dumps(preview(source_id))

    @toolset_registry.tool_call
    @staticmethod
    def prepare(filename: str) -> str:return json.dumps(prepare_assets(filename))

    @toolset_registry.tool_call
    @staticmethod
    def install_capture(filename: str) -> str:return json.dumps(install(filename))

    @toolset_registry.tool_call
    @staticmethod
    def verify_capture(filename: str) -> str:return json.dumps(verify(filename))


def register():
    global _registration
    from toolset_registry.registration import Registration
    _registration=Registration([CapturedBuildingTools]);_registration.register()
