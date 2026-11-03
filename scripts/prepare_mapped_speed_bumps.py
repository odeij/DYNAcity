"""Stage source-linked speed bump placements for the finished CC0 model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'engine' / 'unreal' / 'Content' / 'Python'))
from library_asset_core import read_prepared
from dynacity.engine_mapped_speed_bumps import speed_bump_placements


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('context', 'prepared', 'output'): p.add_argument('--'+name, type=Path, required=True)
    args = p.parse_args()
    if args.output.resolve().is_relative_to(root): p.error('Keep generated data outside the repository')
    raw = args.context.read_bytes(); asset = read_prepared(args.prepared)
    if asset['provenance'].get('id') != 'speed_bump_innerscene': raise ValueError('Expected the speed bump model')
    bounds = asset['bounds_blender_cm']; length = (bounds['max'][0] - bounds['min'][0]) / 100
    result = speed_bump_placements(json.loads(raw), length)
    result.update(context_path=str(args.context.resolve()), context_sha256=hashlib.sha256(raw).hexdigest(),
                  prepared_path=str(args.prepared.resolve()), asset_fbx_sha256=asset['fbx_sha256'], asset_provenance=asset['provenance'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps({'placements': len(result['placements']), 'skipped': len(result['skipped']), 'output': str(args.output)}))

if __name__ == '__main__': main()
