"""Editor-only SUMO replay proof; no physics or packaged runtime integration.

After scene import: import preview_traffic; preview_traffic.start(r'.../traffic-replay.json')
Stop using preview_traffic.stop(). A fixed simulation clock is independent of
editor frame rate. Four imported calibration nodes determine axes and scale.
"""
import bisect
import hashlib
import json
import math
from pathlib import Path
import time
import unreal

_callback=None
_actors=[]
_cars={}
_signals={}
_free=[]
_frames=[]
_times=[]
_basis=None
_started=0
_materials={}
_signal_states={}
_paused_at=None
_vehicle_mesh=None
_vehicle_path=None
_paint_slot=None
_paints=[]


def _material(name,colour,emissive=False):
    path='/Game/Beirut/Prototype/'+name
    result=unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    if result:return result
    result=unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,'/Game/Beirut/Prototype',unreal.Material,unreal.MaterialFactoryNew())
    if emissive:result.set_editor_property('shading_model',unreal.MaterialShadingModel.MSM_UNLIT)
    expression=unreal.MaterialEditingLibrary.create_material_expression(result,unreal.MaterialExpressionConstant3Vector,-200,0)
    expression.set_editor_property('constant',unreal.LinearColor(*colour,1))
    prop=unreal.MaterialProperty.MP_EMISSIVE_COLOR if emissive else unreal.MaterialProperty.MP_BASE_COLOR
    unreal.MaterialEditingLibrary.connect_material_property(expression,'',prop)
    unreal.MaterialEditingLibrary.recompile_material(result)
    unreal.EditorAssetLibrary.save_loaded_asset(result)
    return result


def stop():
    global _callback,_paused_at
    if _callback is not None:
        unreal.unregister_slate_post_tick_callback(_callback);_callback=None
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    for actor in _actors:subsystem.destroy_actor(actor)
    _actors.clear();_cars.clear();_signals.clear();_free.clear();_signal_states.clear()
    _paused_at=None


def pause():
    global _callback,_paused_at
    if _callback is not None:
        _paused_at=time.monotonic()
        unreal.unregister_slate_post_tick_callback(_callback);_callback=None


def resume():
    global _callback,_paused_at,_started
    if _paused_at is not None:
        _started+=time.monotonic()-_paused_at
        _paused_at=None
        _callback=unreal.register_slate_post_tick_callback(_tick)


def _position(p):
    origin,east,north,up=_basis
    return origin+east*p[0]+north*p[1]+up*p[2]


def _spawn(name,mesh_path):
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actor=subsystem.spawn_actor_from_class(unreal.StaticMeshActor,unreal.Vector(0,0,-100000),transient=True)
    actor.set_actor_label('DynaCITY preview '+name)
    component=actor.static_mesh_component
    component.set_mobility(unreal.ComponentMobility.MOVABLE)
    component.set_static_mesh(_vehicle_mesh if mesh_path==_vehicle_path else unreal.load_asset(mesh_path))
    component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    _actors.append(actor)
    return actor


def start(filename):
    global _frames,_times,_basis,_started,_callback,_vehicle_mesh,_paint_slot,_vehicle_path
    if _callback is not None or _paused_at is not None:raise RuntimeError('A preview already exists; resume it or stop it first')
    replay=json.loads(Path(filename).read_text(encoding='utf-8'))
    if replay.get('schema')!='dynacity-engine-replay/1':raise ValueError('Wrong replay schema')
    _frames=replay['frames'];_times=[f['time_s'] for f in _frames]
    if len(_frames)<2 or _times[-1]<=_times[0]:raise ValueError('Replay needs increasing simulation times')
    if any(b<=a for a,b in zip(_times,_times[1:])):raise ValueError('Non-increasing simulation clock')
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    markers={}
    for name in ('ORIGIN','EAST','NORTH','UP'):
        matches=[a for a in subsystem.get_all_level_actors() if a.get_actor_label().startswith('DYNACITY_BASIS_'+name)]
        if len(matches)!=1:raise RuntimeError('Import the calibrated GLB first; missing/duplicate basis '+name)
        markers[name]=matches[0].get_actor_location()
    origin=markers['ORIGIN']
    _basis=(origin,*[(markers[n]-origin)/10 for n in ('EAST','NORTH','UP')])
    for axis in _basis[1:]:
        if not 99<axis.length()<101:raise RuntimeError('Import unit check failed: expected 100 Unreal units per metre')
    import library_assets
    _vehicle_mesh=library_assets.vehicle_mesh()
    _vehicle_path=_vehicle_mesh.get_path_name()
    if _vehicle_mesh:
        box=_vehicle_mesh.get_bounding_box()
        size=box.max-box.min
        if not (350<size.x<550 and 150<size.y<250 and 100<size.z<210):
            raise RuntimeError('Sedan import must be X-forward and measured in centimetres: '+str(size))
        _paint_slot=next((i for i,m in enumerate(_vehicle_mesh.get_editor_property('static_materials'))
                          if m.material_interface and m.material_interface.get_name()=='Body_paint'),None)
        _paints.clear()
        if _paint_slot is not None:
            for name,colour in [('Ivory',(.7,.69,.65)),('Silver',(.38,.4,.42)),
                                ('Charcoal',(.035,.043,.05)),('Blue',(.055,.13,.23)),
                                ('Red',(.32,.035,.025)),('Sand',(.35,.28,.18))]:
                _paints.append(_material('M_VehiclePaint'+name,colour))
    _materials['car']=_material('M_PrototypeVehicle',(.055,.16,.24))
    for state,colour in {'r':(8,.03,.01),'y':(8,3,.01),'g':(.03,8,.3),'off':(.03,.03,.03)}.items():
        _materials[state]=_material('M_Signal_'+state,colour,True)
    _started=time.monotonic()
    _callback=unreal.register_slate_post_tick_callback(_tick)
    unreal.log('Editor SUMO replay started. Recorded simulation, not live traffic. Use preview_traffic.stop() to clean up.')


def start_from_config():
    config=Path(__file__).resolve().parents[2]/'prototype_config.json'
    start(json.loads(config.read_text(encoding='utf-8'))['traffic_replay'])


def _tick(delta):
    elapsed=(time.monotonic()-_started)%(_times[-1]-_times[0])+_times[0]
    index=max(0,min(len(_frames)-2,bisect.bisect_right(_times,elapsed)-1))
    first,second=_frames[index:index+2]
    ratio=(elapsed-first['time_s'])/(second['time_s']-first['time_s'])
    after={v['id']:v for v in second['vehicles']}
    active={v['id'] for v in first['vehicles']}
    for identifier in list(_cars):
        if identifier not in active:
            actor=_cars.pop(identifier);actor.set_actor_location(unreal.Vector(0,0,-100000),False,False)
            _free.append(actor)
    for vehicle in first['vehicles']:
        identifier=vehicle['id'];next_vehicle=after.get(identifier,vehicle)
        position=[a+(b-a)*ratio for a,b in zip(vehicle['position'],next_vehicle['position'])]
        turn=(next_vehicle['heading_deg']-vehicle['heading_deg']+180)%360-180
        heading=math.radians(vehicle['heading_deg']+turn*ratio)
        # SUMO positions are at the front bumper; vehicle meshes are centred.
        direction=_basis[1]*math.sin(heading)+_basis[2]*math.cos(heading)
        where=_position(position)-direction*2.2+_basis[3]*(.03 if _vehicle_mesh else .7)
        if identifier not in _cars:
            _cars[identifier]=_free.pop() if _free else _spawn(identifier,_vehicle_path if _vehicle_mesh else '/Engine/BasicShapes/Cube.Cube')
            _cars[identifier].set_actor_scale3d(unreal.Vector(1,1,1) if _vehicle_mesh else unreal.Vector(4.4,1.8,1.4))
            if not _vehicle_mesh:_cars[identifier].static_mesh_component.set_material(0,_materials['car'])
            if _vehicle_mesh and _paints:
                paint=int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:2],'little')%len(_paints)
                # Reapply on assignment because pooled cars can acquire a new ID.
                _cars[identifier].static_mesh_component.set_material(_paint_slot,_paints[paint])
        actor=_cars[identifier]
        actor.set_actor_location(where,False,False)
        actor.set_actor_rotation(unreal.MathLibrary.make_rot_from_x(direction),False)
    # Signal lamps have explicit source states; no added red/amber phases.
    visible=set()
    for signal in first['signals']:
        key=(str(signal['junction']),signal['movement']);visible.add(key)
        if key not in _signals:
            _signals[key]=_spawn('signal '+str(key),'/Engine/BasicShapes/Sphere.Sphere')
            _signals[key].set_actor_scale3d(unreal.Vector(.4,.4,.4))
        state=signal['state'].lower()
        state=state if state in ('r','y','g') else 'off'
        if _signal_states.get(key)!=state:
            _signals[key].static_mesh_component.set_material(0,_materials[state])
            _signal_states[key]=state
            _signals[key].set_actor_label('DynaCITY preview signal '+str(key)+' state '+signal['state'])
        # Full pole geometry and movement arrows are a separate asset step.
        _signals[key].set_actor_location(_position(signal['position'])+_basis[3]*3.5,False,False)
    for key,actor in _signals.items():
        if key not in visible:actor.set_actor_location(unreal.Vector(0,0,-100000),False,False)
