"""Prepare mapped container placements using a finished recycling asset."""
import argparse, hashlib, json, sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'engine'/'unreal'/'Content'/'Python'))
from library_asset_core import read_prepared
from dynacity.engine_mapped_recycling import recycling_placements

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('context','prepared','output'): p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args()
    if a.output.resolve().is_relative_to(root):p.error('Keep generated data outside repository')
    raw=a.context.read_bytes(); asset=read_prepared(a.prepared)
    if asset['provenance'].get('id')!='recycling_innerscene':raise ValueError('Unexpected asset')
    result=recycling_placements(json.loads(raw))
    result.update(context_path=str(a.context.resolve()),context_sha256=hashlib.sha256(raw).hexdigest(),
                  prepared_path=str(a.prepared.resolve()),asset_fbx_sha256=asset['fbx_sha256'],asset_provenance=asset['provenance'])
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({'placements':len(result['placements']),'output':str(a.output)}))
if __name__=='__main__':main()
