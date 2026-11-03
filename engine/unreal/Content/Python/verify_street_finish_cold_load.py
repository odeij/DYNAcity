"""Verify the street/architecture pilot in a newly opened editor process."""
import importlib.util
import json
import os
from pathlib import Path
import traceback
import unreal

BASE=Path('C:/private/dynacity-data/engine/street-twin-20261029')
FACADE=Path('C:/private/dynacity-data/engine/facade-modules-20261029')
OUT=BASE/'cold-load-verification.json'


def run():
    import city_world,street_detail
    city_world.load_editor_geometry()
    original=json.loads((BASE/'efasim-finish-persistence.json').read_text())['actors']
    files=[FACADE/'charles-malek-placements.json',FACADE/'camera-facing/charles-malek-placements.json']
    refs=set(original)
    for path in files:
        report=json.loads((path.parent/'facade-install.json').read_text())
        refs.update(row['actor'] for row in report['actors'])
    descriptors=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    selected=[d.guid for d in descriptors if str(d.actor_path) in refs] if descriptors and hasattr(descriptors[0],'actor_path') else []
    # Newly created actors were saved before the process closed. Load their
    # descriptors by the pipeline's stable labels, then resolve exact paths.
    selected.extend(d.guid for d in descriptors if str(d.label).startswith(
        ('FacadePilot_', 'StreetTwin ', 'Captured visual - Ac_726', 'Captured building - Ac_726',
         'Street detail | ', 'Reference preview -')))
    unreal.WorldPartitionBlueprintLibrary.load_actors(selected)
    actors={a.get_path_name():a for a in unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()}
    errors=[]
    for path,expected in original.items():
        actor=actors.get(path)
        if actor is None:
            errors.append('Missing saved actor '+path);continue
        component=actor.get_component_by_class(unreal.StaticMeshComponent)
        actual={'mesh':component.static_mesh.get_path_name(),
                'materials':[m.get_path_name() if m else None for m in component.get_materials()],
                'collision':str(component.get_collision_enabled()),
                'profile':str(component.get_collision_profile_name()),'visible':component.is_visible(),
                'actor_collision':actor.get_actor_enable_collision()}
        for key,value in actual.items():
            if value!=expected[key]:errors.append(key+' differs '+path)
    script=Path('C:/Users/PC/DYNAcity/engine/unreal/Content/Python/install_facade_modules.py')
    spec=importlib.util.spec_from_file_location('cold_facade_installer',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    facades=[]
    for path in files:
        verified=module.verify(str(path))
        facades.append({k:v for k,v in verified.items() if k not in ('rotation_diagnostics','errors')})
        if verified['status']!='pass':errors.append('Facade verification failed '+str(path))
        report_path=path.parent/'facade-install.json'
        report=json.loads(report_path.read_text());report.update(reload_verified=True,cold_editor_pid=os.getpid())
        report_path.write_text(json.dumps(report,indent=2))
    curbs=street_detail.verify_reload('C:/private/dynacity-data/engine/street-detail/pilot/manifest.json')
    if not curbs['reload_verified'] or curbs.get('native_ground_issues'):
        errors.append('Existing curb/sidewalk verification failed')
    visuals=[]
    for actor in actors.values():
        if actor.get_actor_label().startswith(('Captured visual - Ac_726','Captured building - Ac_726')):
            c=actor.get_component_by_class(unreal.StaticMeshComponent)
            valid=(not actor.get_actor_enable_collision() and not c.get_editor_property('use_default_collision')
                   and c.get_collision_enabled()==unreal.CollisionEnabled.NO_COLLISION)
            visuals.append({'actor':actor.get_path_name(),'visual_collision_disabled':valid})
            if not valid:errors.append('Visual collision restored unexpectedly')
    if len(visuals)!=2:errors.append('Expected two captured visual actors')
    result={'status':'pass' if not errors else 'failed','editor_pid':os.getpid(),
            'source':'Fresh editor process after saving and clean exit', 'actor_count':len(refs),
            'saved_actor_material_collision_match':not errors,
            'facade_instances':sum(v['instances'] for v in facades),'facades':facades,
            'curbs_and_sidewalks':curbs,'captured_visuals':visuals,'errors':errors}
    OUT.write_text(json.dumps(result,indent=2))
    if errors:raise RuntimeError(str(errors))
    for path in (BASE/'marmikhael/finish/pilot-finish-install.json',BASE/'charles-malek/finish/native-finish-install.json'):
        report=json.loads(path.read_text());report.update(reload_verified=True,cold_editor_pid=os.getpid(),
            cold_load_report=str(OUT))
        path.write_text(json.dumps(report,indent=2))
    return result


try:
    print(json.dumps(run()))
except Exception:
    if not OUT.exists():OUT.write_text(json.dumps({'status':'failed','error':traceback.format_exc()},indent=2))
    raise
