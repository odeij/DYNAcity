"""Read-only native inventory for the street-finish integration checkpoint."""
import json
from pathlib import Path
import traceback
import unreal

OUT = Path('C:/private/dynacity-data/engine/street-twin-20261029/native-inventory.json')
try:
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    actors = subsystem.get_all_level_actors()
    rows = []
    for actor in actors:
        label = actor.get_actor_label()
        if not any(k in label.lower() for k in ('street detail', 'reference preview', 'streettwin', 'street_', 'traffic')):
            continue
        row = {'label': label, 'path': actor.get_path_name(), 'tags': [str(t) for t in actor.tags]}
        if hasattr(actor, 'get_street_instance_count'):
            row['instances'] = actor.get_street_instance_count()
        rows.append(row)
    root = Path(unreal.Paths.project_dir()).resolve()
    reports = {}
    for name in ('street-detail-import.json', 'street-detail-reload.json', 'library-assets-active.json',
                 'mobility-native-check.json', 'mobility-native-status.json', 'street-detail-finish.json'):
        path = root / 'Saved/DynaCity' / name
        if path.exists():
            reports[name] = json.loads(path.read_text())
    OUT.write_text(json.dumps({'project': str(root), 'actor_count': len(actors), 'actors': rows,
                              'reports': reports}, indent=2))
except Exception:
    OUT.write_text(json.dumps({'error': traceback.format_exc()}, indent=2))
    raise
