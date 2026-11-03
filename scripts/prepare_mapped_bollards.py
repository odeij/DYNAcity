"""Stage mapped bollard node placements for a validated finished asset."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'engine' / 'unreal' / 'Content' / 'Python'))
from library_asset_core import read_prepared
from dynacity.engine_mapped_bollards import bollard_placements


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--context', type=Path, required=True)
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.resolve().is_relative_to(root):
        p.error('Keep generated source-linked data outside the repository')
    raw = args.context.read_bytes()
    data = read_prepared(args.prepared)
    if data['provenance'].get('id') != 'steel_bollard_innerscene':
        raise ValueError('Expected the validated generic steel bollard')
    result = bollard_placements(json.loads(raw))
    result.update(context_path=str(args.context.resolve()), context_sha256=hashlib.sha256(raw).hexdigest(),
                  prepared_path=str(args.prepared.resolve()), asset_fbx_sha256=data['fbx_sha256'],
                  asset_provenance=data['provenance'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps({'placements': len(result['placements']), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
