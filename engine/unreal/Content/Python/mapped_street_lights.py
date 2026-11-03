"""Attach night lights to explicitly mapped street-lamp instances only."""
import json
import re
import unreal

TAG = 'DYNACITY_STREET_LIGHT'


def install():
    import city_world
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before adding mapped street lights.')
    source = city_world._root() / 'Saved/DynaCity/street-instances.json'
    records = json.loads(source.read_text(encoding='utf-8'))
    rows = [row for row in records['instances'] if row['kind'] == 'street_lamp']
    if not rows:
        raise RuntimeError('No mapped street-lamp instances in the saved source index.')
    import library_assets
    anchor=unreal.Vector(*library_assets.active()['street']['street_lamp'].get('light_anchor_cm',[0,0,345]))
    # Load only the relevant instanced groups and existing light descriptors.
    # Loading every street/cover actor at once can exhaust memory on this city.
    labels = {'Street_' + re.sub('[^A-Za-z0-9]+', '_', group['area']) + '_street_lamp'
              for group in records['groups'] if group['kind'] == 'street_lamp'}
    descriptors = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    selected = [d.guid for d in descriptors if str(d.label) in labels or
                str(d.label).startswith('Mapped lamp illumination ')]
    unreal.WorldPartitionBlueprintLibrary.load_actors(selected)
    actors = {a.get_path_name(): a for a in city_world._actors()}
    existing = [a for a in actors.values() if TAG in a.tags]
    if existing:
        raise RuntimeError('Mapped street lights already exist; inspect before adding duplicates.')
    seen = set()
    placements = []
    for row in rows:
        source_id = row['source_id']
        if not source_id.startswith('node/') or source_id in seen:
            raise ValueError('Expected unique OSM street-lamp nodes.')
        seen.add(source_id)
        actor = actors.get(row['actor'])
        if actor is None:
            raise RuntimeError('Mapped lamp actor is not loaded: ' + row['actor'])
        index = int(row['instance'])
        source_ids = actor.get_editor_property('source_ids')
        if index < 0 or index >= len(source_ids) or source_ids[index] != row['instance_id']:
            raise RuntimeError('Saved lamp index no longer matches the actor.')
        component = actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        transform = component.get_instance_transform(index, world_space=True)
        # Model-local anchor includes the arm offset and rotates with the pole.
        position = unreal.MathLibrary.transform_location(transform,anchor)
        placements.append((source_id, position))
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    created = []
    try:
        for source_id, position in placements:
            light = subsystem.spawn_actor_from_class(unreal.PointLight, position)
            created.append(light)
            light.set_actor_label('Mapped lamp illumination ' + re.sub('[^A-Za-z0-9]+', '_', source_id))
            light.tags = [TAG, 'OSM_' + source_id.replace('/', '_')]
            light.set_folder_path('DynaCity/Mapped street lights')
            light.set_editor_property('is_spatially_loaded', True)
            component = light.get_component_by_class(unreal.PointLightComponent)
            component.set_mobility(unreal.ComponentMobility.MOVABLE)
            component.set_intensity(0)
            component.set_attenuation_radius(1400)
            component.set_cast_shadows(False)
            component.set_light_color(unreal.LinearColor(1, .72, .38, 1))
        if not unreal.EditorLoadingAndSavingUtils.save_packages([a.get_outermost() for a in created], True):
            raise RuntimeError('Saving the mapped light actor packages failed.')
    except Exception:
        for actor in created:
            subsystem.destroy_actor(actor)
        raise
    report = {'count': len(created), 'source_ids': sorted(seen),
              'actors': [a.get_path_name() for a in created],
              'limits': ['Generic light colour and strength; lamp function and operating hours are not mapped facts.',
                         'Night mode requires the updated native driving plugin.']}
    (city_world._root() / 'Saved/DynaCity/mapped-street-light-import.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
