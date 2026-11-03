"""Install source-bound CC0 facade instances; root owns serialized editor use."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import unreal

TAG='DYNACITY_FACADE_MODULE_PILOT'
ROOT='/Game/DynaCity/FacadeModules'


def _building_signature():
    import city_world
    result=[]
    for actor in city_world._actors():
        if not actor.get_actor_label().startswith('buildings_'):continue
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        if component is None:continue
        mesh=component.get_editor_property('static_mesh')
        loc,rot,scale=actor.get_actor_location(),actor.get_actor_rotation(),actor.get_actor_scale3d()
        result.append({'actor':actor.get_path_name(),'mesh':mesh.get_path_name() if mesh else None,
            'transform':[loc.x,loc.y,loc.z,rot.pitch,rot.yaw,rot.roll,scale.x,scale.y,scale.z],
            'collision':str(component.get_collision_enabled()),
            'collision_profile':str(component.get_collision_profile_name())})
    return sorted(result,key=lambda item:item['actor'])


def _outward_sign(record,mesh):
    box=mesh.get_bounding_box();low,high=[100*b[1] for b in record['bounds_m']]
    actual=[box.min.y,box.max.y]
    same=max(abs(a-b) for a,b in zip(actual,[low,high]))<=.1
    flipped=max(abs(a-b) for a,b in zip(actual,[-high,-low]))<=.1
    if same==flipped:raise ValueError('Ambiguous facade native Y basis '+str(actual))
    return 1 if same else -1


def apply(filename):
    import city_world, explore_city, library_assets
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before facade import')
    path=Path(filename);layout=json.loads(path.read_text())
    bounds=layout.get('fitting',{}).get('positive_scale_bounds',[1.,1.])
    for item in layout['placements']:
        scale=item.get('scale',[1.,1.,1.])
        if len(scale)!=3 or any(not math.isfinite(v) or v<=0 or not bounds[0]-.000001<=v<=bounds[1]+.000001 for v in scale):
            raise ValueError('Invalid or unapproved facade fitting scale')
    module_file=Path(layout['module_manifest']);modules=json.loads(module_file.read_text())
    if modules['source']['license']!='CC0 1.0':raise ValueError('Unexpected licence')
    if hashlib.sha256(Path(layout['source_asset']).read_bytes()).hexdigest()!=layout['source_sha256']:
        raise ValueError('Source city geometry changed')
    index=Path(layout['source_asset']).with_suffix('.objects.json')
    if hashlib.sha256(index.read_bytes()).hexdigest()!=layout['source_index_sha256']:
        raise ValueError('Source building identity index changed')
    for record in modules['source']['files']:
        if hashlib.sha256(Path(record['path']).read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError('Changed library source bytes')
    origin=json.loads(city_world._checkpoint().read_text())['origin_utm_m']
    if origin!=layout['coordinates']['origin_utm_m']:raise ValueError('Facade/city origins differ')
    scope_config={'source':layout['source_sha256'],'bounds':layout['bounds_m'],
        'selection':layout.get('selection'),'budget':layout['budget'],
        'modules':hashlib.sha256(module_file.read_bytes()).hexdigest()}
    scope_hash=hashlib.sha256(json.dumps(scope_config,sort_keys=True,
        separators=(',',':')).encode()).hexdigest()[:12]
    scope_tag=TAG+'_SCOPE_'+scope_hash
    existing=[]
    for actor in city_world._actors():
        if TAG not in actor.tags:continue
        same_manifest=Path(str(actor.get_editor_property('source_manifest'))).resolve()==path.resolve()
        if scope_tag in actor.tags or same_manifest:existing.append(actor)
    if existing:raise RuntimeError('This facade scope is already installed; inspect its report before replacing')
    existing_ids=set()
    for actor in city_world._actors():
        if TAG in actor.tags:
            existing_ids.update(str(value) for value in actor.get_editor_property('source_ids'))
    if existing_ids & {item['id'] for item in layout['placements']}:
        raise RuntimeError('New facade scope duplicates already installed source placements')
    source=json.loads(Path(modules['source']['gltf']).read_text())
    material_data={'textures':source['textures'],
        'images':[str(Path(modules['source']['gltf']).parent / image['uri']) for image in source['images']]}
    definitions={material['name']:material for material in source['materials']}
    meshes={};imports=[];outward_signs={}
    for record in modules['modules']:
        if hashlib.sha256(Path(record['fbx']).read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError('Changed facade export')
        name='Facade_'+record['id']+'_'+record['sha256'][:10];asset=ROOT+'/Meshes/'+name
        if not unreal.EditorAssetLibrary.does_asset_exist(asset):
            task=unreal.AssetImportTask();task.filename=record['fbx'];task.destination_path=ROOT+'/Meshes'
            task.destination_name=name;task.automated=True;task.save=True
            options=unreal.FbxImportUI();options.import_mesh=True;options.import_materials=False
            options.import_textures=False;options.import_as_skeletal=False
            options.static_mesh_import_data.combine_meshes=True
            options.static_mesh_import_data.generate_lightmap_u_vs=False
            options.static_mesh_import_data.auto_generate_collision=False
            task.options=options;unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        mesh=unreal.load_asset(asset)
        if mesh is None:raise RuntimeError('Facade module import failed')
        size=mesh.get_bounding_box().max-mesh.get_bounding_box().min
        expected=[100*(b-a) for a,b in zip(*record['bounds_m'])]
        if any(abs(a-b)>max(1,b*.01) for a,b in zip([size.x,size.y,size.z],expected)):
            raise ValueError('Unexpected facade import axes or scale: '+str(size))
        for index,slot in enumerate(mesh.get_editor_property('static_materials')):
            slot_name=str(slot.material_slot_name)
            if slot_name not in definitions:raise ValueError('Unknown authored material '+slot_name)
            mat=library_assets._material(material_data,definitions[slot_name],
                'FacadeKit_'+hashlib.sha256(slot_name.encode()).hexdigest()[:10])
            mesh.set_material(index,mat)
        unreal.EditorAssetLibrary.save_loaded_asset(mesh);meshes[record['id']]=mesh
        outward_signs[record['id']]=_outward_sign(record,mesh)
        box=mesh.get_bounding_box()
        imports.append({'module':record['id'],'asset':mesh.get_path_name(),'dimensions_cm':[size.x,size.y,size.z],
            'signed_bounds_cm':[[box.min.x,box.min.y,box.min.z],[box.max.x,box.max.y,box.max.z]],
            'native_outward_y_sign':outward_signs[record['id']]})
    groups=defaultdict(list)
    for item in layout['placements']:groups[(item['tile'],item['module'])].append(item)
    cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances')
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem);made=[];rows=[]
    before=_building_signature()
    try:
        for (tile,module),items in sorted(groups.items()):
            actor=subsystem.spawn_actor_from_class(cls,unreal.Vector());made.append(actor)
            actor.set_actor_label('FacadePilot_'+scope_hash+'_'+tile.replace(':','_')+'_'+module)
            actor.tags=[TAG,scope_tag,'ILLUSTRATIVE_ARCHITECTURE'];actor.set_folder_path('DynaCity/Facade pilot/'+scope_hash)
            actor.set_editor_property('layer_kind','facade_module')
            actor.set_editor_property('production_area',tile)
            actor.set_editor_property('source_manifest',str(path))
            actor.set_editor_property('is_spatially_loaded',True)
            actor.set_street_mesh(meshes[module]);actor.set_street_collision(False)
            actor.set_street_cull_distance(12000,int(layout['budget']['view_distance_m']*100))
            for item in items:
                # A source-east/north to native-east/south mapping reflects Y.
                # Determine whether FBX already reflected the asset's outward Y
                # from its asymmetric recess bounds; rotate properly, never mirror.
                yaw=-item['yaw_deg']+(180 if outward_signs[module]==1 else 0)
                transform=unreal.Transform(location=explore_city._world(item['position_m']),
                    rotation=unreal.Rotator(pitch=0,yaw=yaw,roll=0),scale=unreal.Vector(*item.get('scale',[1,1,1])))
                if actor.add_street_instance(transform,item['id'])<0:raise RuntimeError('Facade instance failed')
            if actor.get_street_instance_count()!=len(items):raise RuntimeError('Facade count mismatch')
            rows.append({'actor':actor.get_path_name(),'tile':tile,'module':module,'instances':len(items),
                'source_ids':[item['id'] for item in items]})
        if before!=_building_signature():raise RuntimeError('Source building geometry/collision changed')
        packages=[a.get_package() for a in made]
        if not unreal.EditorLoadingAndSavingUtils.save_packages(packages,True):raise RuntimeError('Facade scoped save failed')
    except Exception:
        for actor in made:subsystem.destroy_actor(actor)
        raise
    report={'schema':'dynacity-facade-pilot-install/1','status':'saved','manifest':str(path),
        'scope_tag':scope_tag,'scope_hash':scope_hash,
        'actors':rows,'imports':imports,'instances':len(layout['placements']),
        'source_building_signature_before':before,'source_buildings_unchanged':True,'reload_verified':False,
        'limits':layout['limits'],'collision':'disabled on facade additions'}
    (path.parent/'facade-install.json').write_text(json.dumps(report,indent=2))
    return report


def repair_orientation(filename):
    """Correct the diagnosed positional-Rotator constructor issue in this pilot."""
    import explore_city
    path=Path(filename);layout=json.loads(path.read_text())
    report_path=path.parent/'facade-install.json';report=json.loads(report_path.read_text())
    errors=report.get('verification',{}).get('errors',[])
    if not errors or any(not value.startswith('Orientation mismatch ') for value in errors):
        raise ValueError('Repair only applies to the measured orientation-only failure')
    expected={item['id']:item for item in layout['placements']}
    imports={item['module']:item for item in report['imports']}
    expected_sources=report['source_building_signature_before']
    expected_paths={item['actor'] for item in expected_sources}
    actual_sources=[item for item in _building_signature() if item['actor'] in expected_paths]
    if actual_sources!=expected_sources:
        raise RuntimeError('Source building state changed before orientation repair')
    packages=[];corrected=0
    for group in report['actors']:
        actor=unreal.load_object(None,group['actor'])
        if actor is None:raise RuntimeError('Missing facade actor during repair')
        component=actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        ids=[str(value) for value in actor.get_editor_property('source_ids')]
        if ids!=group['source_ids'] or component.get_instance_count()!=len(ids):
            raise RuntimeError('Facade source identities/count changed')
        actor.modify();component.modify()
        sign=imports[group['module']]['native_outward_y_sign']
        for index,identity in enumerate(ids):
            item=expected[identity];yaw=-item['yaw_deg']+(180 if sign==1 else 0)
            transform=unreal.Transform(location=explore_city._world(item['position_m']),
                rotation=unreal.Rotator(pitch=0,yaw=yaw,roll=0),scale=unreal.Vector(1,1,1))
            if not component.update_instance_transform(index,transform,world_space=True,
                    mark_render_state_dirty=index==len(ids)-1,teleport=True):
                raise RuntimeError('Facade orientation update failed')
            corrected+=1
        packages.append(actor.get_package())
    if not unreal.EditorLoadingAndSavingUtils.save_packages(packages,True):
        raise RuntimeError('Facade orientation scoped save failed')
    report['orientation_repair']={'reason':'UE positional Rotator constructor mapped angle into pitch',
        'fix':'Named pitch/yaw/roll constructor arguments','instances_corrected':corrected,
        'saved_packages':[package.get_path_name() for package in packages]}
    report_path.write_text(json.dumps(report,indent=2))
    return corrected


def verify(filename):
    """Read back mesh, identities, transforms, source buildings and collision."""
    import city_world,explore_city
    path=Path(filename);layout=json.loads(path.read_text())
    report_file=path.parent/'facade-install.json';report=json.loads(report_file.read_text())
    expected={item['id']:item for item in layout['placements']}
    modules={item['module']:item for item in report['imports']}
    seen=set();errors=[];actual_count=0;rotation_diagnostics=[];position_errors=[]
    for group in report['actors']:
        actor=unreal.load_object(None,group['actor'])
        if actor is None:errors.append('Missing actor '+group['actor']);continue
        component=actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        if component is None:errors.append('Missing HISM '+group['actor']);continue
        ids=[str(value) for value in actor.get_editor_property('source_ids')]
        if ids!=group['source_ids']:errors.append('Source identity ordering changed '+group['actor'])
        if component.get_instance_count()!=len(ids):errors.append('Instance count mismatch')
        if component.get_editor_property('static_mesh').get_path_name()!=modules[group['module']]['asset']:
            errors.append('Module mesh changed')
        if component.get_collision_enabled()!=unreal.CollisionEnabled.NO_COLLISION:
            errors.append('Facade collision enabled')
        if not component.is_visible() or component.get_editor_property('hidden_in_game'):
            errors.append('Facade hidden')
        sign=modules[group['module']]['native_outward_y_sign']
        for index,identity in enumerate(ids):
            if identity in seen or identity not in expected:
                errors.append('Duplicate or unknown facade identity');continue
            seen.add(identity);item=expected[identity]
            transform=component.get_instance_transform(index,world_space=True)
            target=explore_city._world(item['position_m'])
            position_error=(transform.translation-target).length();position_errors.append(position_error)
            if position_error>.1:errors.append('Position mismatch '+identity)
            desired=-item['yaw_deg']+(180 if sign==1 else 0)
            actual=transform.rotation.rotator()
            yaw_error=abs((actual.yaw-desired+180)%360-180)
            rotation_diagnostics.append({'id':identity,'expected_yaw':desired,'actual_yaw':actual.yaw,
                'yaw_error_deg':yaw_error,'pitch_deg':actual.pitch,'roll_deg':actual.roll,
                'quaternion':[transform.rotation.x,transform.rotation.y,transform.rotation.z,transform.rotation.w],
                'scale':[transform.scale3d.x,transform.scale3d.y,transform.scale3d.z]})
            pitch_error=abs((actual.pitch+180)%360-180)
            roll_error=abs((actual.roll+180)%360-180)
            if max(yaw_error,pitch_error,roll_error)>.01:
                errors.append('Orientation mismatch '+identity)
            desired_scale=item.get('scale',[1,1,1])
            if any(target<=0 or abs(value-target)>.0001 for value,target in zip(
                    (transform.scale3d.x,transform.scale3d.y,transform.scale3d.z),desired_scale)):
                errors.append('Incorrect or mirrored facade scale '+identity)
            actual_count+=1
    if seen!=set(expected):errors.append('Expected facade identities missing')
    expected_sources=report['source_building_signature_before']
    expected_paths={item['actor'] for item in expected_sources}
    actual_sources=[item for item in _building_signature() if item['actor'] in expected_paths]
    if actual_sources!=expected_sources:
        errors.append('Source building mesh/transform/collision signature differs')
    report.update(verification={'status':'pass' if not errors else 'fail','errors':errors,
        'instances':actual_count,'identities_match':seen==set(expected),
        'position_error_max_cm':max(position_errors,default=0),
        'yaw_error_max_deg':max((item['yaw_error_deg'] for item in rotation_diagnostics),default=0),
        'rotation_diagnostics':sorted(rotation_diagnostics,key=lambda item:item['yaw_error_deg'],reverse=True),
        'source_buildings_unchanged':actual_sources==expected_sources})
    report_file.write_text(json.dumps(report,indent=2))
    if errors:raise RuntimeError('Facade verification failed: '+str(errors[:10]))
    return report['verification']
