"""Create a source-linked, finished-model barrier placement manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'engine'/'unreal'/'Content'/'Python'))
from library_asset_core import read_prepared
from dynacity.engine_barrier_placements import barrier_placements


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',required=True,type=Path)
    parser.add_argument('--prepared',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(root):
        parser.error('Save generated source-linked manifests outside the repository')
    raw=args.context.read_bytes();context=json.loads(raw)
    if context.get('schema')!='dynacity-engine-street-context/1':
        raise ValueError('Expected a mapped street context manifest')
    asset=read_prepared(args.prepared)
    if asset['provenance'].get('id')!='concrete_road_barrier':
        raise ValueError('Expected the approved concrete_road_barrier model')
    bounds=asset['bounds_blender_cm'];length=(bounds['max'][0]-bounds['min'][0])/100
    result=barrier_placements(context,length)
    result.update(context_path=str(args.context.resolve()),context_sha256=hashlib.sha256(raw).hexdigest(),
        prepared_path=str(args.prepared.resolve()),asset_fbx_sha256=asset['fbx_sha256'],
        asset_provenance=asset['provenance'],
        model_bounds_center_blender_m=[(bounds['max'][i]+bounds['min'][i])/200 for i in range(2)],
        placement_position_semantics='centre of source segment at ground; align model bounds centre before placement')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'placements':len(result['placements']),'omitted':len(result['omitted']),
                      'output':str(args.output)}))


if __name__=='__main__':main()
