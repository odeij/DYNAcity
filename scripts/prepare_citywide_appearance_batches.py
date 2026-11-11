"""Make deterministic whole-building passes from the current staged assignments."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import time


def prepare(plan_root, output, buildings_per_pass=3500):
    if buildings_per_pass<1:raise ValueError('Pass size must be positive')
    started=time.perf_counter();plan_root=Path(plan_root);output=Path(output)
    latest=json.loads((plan_root/'latest-run.json').read_text());run=Path(latest['run_directory'])
    records=[];sources={};seen=set()
    for path in sorted(run.glob('*.json')):
        raw=path.read_bytes();data=json.loads(raw)
        if 'tile' not in data or 'records' not in data:continue
        sources[str(path)]=hashlib.sha256(raw).hexdigest()
        for record in data['records']:
            if record['source_id'] in seen:raise ValueError('Duplicate building across owner tiles')
            if record['owner_tile']!=data['tile']:raise ValueError('Owner tile mismatch')
            seen.add(record['source_id'])
            records.append((record,path))
    expected=json.loads((run/'report.json').read_text())['buildings']
    if len(records)!=expected:raise ValueError('Incomplete staged inventory')
    # Evidence updates cannot reorder pass membership. No building is split.
    records.sort(key=lambda item:(item[0]['district'],item[0]['owner_tile'],item[0]['source_id']))
    config={'schema':1,'plan':latest['fingerprint'],'pass_size':buildings_per_pass,'sources':sources}
    fingerprint=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()[:16]
    root=output/'runs'/fingerprint;root.mkdir(parents=True,exist_ok=True);passes=[]
    for offset in range(0,len(records),buildings_per_pass):
        selected=records[offset:offset+buildings_per_pass];number=len(passes)+1
        entries=[{'source_id':record['source_id'],'owner_tile':record['owner_tile'],
                  'district':record['district'],'assignment_manifest':str(path),
                  'assignment_manifest_sha256':sources[str(path)],
                  'facade_status':record['facade']['status'],
                  'has_photo_candidate':bool(record['street_candidates'])} for record,path in selected]
        historical=sum(e['facade_status']=='observed_historical_visible_portion' for e in entries)
        manifest={'schema':'dynacity-appearance-building-pass/1','pass_number':number,'buildings':len(entries),
            'status':'prepared_not_applied','estimated_facades':len(entries)-historical,
            'historical_visible_facades':historical,'photo_candidate_buildings':sum(e['has_photo_candidate'] for e in entries),
            'records':entries,'limits':['Batch size is processing capacity, not confirmed photo coverage.',
            'Assignment manifests are staged; this command does not import or approve facades.']}
        path=root/f'pass-{number:02}.json';payload=json.dumps(manifest,indent=2)
        if not path.exists() or path.read_text()!=payload:path.write_text(payload)
        passes.append({k:v for k,v in manifest.items() if k not in ['records','limits']})
    report={'status':'prepared_not_applied','buildings':len(records),'buildings_per_pass':buildings_per_pass,
        'passes':passes,'unreal_applied_buildings':0,'seconds':time.perf_counter()-started,
        'source_plan':str(run),'fingerprint':fingerprint,'input_manifest_checksums':sources}
    (root/'report.json').write_text(json.dumps(report,indent=2))
    rows=''.join(f'<tr><td><a href="pass-{p["pass_number"]:02}.json">Pass {p["pass_number"]}</a></td><td>{p["buildings"]:,}</td><td>{p["estimated_facades"]:,}</td><td>{p["historical_visible_facades"]}</td><td>{p["photo_candidate_buildings"]}</td><td>Prepared</td></tr>' for p in passes)
    page=f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Beirut appearance processing passes</title><style>body{{font:16px/1.6 "Segoe UI",Arial,sans-serif;background:#f4f3ed;color:#203b30;margin:30px auto;padding:24px;max-width:1200px}}h1{{font-size:40px;line-height:1.1}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{text-align:left;padding:14px;border-bottom:1px solid #ddd}}a{{color:#176652}}.notice{{background:#fff0d8;padding:18px;border-left:4px solid #b58132}}</style><a href="../../../appearance-evidence-20261030/beirut-work-and-evidence.html">← Work and evidence</a><h1>{len(records):,} buildings in {len(passes)} processing passes</h1><p>Up to {buildings_per_pass:,} whole buildings per pass; the final pass contains the remainder. Every building has exactly one pass and keeps its owner tile.</p><p class="notice"><strong>Prepared, not applied to Unreal.</strong> These are processing batches. They do not establish photographic coverage of 3,500 buildings. Estimated appearances, historical observations and geometric photo candidates remain separate.</p><table><tr><th>Pass</th><th>Buildings</th><th>Estimated</th><th>Historical observation</th><th>Photo candidates</th><th>Status</th></tr>{rows}</table><p><a href="report.json">Report and input checksums</a> · <a href="{Path(latest['page']).as_uri()}">Source assignments and photos</a></p>'''
    (root/'index.html').write_text(page,encoding='utf-8')
    pointer={'fingerprint':fingerprint,'run_directory':str(root),'page':str(root/'index.html'),'report':str(root/'report.json')}
    (output/'latest-run.json').write_text(json.dumps(pointer,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='input_manifest_checksums'}))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-root',default='C:/private/dynacity-data/engine/appearance-plan-20261030')
    parser.add_argument('--output',default='C:/private/dynacity-data/engine/appearance-passes-20261030')
    parser.add_argument('--buildings-per-pass',type=int,default=3500)
    args=parser.parse_args();prepare(args.plan_root,args.output,args.buildings_per_pass)
