"""Install fitted replacement first, verify it, then hide only the old owned scopes."""
import importlib.util
import json
import os
from pathlib import Path
import time
import traceback
import unreal
import city_world

root=Path('C:/private/dynacity-data/engine/facade-coverage-20261030')
target=root/'placements.json'
start=time.perf_counter()
try:
    spec=importlib.util.spec_from_file_location('facade_repair_install',Path('C:/Users/PC/DYNAcity/engine/unreal/Content/Python/install_facade_modules.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    layout=json.loads(target.read_text())
    descs=unreal.WorldPartitionBlueprintLibrary.get_actor_descs() or []
    unreal.WorldPartitionBlueprintLibrary.load_actors([d.guid for d in descs if str(d.label).startswith(('FacadePilot_','buildings_'))])
    old=[]
    for filename in layout['superseded_manifests']:
        report=json.loads((Path(filename).parent/'facade-install.json').read_text())
        for group in report['actors']:
            actor=unreal.load_object(None,group['actor'])
            if actor is None or module.TAG not in actor.tags:raise RuntimeError('Missing owned original scope')
            comp=actor.get_component_by_class(unreal.HierarchicalInstancedStaticMeshComponent)
            if [str(v) for v in actor.get_editor_property('source_ids')]!=group['source_ids']:raise RuntimeError('Original source IDs changed')
            old.append(actor)
    if not (root/'facade-install.json').exists():module.apply(str(target))
    verification=module.verify(str(target))
    before=[{'actor':a.get_path_name(),'visible':a.get_component_by_class(unreal.StaticMeshComponent).is_visible(),
             'hidden_in_game':a.get_component_by_class(unreal.StaticMeshComponent).get_editor_property('hidden_in_game')} for a in old]
    backup=root/'superseded-state-before.json'
    if backup.exists() and any(row['visible'] or not row['hidden_in_game'] for row in before):
        raise RuntimeError('Superseded scope visibility did not persist; restore final comparison mode before rerunning')
    if not backup.exists():backup.write_text(json.dumps(before,indent=2))
    for actor in old:
        actor.modify();comp=actor.get_component_by_class(unreal.StaticMeshComponent);comp.modify()
        comp.set_visibility(False,True);comp.set_hidden_in_game(True,True)
    if not unreal.EditorLoadingAndSavingUtils.save_packages([a.get_outermost() for a in old],True):raise RuntimeError('Old scope visibility save failed')
    if any(a.get_component_by_class(unreal.StaticMeshComponent).is_visible() for a in old):raise RuntimeError('Old scope still visible')
    result={'status':'pass','editor_pid':os.getpid(),'verification':verification,'superseded_actors':[a.get_path_name() for a in old],
            'old_instances_retained_for_rollback':True,'seconds':time.perf_counter()-start}
    (root/'repair-result.json').write_text(json.dumps(result,indent=2))
except Exception:
    (root/'repair-result.json').write_text(json.dumps({'status':'failed','error':traceback.format_exc()},indent=2))
    raise
