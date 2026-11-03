"""Integrate verified paint and licensed finishes with the existing pilot street."""
import json
from pathlib import Path
import traceback
import unreal

ROOT = Path('C:/private/dynacity-data/engine/street-twin-20261029')
REPORT = ROOT / 'charles-malek/finish/native-finish-install.json'
TAG = 'DYNACITY_CHARLES_MALEK_FINISH_20261029'


def signature(actor):
    component = actor.get_component_by_class(unreal.StaticMeshComponent)
    p,r,s = actor.get_actor_location(),actor.get_actor_rotation(),actor.get_actor_scale3d()
    return [[p.x,p.y,p.z,r.pitch,r.yaw,r.roll,s.x,s.y,s.z], component.static_mesh.get_path_name(),
            str(component.get_collision_enabled()), str(component.get_collision_profile_name())]


def apply():
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError('End Play before street finish installation')
    if unreal.InterchangeManager.get_interchange_manager_scripted().is_interchange_active():
        raise RuntimeError('Wait for the active import')
    pbr = json.loads((ROOT / 'marmikhael/finish/pbr-materials.json').read_text())
    materials = {k: unreal.load_asset(v['refPath']) for k,v in pbr['materials'].items()}
    if any(m is None for m in materials.values()):
        raise RuntimeError('Verified finish materials are missing')
    editor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    descriptors = unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descriptors if
        str(d.label).startswith(('roads_achrafieh_', 'roads_remeil_', 'Street detail | ', 'StreetTwin Charles Malek paint'))])
    actors = editor.get_all_level_actors()
    backup_path = REPORT.with_name('native-finish-original-materials.json')
    backup = json.loads(backup_path.read_text()) if backup_path.exists() else {}
    rows, touched, paint_rows = [], [], []
    for actor in actors:
        label = actor.get_actor_label()
        kind = None
        if label.startswith(('roads_achrafieh_', 'roads_remeil_')):
            kind = 'Asphalt' if label.endswith('_material_1') else 'Paving' if label.endswith('_material_2') else None
        if kind is None:
            continue
        component = actor.get_component_by_class(unreal.StaticMeshComponent)
        old = signature(actor)
        path = actor.get_path_name()
        backup.setdefault(path, {'label':label, 'materials':[m.get_path_name() if m else None for m in component.get_materials()]})
        backup_path.write_text(json.dumps(backup, indent=2))
        actor.modify(); component.modify()
        component.set_material(0, materials[kind])
        if signature(actor) != old:
            raise RuntimeError('Source geometry or collision changed')
        rows.append({'actor':path,'label':label,'kind':kind,'geometry_and_collision_unchanged':True})
        touched.append(actor)
    for tile in ('2932', '2933'):
        imported = json.loads((REPORT.parent / ('import-'+tile+'.json')).read_text())
        if imported['status'] != 'pass' or not imported['triangle_match'] or not imported['bounds_match']:
            raise RuntimeError('Paint import failed verification')
        mesh_path = imported['meshes'][0]['asset']
        mesh = unreal.load_asset(mesh_path)
        mesh.set_material(0, materials['Paint'])
        if not unreal.EditorAssetLibrary.save_loaded_asset(mesh):
            raise RuntimeError('Could not save paint material')
        label = 'StreetTwin Charles Malek paint ' + tile
        matches = [a for a in actors if a.get_actor_label() == label and TAG in a.tags]
        if len(matches) > 1:
            raise RuntimeError('Duplicate paint actors')
        actor = matches[0] if matches else editor.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector())
        actor.modify()
        actor.set_actor_label(label)
        actor.tags = [TAG, 'ILLUSTRATIVE_MARKINGS']
        actor.set_folder_path('DynaCity/Street twin finishes/Charles Malek')
        actor.set_editor_property('is_spatially_loaded', True)
        component = actor.static_mesh_component
        component.modify(); component.set_static_mesh(mesh)
        component.set_editor_property('use_default_collision',False)
        component.set_collision_profile_name('NoCollision')
        component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        actor.set_actor_enable_collision(False)
        component.set_material(0, materials['Paint'])
        component.set_visibility(True)
        actor.set_actor_location(unreal.Vector(), False, False)
        paint_rows.append({'actor':actor.get_path_name(),'mesh':mesh_path,'triangles':imported['expected_triangles']})
        touched.append(actor)
    packages = list({a.get_package().get_path_name():a.get_package() for a in touched}.values())
    if not unreal.EditorLoadingAndSavingUtils.save_packages(packages, True):
        raise RuntimeError('Scoped finish save failed')
    report = {'status':'saved', 'source_materials':rows,'paint':paint_rows,
              'saved_packages':[p.get_path_name() for p in packages], 'reload_verified':False,
              'limits':['Illustrative paint, colours and furniture; not a street survey.',
                        'Source road material overrides cover whole existing district batches.',
                        'Existing source geometry and collision retained.']}
    REPORT.write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    try:
        print(json.dumps(apply()))
    except Exception:
        REPORT.write_text(json.dumps({'status':'failed','error':traceback.format_exc()}, indent=2))
        raise
