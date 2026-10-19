"""Build a local coupling runtime from an existing official EPA 5.2.4 tree.

Usage: python scripts/build_swmm_library.py --source /path/official/source --output /private/runtime/libswmm.dylib
The source tree is not downloaded or modified. A source hash manifest is retained.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
solver = args.source.resolve() / 'src/solver'
bridge = Path(__file__).resolve().parents[1] / 'src/dynacity/swmm_bridge.c'
files = sorted(solver.glob('*.c'))
if not files or '#define   VERSION            52004' not in (solver / 'consts.h').read_text():
    raise SystemExit('Official EPA SWMM 5.2.4 source required')
if args.output.exists():
    raise SystemExit('Output already exists')
args.output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(['cc', '-dynamiclib' if sys.platform == 'darwin' else '-shared', '-fPIC', '-O2',
                '-I' + str(solver), '-I' + str(solver / 'include'),
                *map(str, files), str(bridge), '-lm', '-o', str(args.output)], check=True)
hashes = {str(p.relative_to(args.source.resolve())): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted(solver.rglob('*')) if p.is_file() and p.suffix in {'.c', '.h'}}
manifest = {'engine': 'EPA SWMM 5.2.4', 'source_files_sha256': hashes,
            'bridge_sha256': hashlib.sha256(bridge.read_bytes()).hexdigest(),
            'library_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
            'official_source': 'https://github.com/USEPA/Stormwater-Management-Model/tree/v5.2.4'}
args.output.with_suffix('.build.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(args.output)
