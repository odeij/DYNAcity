"""Close the fully saved editor for the authorized persistence check."""
import json
from pathlib import Path
import unreal

ready=Path('C:/private/dynacity-data/engine/street-twin-20261029/reopen-save.json')
if not json.loads(ready.read_text()).get('ready'):
    raise RuntimeError('The scoped save checkpoint did not complete')
if unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages() or unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages():
    raise RuntimeError('New unsaved changes prevent a clean reopen')
unreal.SystemLibrary.quit_editor()
