"""A bounded illustrative street-finish preview, separate from mapped OSM facts."""
import json
import math
from pathlib import Path
import unreal

TAG='DYNACITY_REFERENCE_PREVIEW'


def import_asset(filename, name):
    path='/Game/Beirut/Prototype/Vehicles' if name=='Cockpit' else '/Game/Beirut/Reference'
    target=path+'/'+name+'.'+name
    if unreal.EditorAssetLibrary.does_asset_exist(target):return target
    task=unreal.AssetImportTask();task.filename=str(filename);task.destination_path=path
    task.destination_name=name;task.automated=True;task.save=True
    options=unreal.FbxImportUI();options.import_mesh=True;options.import_materials=True;options.import_textures=True
    options.import_as_skeletal=False;options.static_mesh_import_data.combine_meshes=True
    options.static_mesh_import_data.generate_lightmap_u_vs=False;task.options=options
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    if not unreal.EditorAssetLibrary.does_asset_exist(target):raise RuntimeError('Asset import failed: '+name)
    return target


def build(asset_folder):
    import city_world,explore_city,location_search,location_core
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():raise RuntimeError('End Play before dressing the reference street.')
    folder=Path(asset_folder)
    import library_assets
    approved=library_assets.active()['street']
    lamp_record=approved['street_lamp']
    lamp=unreal.load_asset(lamp_record['mesh'])
    size=lamp.get_bounding_box().max-lamp.get_bounding_box().min
    expected_height=lamp_record.get('dimensions_cm',[0,0,387])[2]
    if abs(size.z-expected_height)>2:raise ValueError('Lamp scale must match active model centimetres.')
    light_anchor=unreal.Vector(*lamp_record.get('light_anchor_cm',[0,0,345]))
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    # Generated groups are spatial actors. Load their descriptors before replacing
    # them so a cold editor cannot leave an older, unloaded copy behind.
    descriptors=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    wanted=[d.guid for d in descriptors if str(d.label).startswith(('Reference preview -','Reference lamp illumination'))]
    unreal.WorldPartitionBlueprintLibrary.load_actors(wanted)
    cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances')
    data=location_search._data();roads=[r for r in data['roads'] if r['id'] in {10086,8415,8416,10167,10168}]
    other_roads={'roads':[r for r in data['roads'] if r['name']!='Charles Malek Avenue']}
    props=json.loads((city_world._root()/'Saved/DynaCity/street-prop-import.json').read_text(encoding='utf-8'))
    # Build replacements first; the old generated layer survives any failed build.
    old=[a for a in city_world._actors() if TAG in a.tags];created=[];records=[]
    east,north=explore_city._basis()[1:3]
    def grounded(position):
        where=explore_city._world(position)
        hit=unreal.SystemLibrary.line_trace_single(city_world._current(),where+unreal.Vector(0,0,200),
            where-unreal.Vector(0,0,300),unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,False,[],unreal.DrawDebugTrace.NONE,True)
        if hit is None:raise ValueError('No loaded ground for the library prop placement.')
        info=hit.to_dict()
        if info['impact_normal'].z<.7 or abs(info['impact_point'].z-where.z)>150:
            raise ValueError('The reference furniture position has no suitable nearby ground.')
        return info['impact_point']
    def group(name,mesh):
        actor=subsystem.spawn_actor_from_class(cls,unreal.Vector());created.append(actor)
        actor.set_actor_label('Reference preview - '+name);actor.tags=[TAG,'ILLUSTRATIVE_PLACEMENT']
        actor.set_folder_path('DynaCity/Reference preview');actor.set_editor_property('is_spatially_loaded',True)
        actor.set_street_mesh(mesh);actor.set_street_collision(False);actor.set_street_cull_distance(25000,60000)
        return actor
    try:
        lamps=group('lattice lamps' if lamp_record.get('model_manifest') else 'CC0 lamps',lamp)
        benches=group('library benches',unreal.load_asset(approved['bench']['mesh']))
        bins=group('library bins',unreal.load_asset(approved['waste_basket']['mesh']))
        # Only the southern edge of the eastbound carriageway is dressed. Keep
        # the northern parallel carriageway and intersections unobstructed.
        for offset in range(-120,121,24):
            # Three illustrative lamps required building clearance adjustments. Keep
            # their stable IDs while shifting them along the same road.
            clearance_shift={-48:2.0,48:-8.0,96:-6.0}.get(offset,0.0)
            point=[2423.6417+offset+clearance_shift,1491.691]
            near=location_core.nearest_roads({'roads':roads},point,max_distance=8,limit=1)
            if not near:continue
            row=near[0];p=row['position'];dx,dy=row['direction'];length=math.hypot(dx,dy)
            normal=[dy/length,-dx/length]
            position=[p[0]+normal[0]*6.15,p[1]+normal[1]*6.15,p[2]]
            if location_core.nearest_roads(other_roads,position,max_distance=5,limit=1):continue
            direction=east*(dx/length)+north*(dy/length)
            rotation=unreal.MathLibrary.make_rot_from_x(direction)
            lamp_rotation=unreal.MathLibrary.make_rot_from_x(east*(-normal[0])+north*(-normal[1])) if lamp_record.get('light_anchor_cm') else rotation
            transform=unreal.Transform(grounded(position),lamp_rotation,unreal.Vector(1,1,1))
            lamps.add_street_instance(transform,'illustrative/lamp/'+str(offset))
            light=subsystem.spawn_actor_from_class(unreal.PointLight,unreal.MathLibrary.transform_location(transform,light_anchor))
            created.append(light);light.tags=[TAG,'DYNACITY_PILOT_LIGHT'];light.set_actor_label('Reference lamp illumination')
            light.set_folder_path('DynaCity/Reference preview');light.set_editor_property('is_spatially_loaded',True)
            component=light.get_component_by_class(unreal.PointLightComponent)
            component.set_mobility(unreal.ComponentMobility.MOVABLE);component.set_intensity(0)
            component.set_attenuation_radius(1400);component.set_cast_shadows(False)
            component.set_light_color(unreal.LinearColor(1,.72,.38,1))
            if offset%48==0:
                for target,key,shift in ((benches,'bench',3),(bins,'bin',-2)):
                    # Keep these illustrative props clear of modeled buildings
                    # and adjacent roads when rebuilding the reference street.
                    extra={('bin',-48):7.5,('bench',48):25.5,('bench',96):14.5}.get((key,offset),0.0)
                    q=[position[0]+dx/length*(shift+extra),position[1]+dy/length*(shift+extra),position[2]]
                    target.add_street_instance(unreal.Transform(grounded(q),rotation,unreal.Vector(1,1,1)),
                                              'illustrative/'+key+'/'+str(offset))
            records.append({'road_id':row['road_id'],'position_local_m':position,'source':'illustrative placement along source road tangent'})
        for actor in old:subsystem.destroy_actor(actor)
        if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True):raise RuntimeError('Could not save reference preview.')
    except Exception:
        for actor in created:subsystem.destroy_actor(actor)
        raise
    report={'street':'Charles Malek Avenue','section_length_m':240,'lamp_count':len(records),
        'replaced_actor_count':len(old),'created_actor_count':len(created),
        'pavement_slabs':0,'pavement_width_m':None,'placements':records,
        'model':'https://polyhaven.com/a/street_lamp_01','license':'CC0 1.0','api_credit':'Powered by Poly Haven',
        'limits':['Illustrative street dressing; lamp/bench/bin positions are not surveyed.',
                  'Procedural pavement and curbs are retired. Replacement requires a finished library asset.',
                  'Mapped signals, trees and crossings retain the existing source layer.',
                  'This is a bounded finish preview, not a complete real-world street inventory.']}
    (city_world._root()/'Saved/DynaCity/reference-preview.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
