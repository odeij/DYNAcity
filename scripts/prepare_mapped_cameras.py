"""Create source-linked placement manifest for the licensed camera model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'engine' / 'unreal' / 'Content' / 'Python'))
from library_asset_core import read_prepared
from dynacity.engine_mapped_cameras import camera_placements

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('context', 'prepared', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(root): parser.error('Keep generated data outside the repository')
    raw = args.context.read_bytes(); asset = read_prepared(args.prepared)
    if asset['provenance'].get('id') != 'camera_pole_innerscene': raise ValueError('Unexpected asset')
    result = camera_placements(json.loads(raw))
    result.update(context_path=str(args.context.resolve()), context_sha256=hashlib.sha256(raw).hexdigest(),
                  prepared_path=str(args.prepared.resolve()), asset_fbx_sha256=asset['fbx_sha256'], asset_provenance=asset['provenance'],
                  model_facing='Blender negative Y; lens verified against dome geometry')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf8')
    print(json.dumps({'placements':len(result['placements']), 'output':str(args.output)}))

if __name__ == '__main__': main()
