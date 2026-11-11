"""Sea surface, Corniche seawall/railing and kerbside parked cars for the Beirut city map.

Placements come from `scripts/prepare_sea_street_life.py` (illustrative, not surveyed). Everything is
tagged and replaceable: re-running an install clears and rebuilds its own actors only.
"""
import json
import math
from pathlib import Path
import unreal

TAG = 'DYNACITY_SEA_STREET_LIFE'
ROOT = '/Game/DynaCity/SeaStreetLife'
DATA = Path('C:/private/dynacity-data/engine/sea-coast')
FOLDER = 'DynaCity/Sea and street life'
CUBE = '/Engine/BasicShapes/Cube.Cube'
PLANE = '/Engine/BasicShapes/Plane.Plane'
# Body-colour slots of the installed traffic models (BMW E36, Toyota KE80, delivery van).
PAINT_SLOTS = ('CarPaint', 'MainColor_001', 'body', 'white')


def _subsystem():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def _owned(label):
    for actor in _subsystem().get_all_level_actors():
        if actor.get_actor_label() == label and TAG in [str(t) for t in actor.tags]:
            return actor
    return None


def _spawn(cls, label):
    actor = _owned(label) or _subsystem().spawn_actor_from_class(cls, unreal.Vector())
    actor.modify()
    actor.set_actor_label(label)
    actor.set_folder_path(FOLDER)
    actor.tags = [TAG]
    return actor


def _material(name, build):
    path = f'{ROOT}/{name}'
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset(name, ROOT, unreal.Material, unreal.MaterialFactoryNew())
    build(material, unreal.MaterialEditingLibrary)
    unreal.MaterialEditingLibrary.recompile_material(material)
    unreal.EditorAssetLibrary.save_loaded_asset(material)
    return material


def _constant(lib, material, value, x, y):
    if isinstance(value, (int, float)):
        node = lib.create_material_expression(material, unreal.MaterialExpressionConstant, x, y)
        node.set_editor_property('r', value)
    else:
        node = lib.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, x, y)
        node.set_editor_property('constant', unreal.LinearColor(*value, 1))
    return node


def _flat(name, color, roughness, metallic=0.):
    def build(material, lib):
        lib.connect_material_property(_constant(lib, material, color, -400, 0), '', unreal.MaterialProperty.MP_BASE_COLOR)
        lib.connect_material_property(_constant(lib, material, roughness, -400, 150), '', unreal.MaterialProperty.MP_ROUGHNESS)
        lib.connect_material_property(_constant(lib, material, metallic, -400, 300), '', unreal.MaterialProperty.MP_METALLIC)
    return _material(name, build)


def _normal_texture():
    task = unreal.AssetImportTask()
    task.filename = str(DATA / 'water-normal.png')
    task.destination_path = ROOT
    task.destination_name = 'T_DynaSea_N'
    task.replace_existing = True
    task.automated = True
    task.save = False
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    texture = unreal.load_asset(f'{ROOT}/T_DynaSea_N')
    texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_NORMALMAP)
    texture.set_editor_property('srgb', False)
    unreal.EditorAssetLibrary.save_loaded_asset(texture)
    return texture


def _sea_material(texture):
    def build(material, lib):
        # World-space UVs so one huge plane tiles evenly; two normal layers panning in different
        # directions and scales give moving swell plus ripples. Fresnel blends deep to sky-lit colour.
        world = lib.create_material_expression(material, unreal.MaterialExpressionWorldPosition, -1400, 0)
        mask = lib.create_material_expression(material, unreal.MaterialExpressionComponentMask, -1200, 0)
        mask.set_editor_property('r', True); mask.set_editor_property('g', True)
        lib.connect_material_expressions(world, '', mask, '')
        layers = []
        for i, (scale, speed) in enumerate([(2400., (0.012, 0.006)), (700., (-0.02, 0.015))]):
            div = lib.create_material_expression(material, unreal.MaterialExpressionDivide, -1000, i * 250)
            div.set_editor_property('const_b', scale)
            lib.connect_material_expressions(mask, '', div, 'A')
            pan = lib.create_material_expression(material, unreal.MaterialExpressionPanner, -800, i * 250)
            pan.set_editor_property('speed_x', speed[0]); pan.set_editor_property('speed_y', speed[1])
            lib.connect_material_expressions(div, '', pan, 'Coordinate')
            sample = lib.create_material_expression(material, unreal.MaterialExpressionTextureSample, -600, i * 250)
            sample.set_editor_property('texture', texture)
            sample.set_editor_property('sampler_type', unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
            lib.connect_material_expressions(pan, '', sample, 'UVs')
            layers.append(sample)
        add = lib.create_material_expression(material, unreal.MaterialExpressionAdd, -350, 120)
        lib.connect_material_expressions(layers[0], 'RGB', add, 'A')
        lib.connect_material_expressions(layers[1], 'RGB', add, 'B')
        norm = lib.create_material_expression(material, unreal.MaterialExpressionNormalize, -200, 120)
        lib.connect_material_expressions(add, '', norm, '')
        lib.connect_material_property(norm, '', unreal.MaterialProperty.MP_NORMAL)
        fresnel = lib.create_material_expression(material, unreal.MaterialExpressionFresnel, -400, -300)
        fresnel.set_editor_property('exponent', 4.0)
        lerp = lib.create_material_expression(material, unreal.MaterialExpressionLinearInterpolate, -200, -300)
        lib.connect_material_expressions(_constant(lib, material, (0.006, 0.045, 0.085), -400, -450), '', lerp, 'A')
        lib.connect_material_expressions(_constant(lib, material, (0.05, 0.15, 0.24), -400, -380), '', lerp, 'B')
        lib.connect_material_expressions(fresnel, '', lerp, 'Alpha')
        lib.connect_material_property(lerp, '', unreal.MaterialProperty.MP_BASE_COLOR)
        # Faint self-lit tint: the project's sky light has no lower hemisphere, so unlit water read as black.
        glow = lib.create_material_expression(material, unreal.MaterialExpressionMultiply, -100, -200)
        glow.set_editor_property('const_b', 0.12)
        lib.connect_material_expressions(lerp, '', glow, 'A')
        lib.connect_material_property(glow, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        lib.connect_material_property(_constant(lib, material, 0.08, -200, 300), '', unreal.MaterialProperty.MP_ROUGHNESS)
        lib.connect_material_property(_constant(lib, material, 0.6, -200, 380), '', unreal.MaterialProperty.MP_SPECULAR)
    return _material('M_DynaSea', build)


def install_sea(sea_z=None):
    import explore_city
    data = json.loads((DATA / 'corniche.json').read_text(encoding='utf-8'))
    z = data['sea_z'] if sea_z is None else sea_z
    material = _sea_material(_normal_texture())
    actor = _spawn(unreal.StaticMeshActor, 'DynaCity_Sea')
    component = actor.static_mesh_component
    component.set_static_mesh(unreal.load_asset(PLANE))
    component.set_material(0, material)
    component.set_editor_property('cast_shadow', False)
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    actor.set_editor_property('is_spatially_loaded', False)
    # Centred on the city and well past the horizon from any street or aerial view.
    actor.set_actor_location(explore_city._world([700, 700, z]), False, False)
    actor.set_actor_scale3d(unreal.Vector(60000, 60000, 1))   # the plane is 1 m: 60 km square
    return {'actor': actor.get_actor_label(), 'sea_z_local_m': z}


def _instances(label, mesh, material, transforms, cull=(30000, 60000), paint=None):
    cls = unreal.load_class(None, '/Script/DynaCityDrive.DynaStreetInstances')
    actor = _spawn(cls, label)
    actor.set_editor_property('is_spatially_loaded', False)
    actor.set_street_mesh(unreal.load_asset(mesh) if isinstance(mesh, str) else mesh)
    actor.clear_street_instances()
    actor.set_street_collision(False)
    actor.set_street_cull_distance(*cull)
    for i, transform in enumerate(transforms):
        actor.add_street_instance(transform, f'{label}:{i}')
    for component in actor.get_components_by_class(unreal.InstancedStaticMeshComponent):
        if material is not None:
            component.set_material(0, material)
        if paint is not None:   # body-paint slots only; glass, trim and tyres keep their materials
            for slot in PAINT_SLOTS:
                index = component.get_material_index(slot)
                if index >= 0:
                    component.set_material(index, paint)
    return actor.get_street_instance_count()


def _rotation(heading_degrees):
    import explore_city
    _, east, north, _ = explore_city._basis()
    h = math.radians(heading_degrees)
    return unreal.MathLibrary.make_rot_from_x(east * math.cos(h) + north * math.sin(h))


def install_corniche():
    import explore_city
    data = json.loads((DATA / 'corniche.json').read_text(encoding='utf-8'))
    concrete = _flat('M_DynaCornicheConcrete', (0.42, 0.40, 0.37), 0.85)
    railing = _flat('M_DynaCornicheRailing', (0.08, 0.20, 0.22), 0.45, 0.6)
    walls, rails, posts = [], [], []
    for w in data['walls']:
        rot = _rotation(w['heading_degrees'])
        walls.append(unreal.Transform(explore_city._world(w['center']), rot,
                                      unreal.Vector(w['length_m'], 1.2, w['height_m'])))
        c = w['center']
        rails.append(unreal.Transform(explore_city._world([c[0], c[1], w['top_z'] + 1.05]), rot,
                                      unreal.Vector(w['length_m'], .06, .06)))
        rails.append(unreal.Transform(explore_city._world([c[0], c[1], w['top_z'] + .55]), rot,
                                      unreal.Vector(w['length_m'], .04, .04)))
    for p in data['railing_posts']:
        x, y, top = p['position']
        posts.append(unreal.Transform(explore_city._world([x, y, top + .55]), _rotation(p['heading_degrees']),
                                      unreal.Vector(.06, .06, 1.1)))
    return {'walls': _instances('Corniche_Seawall', CUBE, concrete, walls),
            'rails': _instances('Corniche_Rails', CUBE, railing, rails, (15000, 30000)),
            'posts': _instances('Corniche_Posts', CUBE, railing, posts, (12000, 25000))}


def install_parked_cars():
    import explore_city
    data = json.loads((DATA / 'parked-cars.json').read_text(encoding='utf-8'))
    active = json.loads((Path(unreal.Paths.project_saved_dir()) / 'DynaCity' / 'library-assets-active.json').read_text())
    meshes = {'bmw': active['traffic_vehicle_assets']['bmw-e36-m3']['mesh'],
              'toyota': active['traffic_vehicle_assets']['toyota-corolla']['mesh'],
              'van': active['traffic_vehicle_assets']['delivery_van_innerscene']['mesh']}
    paints = ['Silver', 'Ivory', 'Charcoal', 'Blue', 'Red', 'Sand']
    weights = [0.30, 0.26, 0.20, 0.10, 0.07, 0.07]   # illustrative mix, not a Beirut survey
    # Keep the car spawn points clear: the player's car appears at each drive start.
    starts = [a.get_actor_location() for a in _subsystem().get_all_level_actors()
              if a.get_actor_label().startswith('Drive start')]
    groups = {}
    for i, slot in enumerate(data['slots']):
        where = explore_city._world(slot['position'])
        if any((where - start).length() < 1500 for start in starts):
            continue
        u = ((i * 2654435761) % 2 ** 32) / 2 ** 32
        paint = next(p for p, c in zip(paints, [sum(weights[:k + 1]) for k in range(len(weights))]) if u <= c)
        groups.setdefault((slot['model'], paint), []).append(unreal.Transform(
            where, _rotation(slot['heading_degrees']), unreal.Vector(1, 1, 1)))
    for old in [a for a in _subsystem().get_all_level_actors() if TAG in [str(t) for t in a.tags]
                and a.get_actor_label() in ('Parked_bmw', 'Parked_toyota', 'Parked_van')]:
        _subsystem().destroy_actor(old)   # earlier single-paint layout
    return {f'{m}_{p}': _instances(f'Parked_{m}_{p}', meshes[m], None, v, (9000, 16000),
                                   unreal.load_asset(f'/Game/Beirut/Prototype/M_VehiclePaint{p}'))
            for (m, p), v in groups.items()}


def install_all():
    result = {'sea': install_sea(), 'corniche': install_corniche(), 'parked': install_parked_cars()}
    (DATA / 'install-report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result
