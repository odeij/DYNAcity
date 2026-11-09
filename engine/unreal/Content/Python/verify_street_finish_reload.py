"""Release only this integration's diagnostic references before reload testing."""
import importlib
import sys
from pathlib import Path

# Diagnostic Python files run in the console namespace. Their actor lists must
# not pin World Partition objects while explicitly testing unload/reload.
for _name in ('actors', 'actor', 'component', 'paint', 'selected'):
    globals().pop(_name, None)
_source = Path('C:/Users/PC/DYNAcity/engine/unreal/Content/Python')
_previous_path = list(sys.path)
sys.path.insert(0, str(_source))
import save_street_twin_finish
importlib.reload(save_street_twin_finish)
save_street_twin_finish.verify_reload('C:/private/dynacity-data/engine/street-twin-20261029/marmikhael/finish/pilot-finish-install.json')
sys.path[:] = _previous_path
