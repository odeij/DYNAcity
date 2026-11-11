"""Install only the isolated, finished-module street-detail proposal layer."""
import hashlib
import json
import math
from pathlib import Path
import time

import unreal

TAG = 'DYNACITY_STREET_DETAIL_V1'


def apply(filename):
    import city_world, explore_city, library_assets
    started=time.perf_counter(); city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before regenerating street detail')
    if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
        raise RuntimeError('Wait for the existing asset import to finish')
    manifest=json.loads(Path(filename).read_text(encoding='utf8'))
    if manifest['schema']!='dynacity-street-detail/1': raise ValueError('Unexpected detail schema')
    if manifest.get('batch_checks',{}).get('failures'): raise ValueError('Resolve batch validation failures before installation')
    if manifest['origin_utm_m']!=json.loads(city_world._checkpoint().read_text())['origin_utm_m']:
        raise ValueError('Street detail origin differs from existing city')
    root=city_world._root()/'Saved/DynaCity'; state_path=root/'street-detail-state.json'
    old=json.loads(state_path.read_text()) if state_path.exists() else {'streets':{}}
    placement_path=root/'street-detail-placement-index.json'
    placements=json.loads(placement_path.read_text()) if placement_path.exists() else {}
    meshes={}; asset_signature=[]
    for kind in ('sidewalk','curb'):
        entry=manifest['assets'][kind]
        from library_asset_core import read_prepared
        prepared=read_prepared(entry['prepared'])
        imported=library_assets.import_prepared(entry['prepared'],'street_detail_'+kind)
        mesh=unreal.load_asset(imported['mesh'])
        if mesh is None: raise RuntimeError('Missing finished '+kind+' mesh')
        box=mesh.get_bounding_box();size=box.max-box.min
        if min(size.x,size.y,size.z)<=0: raise ValueError('Invalid modular mesh bounds')
        meshes[kind]=(mesh,box,size);asset_signature.append(prepared['fbx_sha256'])
    asset_signature=hashlib.sha256(Path(__file__).read_bytes()+'|'.join(asset_signature).encode()).hexdigest()
    descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs if str(d.label).startswith('Street detail | ')])
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    existing=[a for a in city_world._actors() if TAG in a.tags]
    cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances')
    if cls is None: raise RuntimeError('Compiled shared street instance class required')
    scope={row['street_id'] for row in manifest['streets']}|set(manifest.get('removed_streets',[]))
    state={sid:value for sid,value in old['streets'].items() if sid not in scope}
    _,east,north,up_axis=explore_city._basis(); changed=[]; skipped=[]; created=[]; retired=[]; instances=[]
    try:
        for row in manifest['streets']:
            sid=row['street_id'];signature=row['signature']+asset_signature
            groups=[a for a in existing if ('street_id:'+sid) in a.tags]
            data=json.loads(Path(row['file']).read_text(encoding='utf8'))
            expected={k:sum(f['kind']==k for f in data['features']) for k in meshes}
            valid=(len(groups)==2 and all(a.get_street_instance_count()==expected.get(str(a.get_editor_property('layer_kind')),-1) for a in groups))
            if old['streets'].get(sid,{}).get('signature')==signature and valid:
                skipped.append(sid);state[sid]=old['streets'][sid];continue
            if data['checks']['failures']: raise ValueError('Failed layout checks: '+sid)
            placements[sid]={}
            actors={}
            for kind,(mesh,box,size) in meshes.items():
                actor=subsystem.spawn_actor_from_class(cls,unreal.Vector());created.append(actor)
                actor.set_actor_label('Street detail | '+sid+' | '+kind)
                actor.tags=[TAG,'street_id:'+sid,'PROPOSED_UNSURVEYED_LAYOUT']
                actor.set_folder_path('DynaCity/Generated street detail/'+data['street'].get('district','Unassigned')+'/'+sid)
                actor.set_editor_property('is_spatially_loaded',True)
                actor.set_editor_property('layer_kind',kind);actor.set_editor_property('production_area',sid)
                actor.set_editor_property('source_manifest',row['file'])
                actor.set_street_mesh(mesh);actor.set_street_collision(False)
                actor.set_street_cull_distance(20000,45000);actors[kind]=actor
            for feature in data['features']:
                kind=feature['kind'];mesh,box,size=meshes[kind];angle=math.radians(feature['yaw_degrees'])
                gx,gy,_=feature['ground_plane'];dx,dy=math.cos(angle),math.sin(angle)
                forward=east*dx+north*dy+up_axis*(gx*dx+gy*dy)
                up=up_axis-east*gx-north*gy
                rotation=unreal.MathLibrary.make_rot_from_xz(forward,up)
                # Finished tiles have a centred XY and ground-contact Z origin.
                if max(abs(box.min.x+box.max.x),abs(box.min.y+box.max.y),abs(box.min.z))>.1:
                    raise ValueError('Expected centred, ground-contact module')
                world=explore_city._world(feature['position_local_m'])
                # Reuse existing source pavement as support. A terrain-only
                # proposal cannot tell whether this spot already has a slab.
                hit=unreal.SystemLibrary.line_trace_single(city_world._current(),world+unreal.Vector(0,0,40),
                    world-unreal.Vector(0,0,80),unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,False,existing+created,
                    unreal.DrawDebugTrace.NONE,True)
                support=hit.to_dict() if hit is not None else {}
                if not support.get('blocking_hit') or support['impact_normal'].z<.7:
                    raise ValueError('No suitable native source support: '+feature['id'])
                ground_z=support['impact_point'].z
                planned_top=world.z+feature['height_m']*100
                if abs(ground_z-world.z)>30:
                    raise ValueError('Native/source elevation mismatch: '+feature['id'])
                mode='finished_cap_over_existing_pavement' if ground_z>=planned_top-2 else 'grounded_module'
                world.z=ground_z+.3
                height_cm=2 if mode=='finished_cap_over_existing_pavement' else planned_top-world.z
                if height_cm<=0: raise ValueError('Invalid grounded height')
                scale=unreal.Vector(feature['length_m']*100/size.x,feature['width_m']*100/size.y,height_cm/size.z)
                placements[sid][feature['id']]={'position_world_cm':[world.x,world.y,world.z],
                                               'height_cm':height_cm,'support_mode':mode}
                transform=unreal.Transform(location=world,rotation=rotation,scale=scale)
                index=actors[kind].add_street_instance(transform,feature['id'])
                if index<0: raise RuntimeError('Could not add '+feature['id'])
                instances.append({'id':feature['id'],'actor':actors[kind].get_path_name(),'index':index})
            for kind,actor in actors.items():
                if actor.get_street_instance_count()!=expected[kind]: raise RuntimeError('Instance count mismatch')
                actor.modify()
            retired.extend(groups);changed.append(sid)
            state[sid]={'signature':signature,'counts':expected}
    except Exception:
        for actor in created: subsystem.destroy_actor(actor)
        raise
    # All replacements exist before removing only this pipeline's old groups.
    removed=set(manifest.get('removed_streets',[]))
    retired.extend(a for a in existing if any(('street_id:'+sid) in a.tags for sid in removed))
    for actor in set(retired): subsystem.destroy_actor(actor)
    if created or retired:
        if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level(): raise RuntimeError('Level save failed')
        if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True): raise RuntimeError('Actor package save failed')
    state_path.write_text(json.dumps({'streets':state},indent=2))
    for sid in removed: placements.pop(sid,None)
    placement_path.write_text(json.dumps(placements,indent=2))
    if manifest.get('surface_finishes'):
        import street_detail_finish
        street_detail_finish.apply(manifest['surface_finishes'],persist=True)
    report={'changed':changed,'unchanged':skipped,'groups':len(state)*2,
            'instances':sum(sum(s['counts'].values()) for s in state.values()),
            'created_instance_index':instances,'editor_time_s':time.perf_counter()-started,
            'collision':'disabled; decorative proposal layer','saved':True,'reload_verified':False}
    (root/'street-detail-import.json').write_text(json.dumps(report,indent=2))
    return report


def verify_reload(filename):
    """Unload/reload only generated actors and compare the full stable-ID multiset."""
    import city_world, explore_city
    manifest=json.loads(Path(filename).read_text(encoding='utf8'))
    placement_path=city_world._root()/'Saved/DynaCity/street-detail-placement-index.json'
    installed=json.loads(placement_path.read_text()) if placement_path.exists() else {}
    installed={fid:value for group in installed.values() for fid,value in group.items()}
    descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    guids=[d.guid for d in descs if str(d.label).startswith('Street detail | ')]
    unreal.WorldPartitionBlueprintLibrary.unload_actors(guids)
    unreal.WorldPartitionBlueprintLibrary.load_actors(guids)
    selected={row['street_id'] for row in manifest['streets']}
    actors=[a for a in city_world._actors() if TAG in a.tags and any(('street_id:'+sid) in a.tags for sid in selected)]
    expected=[]; definitions={}
    for row in manifest['streets']:
        for feature in json.loads(Path(row['file']).read_text(encoding='utf8'))['features']:
            expected.append(feature['id']);definitions[feature['id']]=feature
    actual=[]; misplaced=[]; ground_issues=[]; checked=0
    for actor in actors:
        component=actor.get_editor_property('instances')
        for index,value in enumerate(actor.get_editor_property('source_ids')):
            fid=str(value);actual.append(fid)
            if fid not in definitions:continue
            transform=component.get_instance_transform(index,world_space=True)
            where=transform.translation
            expected_world=unreal.Vector(*installed[fid]['position_world_cm']) if fid in installed else explore_city._world(definitions[fid]['position_local_m'])
            if (where-expected_world).length()>.1: misplaced.append(fid)
            checked+=1
            hit=unreal.SystemLibrary.line_trace_single(city_world._current(),where+unreal.Vector(0,0,40),
                where-unreal.Vector(0,0,80),unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,False,actors,
                unreal.DrawDebugTrace.NONE,True)
            info=hit.to_dict() if hit is not None else {}
            if not info.get('blocking_hit'):
                ground_issues.append({'id':fid,'reason':'no_native_ground_hit'})
            else:
                separation=where.z-info['impact_point'].z
                if separation < -1 or separation > 3:
                    ground_issues.append({'id':fid,'reason':'native_support_height','separation_cm':separation})
    result={'expected':len(expected),'actual':len(actual),'groups':len(actors),
            'reload_verified':sorted(expected)==sorted(actual) and len(actual)==len(set(actual)) and not misplaced,
            'checked_transforms':checked,'misplaced':misplaced,'native_ground_issues':ground_issues,
            'missing':sorted(set(expected)-set(actual)),'unexpected':sorted(set(actual)-set(expected))}
    (city_world._root()/'Saved/DynaCity/street-detail-reload.json').write_text(json.dumps(result,indent=2))
    return result


_profile=None


def profile_frames(samples=90):
    """Compare editor Slate frame intervals with this layer hidden/visible.

    This is editor tick cadence, not GPU duration or packaged-game FPS.
    """
    global _profile
    import city_world
    if _profile and _profile.get('running'): raise RuntimeError('Detail profile already running')
    if not 30<=samples<=300: raise ValueError('Use 30 to 300 frame samples')
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before editor frame comparison')
    actors=[a for a in city_world._actors() if TAG in a.tags]
    states=[(a,a.is_temporarily_hidden_in_editor()) for a in actors]
    _profile={'running':True,'samples_per_phase':samples,'phase':'baseline_hidden','warmup':30,
              'values':[],'results':{},'metric':'editor Slate tick interval ms; not GPU or packaged FPS',
              'groups':len(actors),'instances':sum(a.get_street_instance_count() for a in actors)}
    for a,_ in states: a.set_is_temporarily_hidden_in_editor(True)
    def restore():
        for a,hidden in states: a.set_is_temporarily_hidden_in_editor(hidden)
    def tick(delta):
        global _profile
        try:
            if _profile['warmup']:
                _profile['warmup']-=1;return
            _profile['values'].append(float(delta)*1000)
            if len(_profile['values'])<samples:return
            import statistics
            ordered=sorted(_profile['values'])
            _profile['results'][_profile['phase']]={'median_ms':statistics.median(ordered),
                                                   'p95_ms':ordered[min(len(ordered)-1,int(.95*len(ordered)))],
                                                   'samples':samples}
            if _profile['phase']=='baseline_hidden':
                restore();_profile['phase']='detail_visible';_profile['warmup']=30;_profile['values']=[]
            else:
                restore();_profile['running']=False
                unreal.unregister_slate_post_tick_callback(_profile['handle'])
                target=city_world._root()/'Saved/DynaCity/street-detail-frame-profile.json'
                target.write_text(json.dumps({k:v for k,v in _profile.items() if k not in ('handle','values')},indent=2))
        except Exception as exc:
            restore();_profile['running']=False;_profile['error']=str(exc)
            unreal.unregister_slate_post_tick_callback(_profile['handle'])
    _profile['handle']=unreal.register_slate_post_tick_callback(tick)
    return {'started':True,'samples_per_phase':samples}


def profile_status():
    return {k:v for k,v in (_profile or {}).items() if k not in ('handle','values')}
