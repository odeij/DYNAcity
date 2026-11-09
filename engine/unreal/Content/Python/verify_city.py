"""Check the actual loaded city world, not just export file counts."""
import json
from pathlib import Path
import unreal


def verify():
    import city_world,city_collision
    world=city_world._current();data=json.loads(city_world._checkpoint().read_text())
    state=city_world.status()
    if state['pending'] or len(state['completed'])!=state['total_packages']:
        raise RuntimeError('City import is incomplete')
    loaded_before=sum(city_world.TAG in a.tags for a in city_world._actors())
    city_world.load_editor_geometry()
    actors=city_world._actors()
    meshes=[a for a in actors if city_world.TAG in a.tags]
    expected=sum(p['actors'] for p in state['completed'].values())
    if len(meshes)!=expected:raise RuntimeError('Loaded source actor count differs from checkpoint')
    basis=[a for a in meshes if a.get_actor_label().startswith('DYNACITY_BASIS_')]
    if len(basis)!=4:raise RuntimeError('Expected one coordinate basis')
    import dynacity_tools
    origin,*axes=dynacity_tools._basis()
    surfaces=[a for a in meshes if a not in basis]
    if any(a.get_attach_parent_actor() is not None for a in surfaces):
        raise RuntimeError('Importer hierarchy still links source actors')
    if any(not a.get_editor_property('is_spatially_loaded') for a in surfaces):
        raise RuntimeError('Source surface is not spatially loaded')
    partition=world.get_world_settings().get_editor_property('world_partition')
    if partition is None:raise RuntimeError('Map is not partitioned')
    bad=[]
    fallback_bad=[]
    mesh_editor=unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    for actor in surfaces:
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        body=component.static_mesh.get_editor_property('body_setup')
        if body.get_editor_property('collision_trace_flag')!=unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE:
            bad.append(actor.get_actor_label())
        settings=mesh_editor.get_nanite_settings(component.static_mesh)
        if settings.get_editor_property('enabled') and (
            settings.get_editor_property('fallback_target')!=unreal.NaniteFallbackTarget.PERCENT_TRIANGLES or
            settings.get_editor_property('fallback_percent_triangles')!=1.0 or
            settings.get_editor_property('fallback_relative_error')!=0.0):
            fallback_bad.append(actor.get_actor_label())
    if bad:raise RuntimeError('Missing complex source collision: '+str(bad))
    if fallback_bad:raise RuntimeError('Simplified fallback collision on source surfaces: '+str(fallback_bad))
    road_file=data.get('roads_metadata')
    hits=[]
    if road_file:
        roads=json.loads(Path(road_file).read_text())['roads']
        for road in roads[::max(1,len(roads)//20)]:
            point=road['path'][len(road['path'])//2]
            hits.append({'road_id':road['id'],'simple_trace_hit':city_collision.trace_at_source(point) is not None})
    if not hits or not all(h['simple_trace_hit'] for h in hits):
        raise RuntimeError('Source road trace validation failed: '+str(hits))
    result={'map':world.get_path_name(),'partition_object':partition.get_path_name(),
            'source_actors':len(meshes),'surface_actors':len(surfaces),'basis_actors':len(basis),
            'source_objects':sum(p.get('object_count',0) for p in data['packages']),
            'detached_surface_actors':len(surfaces),'spatial_surface_actors':len(surfaces),
            'source_actors_loaded_before_editor_request':loaded_before,
            'collision_mode':'complex-as-simple on static source surfaces','simple_trace_samples':hits,
            'source_fallback':'explicit 100 percent triangles; zero relative error',
            'basis_cm_per_m':[axis.length() for axis in axes],
            'limits':['Runtime streaming performance and vehicle physics are not validated by these checks.',
                      'A trace hit confirms a blocking surface, not surveyed road elevation or legal driveability.']}
    path=city_world._root()/'Saved'/'DynaCity'/'city-verification.json'
    path.write_text(json.dumps(result,indent=2))
    return result
