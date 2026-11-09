"""Yellow painted lamp metal; keep the original light color and optics."""
import json
from pathlib import Path
import shutil
import unreal

LIGHT_COLOR = (1.0, .72, .38, 1.0)
PAINT_COLOR = (.68, .423, .032, 1.0)
TAGS = {'DYNACITY_PILOT_LIGHT', 'DYNACITY_STREET_LIGHT'}
PAINT = '/Game/Beirut/LibraryAssets/Materials/M_LampYellowPaint_v1'


def apply():
    import city_world, library_assets
    city_world._current()
    if library_assets.active()['street']['street_lamp'].get('model_manifest'):
        raise RuntimeError('The original lattice lamp supersedes this ornate-lamp paint helper.')
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before saving street-lamp appearance.')
    mesh = unreal.load_asset(library_assets.active()['street']['street_lamp']['mesh'])
    slots = list(mesh.get_editor_property('static_materials'))
    indices = [i for i, slot in enumerate(slots) if str(slot.material_slot_name) == 'street_lamp_01']
    if len(indices) != 1:
        raise RuntimeError('Expected one explicitly identified metal slot.')
    index = indices[0]
    before_materials = [s.material_interface.get_path_name() for s in slots]
    original = before_materials[index]
    backup = city_world._root()/'Saved/DynaCity/yellow-pole-backup-20261031'
    backup.mkdir(exist_ok=True)
    relative = mesh.get_path_name().split('.')[0].removeprefix('/Game/') + '.uasset'
    source_file = city_world._root()/'Content'/relative
    destination = backup/source_file.name
    if not destination.exists():
        shutil.copy2(source_file, destination)
    if not unreal.EditorAssetLibrary.does_asset_exist(PAINT):
        material = unreal.EditorAssetLibrary.duplicate_asset(original, PAINT)
        if material is None:
            raise RuntimeError('Could not duplicate the metal material.')
        lib = unreal.MaterialEditingLibrary
        # Retain the authored normal and roughness maps in the duplicate.
        color = lib.create_material_expression(material, unreal.MaterialExpressionConstant3Vector, -300, -300)
        color.set_editor_property('constant', unreal.LinearColor(*PAINT_COLOR))
        lib.connect_material_property(color, '', unreal.MaterialProperty.MP_BASE_COLOR)
        metal = lib.create_material_expression(material, unreal.MaterialExpressionConstant, -300, -200)
        metal.set_editor_property('r', 0.0)
        lib.connect_material_property(metal, '', unreal.MaterialProperty.MP_METALLIC)
        lib.recompile_material(material)
        unreal.EditorAssetLibrary.set_metadata_tag(material, 'DynaCityAppearance', 'User requested yellow painted pole, 2026-10-31; illustrative color')
        if not unreal.EditorAssetLibrary.save_loaded_asset(material):
            raise RuntimeError('Yellow paint material save failed.')
    material = unreal.load_asset(PAINT)
    mesh.modify()
    mesh.set_material(index, material)
    if not unreal.EditorAssetLibrary.save_loaded_asset(mesh):
        raise RuntimeError('Lamp mesh material assignment save failed.')
    descriptors = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    selected = [d.guid for d in descriptors if str(d.label).startswith(
        ('Reference lamp illumination', 'Mapped lamp illumination ', 'Reference preview - CC0 lamps'))
        or (str(d.label).startswith('Street_') and str(d.label).endswith('_street_lamp'))]
    unreal.WorldPartitionBlueprintLibrary.load_actors(selected)
    lights, groups, packages = [], [], []
    for actor in city_world._actors():
        if isinstance(actor, unreal.PointLight) and TAGS.intersection(str(t) for t in actor.tags):
            component = actor.get_component_by_class(unreal.PointLightComponent)
            actor.modify(); component.modify()
            component.set_light_color(unreal.LinearColor(*LIGHT_COLOR))
            color = component.get_editor_property('light_color')
            lights.append({'actor': actor.get_path_name(), 'srgb': [color.r, color.g, color.b],
                           'intensity': component.get_editor_property('intensity')})
            packages.append(actor.get_outermost())
        for component in actor.get_components_by_class(unreal.StaticMeshComponent):
            if component.static_mesh != mesh:
                continue
            actor.modify(); component.modify()
            component.set_material(index, material)
            count = component.get_instance_count() if isinstance(component, unreal.InstancedStaticMeshComponent) else 1
            groups.append({'actor': actor.get_path_name(), 'instances': count, 'metal_material': component.get_material(index).get_path_name()})
            packages.append(actor.get_outermost())
    if packages and not unreal.EditorLoadingAndSavingUtils.save_packages(list(set(packages)), True):
        raise RuntimeError('Lamp actor package save failed.')
    after_materials = [s.material_interface.get_path_name() for s in mesh.get_editor_property('static_materials')]
    if any(after_materials[i] != path for i, path in enumerate(before_materials) if i != index):
        raise RuntimeError('An optical material unexpectedly changed.')
    report = {'mesh': mesh.get_path_name(), 'paint_linear': list(PAINT_COLOR),
              'material_before': original, 'material_after': material.get_path_name(),
              'glass_and_bulb_unchanged': True, 'light_color_restored': lights,
              'groups': groups, 'visible_instance_count': sum(g['instances'] for g in groups),
              'verification_summary': 'Yellow paint assigned to lamp metal; glass and bulb slots retained. Original light tint restored. Rendered daylight review pending.',
              'limitations': 'User-directed yellow paint approximation on existing CC0 geometry; not an exact reconstruction of the photographed lattice utility pole.'}
    target = city_world._root()/'Saved/DynaCity/street-lamp-paint-20261031.json'
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
