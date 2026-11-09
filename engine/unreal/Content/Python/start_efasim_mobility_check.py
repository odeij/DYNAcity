"""Load the scoped updated adapter and start an owned native mobility check."""
import importlib
import sys
from pathlib import Path

import unreal

folder = Path(unreal.Paths.project_dir()).resolve() / 'Content/Python'
source = Path('C:/Users/PC/DYNAcity/engine/unreal/Content/Python').resolve()
# Runtime modules resolve the generated project's private configuration beside
# Content. Do not reload them from the repository's source-only directory.
sys.path[:] = [entry for entry in sys.path if Path(entry or '.').resolve() != source]
sys.path.insert(0, str(folder))
import verify_efasim_mobility
importlib.reload(verify_efasim_mobility)
print(verify_efasim_mobility.start())
