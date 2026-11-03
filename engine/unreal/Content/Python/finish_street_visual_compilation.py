"""Complete pending asset compilation before inspecting the persisted scene."""
import json
from pathlib import Path
import traceback
import unreal

out=Path('C:/private/dynacity-data/engine/street-twin-20261029/visual-compilation.json')
try:
    world=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    # UE 5.8 exposes this registered console command, but does not expose
    # FAssetCompilingManager as a Python class. Verified against local source.
    unreal.SystemLibrary.execute_console_command(world,'Editor.AsyncAssetCompilationFinishAll')
    unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_invalidate_viewports()
    out.write_text(json.dumps({'status':'pass','pending_assets_finished':True}))
except Exception:
    out.write_text(json.dumps({'status':'failed','error':traceback.format_exc(),
        'available_apis':[n for n in dir(unreal) if 'Compil' in n]}))
    raise
