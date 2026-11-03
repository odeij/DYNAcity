"""Agent 2: mapped OSM traffic islands as raised surface patches built offline from traced road heights. Additive."""
import json
import re
from pathlib import Path
import unreal

TAG = 'DYNACITY_AGENT2_TRAFFIC_ISLAND'
ASSET_DIR = '/Game/Beirut/Agent2/TrafficIslands'


def _material():
    path = ASSET_DIR + '/M_MappedTrafficIsland'
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.load_asset(path)
    lib = unreal.MaterialEditingLibrary
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset('M_MappedTrafficIsland', ASSET_DIR, unreal.Material, unreal.MaterialFactoryNew())
    mat.set_editor_property('two_sided', True)
    node = lib.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -400, 0)
    node.set_editor_property('constant', unreal.LinearColor(.46, .45, .42, 1))
    lib.connect_material_property(node, '', unreal.MaterialProperty.MP_BASE_COLOR)
    r = lib.create_material_expression(mat, unreal.MaterialExpressionConstant, -400, 200)
    r.set_editor_property('r', .85)
    lib.connect_material_property(r, '', unreal.MaterialProperty.MP_ROUGHNESS)
    lib.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def apply(filename):
    import city_world
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play first')
    rows = json.loads(Path(filename).read_text(encoding='utf8'))
    city_world.load_editor_geometry()
    if any(TAG in a.tags for a in city_world._actors()):
        raise RuntimeError('Already imported')
    material = _material()
    sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    made = []
    report = []
    try:
        for row in rows:
            if 'fbx' not in row:
                continue
            num = re.sub('[^0-9]', '', row['source_id'])
            name = 'SM_TrafficIsland_' + num
            path = ASSET_DIR + '/' + name
            if not unreal.EditorAssetLibrary.does_asset_exist(path):
                task = unreal.AssetImportTask()
                task.filename = row['fbx']
                task.destination_path = ASSET_DIR
                task.destination_name = name
                task.automated = True
                task.save = True
                opt = unreal.FbxImportUI()
                opt.import_mesh = True
                opt.import_materials = False
                opt.import_textures = False
                opt.import_as_skeletal = False
                opt.static_mesh_import_data.combine_meshes = True
                opt.static_mesh_import_data.generate_lightmap_u_vs = False
                opt.static_mesh_import_data.auto_generate_collision = False
                task.options = opt
                unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
            mesh = unreal.load_asset(path)
            if mesh is None:
                raise RuntimeError('Island mesh import failed ' + name)
            mesh.set_material(0, material)
            unreal.EditorAssetLibrary.save_loaded_asset(mesh)
            loc = unreal.Vector(row['centre_world_cm_xy'][0], row['centre_world_cm_xy'][1], row['centre_z_cm'])
            actor = sub.spawn_actor_from_class(unreal.StaticMeshActor, loc)
            made.append(actor)
            actor.set_actor_label('Street_TrafficIsland_' + num)
            actor.tags = [TAG, 'osm:' + row['source_id']]
            actor.set_folder_path('DynaCity/Agent2 mapped traffic islands')
            actor.set_editor_property('is_spatially_loaded', True)
            comp = actor.static_mesh_component
            comp.set_static_mesh(mesh)
            comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            comp.set_cast_shadow(False)
            b = mesh.get_bounding_box()
            report.append({'source_id': row['source_id'], 'actor': actor.get_path_name(), 'label': actor.get_actor_label(),
                'mesh': path, 'triangles': row['triangles'], 'surface_hits': row['surface_hits'], 'vertices': row['vertices'],
                'bounds_cm': [b.max.x - b.min.x, b.max.y - b.min.y, b.max.z - b.min.z], 'geometry_kind': row['geometry_kind'],
                'surface_actors': row['surface_actors'], 'fbx_sha256': row['fbx_sha256']})
        if not report:
            raise RuntimeError('Nothing to import')
        if not unreal.EditorLoadingAndSavingUtils.save_packages(list({a.get_outermost() for a in made}), True):
            raise RuntimeError('Save failed')
    except Exception:
        for a in made:
            sub.destroy_actor(a)
        raise
    out = {'schema': 'dynacity-agent2-traffic-island-import/1', 'manifest': str(filename), 'actors': report,
           'skipped': [r for r in rows if 'fbx' not in r]}
    (city_world._root() / 'Saved/DynaCity/agent2-traffic-island-import.json').write_text(json.dumps(out, indent=2), encoding='utf8')
    return out
