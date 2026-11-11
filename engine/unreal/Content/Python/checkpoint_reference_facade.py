"""Save only reference actors; preserve unrelated dirty packages in a recovery copy."""
import hashlib
import json
import os
from pathlib import Path
import unreal
import city_world

root = Path('C:/private/dynacity-data/engine/facade-reference-20261030')
report = json.loads((root/'native-install.json').read_text())
actors = [unreal.load_object(None, path) for path in report['actors']+[report['source_actor']]]
if any(actor is None for actor in actors):
    raise RuntimeError('Missing reference actor')
if not unreal.EditorLoadingAndSavingUtils.save_packages([a.get_outermost() for a in actors], True):
    raise RuntimeError('Scoped save failed')
dirty = list(unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()) + list(unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages())
backup = root/'live-recovery-backup'
backup.mkdir(exist_ok=True)
records = []
for index, package in enumerate(dirty):
    path = backup/(str(index).zfill(3)+'.uasset')
    # Native OBJ SAVEPACKAGE supports an alternate filename and keeps the dirty flag.
    command = f'OBJ SAVEPACKAGE PACKAGE="{package.get_path_name()}" FILE="{path.as_posix()}" SILENT=true AUTOSAVING=true KEEPDIRTY=true'
    unreal.SystemLibrary.execute_console_command(city_world._current(), command)
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError('Recovery copy failed: '+package.get_path_name())
    records.append({'package':package.get_path_name(), 'file':str(path), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
(root/'restart-checkpoint.json').write_text(json.dumps({'status':'ready', 'editor_pid':os.getpid(), 'scoped_actor_count':len(actors), 'recovery_copies':records}, indent=2))
