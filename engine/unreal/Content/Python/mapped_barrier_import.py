"""Import source-linked finished barriers, replacing only matching cover actors.

Call apply(path) with the Beirut city world open and editor Play stopped. The
prior source covers remain saved and hidden, so the operation can be reversed.
"""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import unreal

TAG = 'DYNACITY_FINISHED_BARRIER'


def apply(filename):
    import city_world, explore_city, street_context, library_assets
    from library_asset_core import read_prepared
    from mapped_barrier_core import replacement_plan
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before importing barrier models')
    path = Path(filename).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    covers = json.loads((city_world._root()/'Saved/DynaCity/street-covers.json').read_text(encoding='utf-8'))
    origin = json.loads(city_world._checkpoint().read_text(encoding='utf-8'))['origin_utm_m']
    replacements = replacement_plan(data, covers, origin)
    context_path = Path(data['context_path'])
    if hashlib.sha256(context_path.read_bytes()).hexdigest() != data['context_sha256']:
        raise ValueError('Barrier source context changed after the placement manifest was made')
    prepared = Path(data['prepared_path'])
    checked = read_prepared(prepared)
    if checked['fbx_sha256'] != data['asset_fbx_sha256'] or checked['provenance'].get('id') != 'concrete_road_barrier':
        raise ValueError('Barrier asset does not match the prepared manifest')
    cls = unreal.load_class(None, '/Script/DynaCityDrive.DynaStreetInstances')
    if cls is None:
        raise RuntimeError('Street instancing module is unavailable')
    street_context.load_editor_geometry()
    actors = city_world._actors()
    if any(TAG in actor.tags for actor in actors):
        raise RuntimeError('Finished barrier actors already exist; inspect or restore before reapplying')
    by_path = {a.get_path_name(): a for a in actors}
    old = []
    for item in replacements:
        actor = by_path.get(item['actor'])
        if actor is None or 'DYNACITY_STREET_COVER' not in actor.tags or 'cover_kind:jersey_barrier' not in actor.tags:
            raise RuntimeError('Expected original barrier actor is unavailable: '+item['actor'])
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        if component is None:
            raise RuntimeError('Expected original barrier mesh component')
        old.append((actor, component, component.is_visible(), component.get_editor_property('hidden_in_game')))
    imported = library_assets.import_prepared(prepared, 'concrete_road_barrier')
    mesh = unreal.load_asset(imported['mesh'])
    bounds = mesh.get_bounding_box()
    center = (bounds.min + bounds.max) * .5
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    _, east, north, _ = explore_city._basis()
    groups = defaultdict(list)
    for row in data['placements']:
        groups[row.get('production_area', 'Unassigned')].append(row)
    made = []
    try:
        for area, rows in sorted(groups.items()):
            actor = subsystem.spawn_actor_from_class(cls, unreal.Vector())
            if actor is None: raise RuntimeError('Could not create barrier instance actor')
            made.append(actor)
            actor.set_actor_label('Street_FinishedBarrier_'+re.sub('[^A-Za-z0-9]+', '_', area))
            actor.tags = [TAG]
            actor.set_folder_path('DynaCity/Finished mapped barriers/'+area)
            actor.set_editor_property('is_spatially_loaded', True)
            actor.set_editor_property('layer_kind', 'jersey_barrier')
            actor.set_editor_property('production_area', area)
            actor.set_editor_property('source_manifest', str(path))
            actor.set_street_mesh(mesh); actor.set_street_collision(False)
            actor.set_street_cull_distance(25000, 70000)
            for row in rows:
                angle = math.radians(row['heading_degrees'])
                rotation = unreal.MathLibrary.make_rot_from_x(east*math.cos(angle)+north*math.sin(angle))
                scale = unreal.Vector(row['scale_x'], 1, 1)
                # Match the imported model's own bounds, including FBX axis conversion.
                center_offset = (unreal.MathLibrary.get_forward_vector(rotation)*center.x*scale.x
                                 + unreal.MathLibrary.get_right_vector(rotation)*center.y)
                position = explore_city._world(row['position_local_m']) - center_offset
                source = '%s:%s:%s' % (row['source_instance_id'], row['display_segment'], row['display_part'])
                if actor.add_street_instance(unreal.Transform(location=position, rotation=rotation, scale=scale), source) < 0:
                    raise RuntimeError('Could not create mapped barrier instance')
            if actor.get_street_instance_count() != len(rows):
                raise RuntimeError('Barrier instance count does not match source')
        for actor, component, _, _ in old:
            actor.modify(); component.modify()
            component.set_visibility(False); component.set_hidden_in_game(True)
        if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():
            raise RuntimeError('Could not save barrier world')
        if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True):
            raise RuntimeError('Could not save barrier packages')
    except Exception:
        for actor, component, visible, hidden in old:
            component.set_visibility(visible); component.set_hidden_in_game(hidden)
        for actor in made: subsystem.destroy_actor(actor)
        raise
    report = {'schema':'dynacity-finished-barrier-import/1', 'manifest':str(path),
              'asset':imported, 'actors':[a.get_path_name() for a in made],
              'instances':len(data['placements']), 'replaced':replacements,
              'previous_visibility':[{'actor':a.get_path_name(), 'visible':v, 'hidden_in_game':h} for a,c,v,h in old]}
    target = city_world._root()/'Saved/DynaCity/finished-barrier-import.json'
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
