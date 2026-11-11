"""Save only this task's dirty assets before a clean persistence check."""
import json
from pathlib import Path
import unreal

def save():
    levels=unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if levels.is_in_play_in_editor():
        raise RuntimeError('End the owned Play check first')
    dirty=unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()
    prefixes=('/Game/DynaCity/StreetTwinPilot/', '/Game/DynaCity/FacadeModules/',
              '/Game/Beirut/LibraryAssets/Materials/FacadeKit_')
    unknown=[p.get_path_name() for p in dirty if not p.get_path_name().startswith(prefixes)]
    if unknown:
        raise RuntimeError('Unrelated unsaved content prevents automatic reopen: '+str(unknown))
    if dirty and not unreal.EditorLoadingAndSavingUtils.save_packages(dirty,True):
        raise RuntimeError('Could not save task assets')
    if not levels.save_current_level():
        raise RuntimeError('Could not save actor descriptor changes')
    remaining=[p.get_path_name() for p in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()]
    if remaining:
        raise RuntimeError('Unsaved packages remain: '+str(remaining))
    result={'ready':True,'saved_task_packages':[p.get_path_name() for p in dirty],
            'unsaved_content':remaining,'quit_api':getattr(unreal.SystemLibrary,'quit_editor',None) is not None}
    Path('C:/private/dynacity-data/engine/street-twin-20261029/reopen-save.json').write_text(json.dumps(result,indent=2))
    return result

print(save())
