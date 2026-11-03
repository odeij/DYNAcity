"""Run through Unreal's PythonScript commandlet in the private target project.

Creates only the five versioned ambient materials. Does not load or save a city
map. Native signal behavior is checked separately by Unreal automation.
"""
import json
from pathlib import Path
import sys
import unreal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python'))
import ambient_effects

clouds, lamps = ambient_effects.prepare_assets()
paths = [material.get_path_name() for material in [clouds, *lamps]]
if len(set(paths)) != 5 or any(not unreal.EditorAssetLibrary.does_asset_exist(p) for p in paths):
    raise RuntimeError('Missing or duplicate ambient material assets')
output = Path('C:/private/dynacity-data/engine/ambient-effects-20261030')
output.mkdir(parents=True, exist_ok=True)
(output/'asset-verification.json').write_text(json.dumps({
    'materials': paths, 'asset_creation_passed': True,
    'rendered_appearance_verified': False,
    'scope': 'Versioned material creation/reuse, not a full-city visual or GPU benchmark',
}, indent=2), encoding='utf-8')
print('DYNACITY_AMBIENT_ASSETS_PASSED ' + json.dumps(paths))
