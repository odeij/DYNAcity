"""Improve daytime ambient fill so shaded road details remain visible."""
import json,traceback,unreal,city_world
root=city_world._root()/'Saved/DynaCity';out=root/'road-daylight-fill-20261028.json';backup=root/'road-daylight-fill-before-20261028.json'
try:
 city_world._current();skies=[a for a in city_world._actors() if isinstance(a,unreal.SkyLight)]
 if len(skies)!=1:raise RuntimeError('Expected one city skylight; found '+str(len(skies)))
 sky=skies[0];fill=sky.get_component_by_class(unreal.SkyLightComponent)
 if backup.exists():raise RuntimeError('Existing backup; inspect previous run')
 backup.write_text(json.dumps({'actor':sky.get_path_name(),'intensity':fill.get_editor_property('intensity')},indent=2))
 sky.modify();fill.modify();fill.set_intensity(8.0);fill.recapture_sky()
 if not unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level():raise RuntimeError('Level save failed')
 out.write_text(json.dumps({'success':True,'actor':sky.get_path_name(),'old_intensity':json.loads(backup.read_text())['intensity'],'new_intensity':8.0,'note':'Daytime skylight fill; night mode uses its own intensity.'},indent=2))
except Exception:out.write_text(json.dumps({'success':False,'error':traceback.format_exc()},indent=2));raise
