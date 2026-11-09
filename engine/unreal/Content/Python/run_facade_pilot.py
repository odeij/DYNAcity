"""Bounded console entry point; writes errors for the single editor owner."""
import importlib.util
import json
from pathlib import Path
import traceback

TARGET=Path('C:/private/dynacity-data/engine/facade-modules-20261029/charles-malek-placements.json')
SCRIPT=Path('C:/Users/PC/DYNAcity/engine/unreal/Content/Python/install_facade_modules.py')
try:
    spec=importlib.util.spec_from_file_location('install_facade_modules',SCRIPT)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if not (TARGET.parent/'facade-install.json').exists():module.apply(str(TARGET))
    else:
        prior=json.loads((TARGET.parent/'facade-install.json').read_text())
        errors=prior.get('verification',{}).get('errors',[])
        if errors and all(value.startswith('Orientation mismatch ') for value in errors):
            module.repair_orientation(str(TARGET))
    result=module.verify(str(TARGET))
    (TARGET.parent/'facade-driver-result.json').write_text(json.dumps({'status':'pass','verification':result},indent=2))
except Exception:
    (TARGET.parent/'facade-driver-result.json').write_text(json.dumps({'status':'failed','error':traceback.format_exc()},indent=2))
    raise
