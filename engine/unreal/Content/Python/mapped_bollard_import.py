"""Place licensed bollards at mapped nodes; retain old source groups hidden."""
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import unreal

TAG = 'DYNACITY_FINISHED_BOLLARD'


def apply(filename):
    import city_world, explore_city, street_context, library_assets
    from library_asset_core import read_prepared
    city_world._current()
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before importing bollards')
    path = Path(filename).resolve(); data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 'dynacity-mapped-bollards/1': raise ValueError('Invalid bollard manifest')
    origin = json.loads(city_world._checkpoint().read_text(encoding='utf-8'))['origin_utm_m']
    if data['origin_utm_m'] != origin: raise ValueError('Bollard and city origins differ')
    if hashlib.sha256(Path(data['context_path']).read_bytes()).hexdigest() != data['context_sha256']:
        raise ValueError('Bollard source context changed')
    prepared = Path(data['prepared_path']); checked = read_prepared(prepared)
    if checked['fbx_sha256'] != data['asset_fbx_sha256'] or checked['provenance'].get('id') != 'steel_bollard_innerscene':
        raise ValueError('Bollard asset differs from manifest')
    rows=data['placements']; ids=set()
    for row in rows:
        position=row.get('position_local_m',[])
        if len(position)!=3 or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in position):
            raise ValueError('Invalid mapped bollard position')
        if not row.get('source_id','').startswith('node/') or row['source_id'] in ids:
            raise ValueError('Each bollard must have a unique mapped node ID')
        ids.add(row['source_id'])
    if not ids: raise ValueError('No mapped bollards')
    source=json.loads((city_world._root()/'Saved/DynaCity/street-instances.json').read_text(encoding='utf-8'))
    groups=defaultdict(set)
    for row in source['instances']:
        if row['kind']=='bollard': groups[row['actor']].add(row['source_id'])
    matched={actor: members for actor,members in groups.items() if members & ids}
    if any(not members <= ids for members in matched.values()) or set().union(*matched.values())!=ids:
        raise ValueError('Refusing to replace unmatched old source groups')
    street_context.load_editor_geometry();actors=city_world._actors()
    if any(TAG in actor.tags for actor in actors): raise RuntimeError('Finished bollard instances already exist')
    bypath={a.get_path_name():a for a in actors};old=[]
    for path_key,members in matched.items():
        actor=bypath.get(path_key)
        if actor is None or not {'street_kind:bollard'} <= {str(t) for t in actor.tags}:
            raise RuntimeError('Missing matching original bollard actor '+path_key)
        component=actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
        if component is None or actor.get_street_instance_count()!=len(members):
            raise RuntimeError('Original bollard instance group differs from source index')
        old.append((actor,component,component.is_visible(),component.get_editor_property('hidden_in_game')))
    imported=library_assets.import_prepared(prepared,'steel_bollard_innerscene');mesh=unreal.load_asset(imported['mesh'])
    bounds=mesh.get_bounding_box();center=(bounds.min+bounds.max)*.5
    cls=unreal.load_class(None,'/Script/DynaCityDrive.DynaStreetInstances')
    subsystem=unreal.get_editor_subsystem(unreal.EditorActorSubsystem);made=[]
    _,east,north,_=explore_city._basis();rotation=unreal.MathLibrary.make_rot_from_x(east)
    offset=unreal.MathLibrary.get_forward_vector(rotation)*center.x+unreal.MathLibrary.get_right_vector(rotation)*center.y
    byarea=defaultdict(list)
    for row in rows:byarea[row.get('production_area','Unassigned')].append(row)
    try:
        for area,items in sorted(byarea.items()):
            actor=subsystem.spawn_actor_from_class(cls,unreal.Vector());made.append(actor)
            actor.set_actor_label('Street_FinishedBollard_'+re.sub('[^A-Za-z0-9]+','_',area))
            actor.tags=[TAG];actor.set_folder_path('DynaCity/Finished mapped bollards/'+area)
            actor.set_editor_property('is_spatially_loaded',True)
            actor.set_editor_property('layer_kind','bollard');actor.set_editor_property('production_area',area)
            actor.set_editor_property('source_manifest',str(path));actor.set_street_mesh(mesh)
            actor.set_street_collision(False);actor.set_street_cull_distance(25000,70000)
            for row in items:
                transform=unreal.Transform(location=explore_city._world(row['position_local_m'])-offset,
                                           rotation=rotation,scale=unreal.Vector(1,1,1))
                if actor.add_street_instance(transform,row['source_id'])<0:raise RuntimeError('Bollard placement failed')
            if actor.get_street_instance_count()!=len(items):raise RuntimeError('Bollard count differs')
        for actor,component,_,_ in old:
            actor.modify();component.modify();component.set_visibility(False);component.set_hidden_in_game(True)
        if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():raise RuntimeError('World save failed')
        if not unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True):raise RuntimeError('Package save failed')
    except Exception:
        for actor,component,visible,hidden in old:component.set_visibility(visible);component.set_hidden_in_game(hidden)
        for actor in made:subsystem.destroy_actor(actor)
        raise
    report={'schema':'dynacity-finished-bollard-import/1','manifest':str(path),'asset':imported,
            'actors':[a.get_path_name() for a in made],'instances':len(rows),
            'replaced':[{'actor':a.get_path_name(),'visible':v,'hidden_in_game':h} for a,c,v,h in old]}
    (city_world._root()/'Saved/DynaCity/finished-bollard-import.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
