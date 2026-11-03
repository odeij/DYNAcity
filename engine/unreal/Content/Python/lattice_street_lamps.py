"""Install the user-approved original lattice lamp at existing placements only."""
import hashlib
import json
from pathlib import Path
import unreal

ROOT = '/Game/Beirut/OriginalAssets/LatticeLamp'


def _material(name, definition):
    path = ROOT+'/M_'+name
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.load_asset(path)
    material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        'M_'+name, ROOT, unreal.Material, unreal.MaterialFactoryNew())
    material.set_editor_property('used_with_instanced_static_meshes', True)
    lib = unreal.MaterialEditingLibrary
    color = lib.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, -300, 0)
    color.set_editor_property('constant', unreal.LinearColor(*definition['color'], 1))
    lib.connect_material_property(color, '', unreal.MaterialProperty.MP_BASE_COLOR)
    for key, prop in (('roughness', unreal.MaterialProperty.MP_ROUGHNESS), ('metallic', unreal.MaterialProperty.MP_METALLIC)):
        scalar = lib.create_material_expression(material, unreal.MaterialExpressionConstant, -300, 100)
        scalar.set_editor_property('r', definition[key])
        lib.connect_material_property(scalar, '', prop)
    lib.recompile_material(material)
    if not unreal.EditorAssetLibrary.save_loaded_asset(material):
        raise RuntimeError('Material save failed: '+name)
    return material


def install(model_file):
    import city_world, library_assets, explore_city, location_core, location_search
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before replacing lamp assets.')
    model_file = Path(model_file)
    data = json.loads(model_file.read_text(encoding='utf-8'))
    digest = hashlib.sha256(Path(data['fbx']).read_bytes()).hexdigest()
    if digest != data['sha256_fbx']:
        raise ValueError('Model bytes differ from reviewed export.')
    name = 'SM_LebaneseLatticeLamp_'+digest[:10]
    path = ROOT+'/'+name
    if not unreal.EditorAssetLibrary.does_asset_exist(path):
        task = unreal.AssetImportTask()
        task.filename = data['fbx']; task.destination_path = ROOT
        task.destination_name = name; task.automated = True; task.save = True
        options = unreal.FbxImportUI()
        options.import_mesh = True; options.import_materials = False; options.import_textures = False
        options.import_as_skeletal = False
        options.static_mesh_import_data.combine_meshes = True
        options.static_mesh_import_data.auto_generate_collision = False
        options.static_mesh_import_data.generate_lightmap_u_vs = False
        task.options = options
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    mesh = unreal.load_asset(path)
    if mesh is None:
        raise RuntimeError('Lattice mesh import failed.')
    size = mesh.get_bounding_box().max-mesh.get_bounding_box().min
    if any(abs(value-expected) > 1 for value, expected in zip((size.x,size.y,size.z),data['dimensions_cm'])):
        raise ValueError('Lattice mesh scale does not match centimetre export.')
    for index, slot in enumerate(mesh.get_editor_property('static_materials')):
        key = str(slot.material_slot_name)
        mesh.set_material(index, _material(key, data['materials'][key]))
    if not unreal.EditorAssetLibrary.save_loaded_asset(mesh):
        raise RuntimeError('Could not save lattice mesh materials.')
    active_path = city_world._root()/'Saved/DynaCity/library-assets-active.json'
    active = library_assets.active()
    old_mesh = unreal.load_asset(active['street']['street_lamp']['mesh'])
    descriptors = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    selected = [d.guid for d in descriptors if str(d.label).startswith(
        ('Reference preview - CC0 lamps', 'Reference preview - lattice lamps', 'Reference lamp illumination', 'Mapped lamp illumination '))
        or (str(d.label).startswith('Street_') and str(d.label).endswith('_street_lamp'))]
    unreal.WorldPartitionBlueprintLibrary.load_actors(selected)
    actors = city_world._actors()
    lights = [a for a in actors if isinstance(a,unreal.PointLight) and any(
        str(t) in {'DYNACITY_PILOT_LIGHT','DYNACITY_STREET_LIGHT'} for t in a.tags)]
    groups = []
    for actor in actors:
        for component in actor.get_components_by_class(unreal.HierarchicalInstancedStaticMeshComponent):
            if component.static_mesh == old_mesh:
                groups.append((actor, component))
    if sum(c.get_instance_count() for _,c in groups) != 10 or len(lights) != 10:
        raise RuntimeError('Expected ten existing lamp instances and ten lights; inspect changed scene before installing.')
    if old_mesh == mesh:
        raise RuntimeError('Lattice lamp is already active; inspect instead of reinstalling.')
    roads = location_search._data()
    plans = []; remaining = list(lights)
    for actor, component in groups:
        source_ids = list(actor.get_editor_property('source_ids'))
        if len(source_ids) != component.get_instance_count():
            raise RuntimeError('Lamp source IDs no longer match instance count.')
        for index in range(component.get_instance_count()):
            original = component.get_instance_transform(index, world_space=True)
            position = original.translation
            light = min(remaining, key=lambda a:(a.get_actor_location().x-position.x)**2+(a.get_actor_location().y-position.y)**2)
            lp = light.get_actor_location()
            if (lp.x-position.x)**2+(lp.y-position.y)**2 > 200**2:
                raise RuntimeError('No unique light within 2 m of existing pole base.')
            remaining.remove(light)
            local = explore_city._local(position)
            near = location_core.nearest_roads(roads, local, max_distance=20, limit=1)
            if not near:
                raise RuntimeError('Cannot orient the fixture without a nearby road.')
            road = near[0]['position']
            target = explore_city._world([road[0],road[1],local[2]])
            direction = target-position; direction.z = 0
            rotation = unreal.MathLibrary.make_rot_from_x(direction) if direction.length() > 10 else original.rotation.rotator()
            transform = unreal.Transform(position, rotation, original.scale3d)
            plans.append((actor,component,index,source_ids[index],original,transform,light))
    backup = city_world._root()/'Saved/DynaCity/lattice-lamp-before-20261031.json'
    if not backup.exists():
        backup.write_text(json.dumps({'active_record':active['street']['street_lamp'],
            'instances':[{'actor':a.get_path_name(),'index':i,'source_id':str(s),
                'transform':str(old),'light':l.get_path_name(),'light_position':str(l.get_actor_location())}
                for a,c,i,s,old,new,l in plans]},indent=2),encoding='utf-8')
    packages = []; records = []
    for actor, component in groups:
        actor.modify(); component.modify()
        component.set_editor_property('override_materials', [])
        actor.set_street_mesh(mesh)
        if actor.get_actor_label() == 'Reference preview - CC0 lamps':
            actor.set_actor_label('Reference preview - lattice lamps')
        packages.append(actor.get_outermost())
    for actor,component,index,source_id,old,transform,light in plans:
        component.update_instance_transform(index,transform,world_space=True,mark_render_state_dirty=True,teleport=True)
        # Explicit rotated local anchor, rather than the obsolete 3.45 m offset.
        anchor = unreal.MathLibrary.transform_location(transform,unreal.Vector(*data['light_anchor_cm']))
        light.modify(); light.set_actor_location(anchor,False,False)
        packages.append(light.get_outermost())
        records.append({'source_id':str(source_id),'base_position_cm':[transform.translation.x,transform.translation.y,transform.translation.z],
                        'head_light_cm':[anchor.x,anchor.y,anchor.z], 'light_actor':light.get_path_name(),
                        'base_displacement_cm':(old.translation-transform.translation).length()})
    if not unreal.EditorLoadingAndSavingUtils.save_packages(list(set(packages)),True):
        raise RuntimeError('Lamp actor package save failed.')
    active['street']['street_lamp'] = {'mesh':mesh.get_path_name(),'model_manifest':str(model_file),
        'materials':{str(s.material_slot_name):s.material_interface.get_path_name() for s in mesh.get_editor_property('static_materials')},
        'dimensions_cm':data['dimensions_cm'],'source_triangles':data['triangles'],
        'light_anchor_cm':data['light_anchor_cm'],'facing':data['facing'],
        'provenance':{'authorship':data['authorship'],'reference':data['reference'],'illustrative':True}}
    temporary=active_path.with_suffix('.partial'); temporary.write_text(json.dumps(active,indent=2),encoding='utf-8');temporary.replace(active_path)
    report={'mesh':mesh.get_path_name(),'instances':records,'count':len(records),'dimensions_cm':[size.x,size.y,size.z],
            'triangles':data['triangles'],'light_color_changed':False,'base_positions_preserved':True,
            'limitations':data['limitations'],'saved':True,'rendered_verified':False}
    (city_world._root()/'Saved/DynaCity/lattice-lamp-installed-20261031.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
