"""Generate a source-linked manifest for the finished power tower model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'engine'/'unreal'/'Content'/'Python'))
from library_asset_core import read_prepared
from dynacity.engine_mapped_power_towers import power_tower_placements

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('context','prepared','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(root):parser.error('Keep generated data outside the repository')
    raw=args.context.read_bytes();asset=read_prepared(args.prepared)
    if asset['provenance'].get('id')!='power_tower_grab3d':raise ValueError('Expected power tower model')
    data=power_tower_placements(json.loads(raw))
    data.update(context_path=str(args.context.resolve()),context_sha256=hashlib.sha256(raw).hexdigest(),
        prepared_path=str(args.prepared.resolve()),asset_fbx_sha256=asset['fbx_sha256'],asset_provenance=asset['provenance'])
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(data,indent=2),encoding='utf8')
    print(json.dumps({'placements':len(data['placements']),'skipped':len(data['skipped']),'output':str(args.output)}))

if __name__=='__main__':main()
