"""Single editor-owner entry point for the scoped reference test."""
import importlib.util
import json
from pathlib import Path
import traceback

ROOT=Path('C:/private/dynacity-data/engine/facade-reference-20261030')
try:
    path=Path('C:/Users/PC/DYNAcity/engine/unreal/Content/Python/install_reference_facade.py')
    spec=importlib.util.spec_from_file_location('install_reference_facade',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=module.apply()
    (ROOT/'driver-result.json').write_text(json.dumps({'status':'pass','result':result},indent=2))
except Exception:
    (ROOT/'driver-result.json').write_text(json.dumps({'status':'failed','error':traceback.format_exc()},indent=2))
    raise
