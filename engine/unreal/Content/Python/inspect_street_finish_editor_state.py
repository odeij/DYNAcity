"""Read-only diagnostics for scoped persistence and capture quality."""
import json
from pathlib import Path
import unreal

names = ('sg.ShadowQuality','sg.GlobalIlluminationQuality','sg.ReflectionQuality',
         'sg.PostProcessQuality','r.ScreenPercentage','r.AntiAliasingMethod')
value = {'dirty_packages':[p.get_path_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()],
         'quality':{n:unreal.SystemLibrary.get_console_variable_float_value(n) for n in names},
         'reload_api':unreal.EditorLoadingAndSavingUtils.reload_packages.__doc__}
Path('C:/private/dynacity-data/engine/street-twin-20261029/editor-state.json').write_text(json.dumps(value,indent=2))
