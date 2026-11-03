"""Offline, resumable appearance assignments and geometric photo review queue.

Reads a snapshot of cached collection artifacts; never downloads, approves
roof candidates, infers wall color from roofs, or writes to Unreal.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import html
import json
import math
from pathlib import Path
import time
from pyproj import Geod, Transformer
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from dynacity.street_photo_visibility import visible_wall_samples


def digest(data):return hashlib.sha256(data).hexdigest()


def prepare(collection, output):
    started=time.perf_counter();collection=Path(collection);output=Path(output)
    latest=json.loads((collection/'latest-run.json').read_text());run=Path(latest['run_directory'])
    inventory_bytes=(run/'inventory.json').read_bytes();inventory=json.loads(inventory_bytes)
    records=inventory['records'];ids={r['source_id'] for r in records}
    if len(ids)!=len(records):raise ValueError('Duplicate source ownership')
    for source in inventory['sources']:
        if digest(Path(source['index']).read_bytes())!=source['sha256']:raise ValueError('Source index changed')
    roofs={};inputs={'inventory':digest(inventory_bytes),'tiles':{},'street':{}}
    for path in sorted((run/'tiles').glob('*/evidence.json')):
        raw=path.read_bytes();checkpoint=json.loads((path.parent/'result.json').read_text())
        if digest(raw)!=checkpoint['evidence_sha256']:raise ValueError('Roof cache hash mismatch')
        inputs['tiles'][path.parent.name]=digest(raw)
        for record in json.loads(raw)['records']:
            if record['source_id'] in roofs:raise ValueError('Duplicate roof owner')
            roofs[record['source_id']]=record['roof']
    if set(roofs)!=ids:raise ValueError('Roof collection is not complete')
    observations_file=collection.parent/'appearance-evidence-20261030/reviewed-observations.json'
    observations_bytes=observations_file.read_bytes();inputs['reviewed']=digest(observations_bytes)
    observations={o['source_id']:o for o in json.loads(observations_bytes)['observations'] if o['identity_status']=='corroborated'}
    photos={};photo_rows={}
    for path in sorted((run/'street').glob('*/result.json')):
        raw=path.read_bytes();result=json.loads(raw)
        query_path=path.parent/'street-query.json'
        if not query_path.exists():continue
        query_bytes=query_path.read_bytes()
        if digest(query_bytes)!=result['query_sha256']:raise ValueError('Street query changed')
        inputs['street'][path.parent.name]={'result':digest(raw),'query':digest(query_bytes)}
        rows={str(r['id']):r for r in json.loads(query_bytes)['response']['result']['data']}
        for photo in result['photos']:
            image=path.parent/photo['file']
            if digest(image.read_bytes())!=photo['sha256']:raise ValueError('Photo bytes changed')
            pid=str(photo['id']);row=rows.get(pid)
            photos[pid]={**photo,'path':str(image)}
            if row is not None:photo_rows[pid]=row
    # Query only nearby source polygons, then use heading and occlusion screening.
    polygons=[shape(r['footprint']) for r in records];tree=STRtree(polygons)
    convert=Transformer.from_crs('EPSG:4326',records[0]['crs'],always_xy=True);geod=Geod(ellps='WGS84')
    candidates=defaultdict(list);skipped=[]
    for pid,row in sorted(photo_rows.items()):
        try:
            if row.get('projection')!='PLANE':raise ValueError('Unsupported projection')
            lon,lat,heading=map(float,(row['lng'],row['lat'],row['heading']))
            if not all(math.isfinite(v) for v in (lon,lat,heading)):raise ValueError('Invalid camera coordinates')
        except (ValueError,TypeError,KeyError) as error:
            skipped.append({'photo_id':pid,'reason':str(error)});continue
        xy=convert.transform(lon,lat);lon2,lat2,_=geod.fwd(lon,lat,heading,100);x2,y2=convert.transform(lon2,lat2)
        grid_heading=math.degrees(math.atan2(x2-xy[0],y2-xy[1]))%360
        for index in tree.query(Point(xy).buffer(80)):
            outcome=visible_wall_samples(xy,grid_heading,polygons[index],tree)
            if not outcome['samples']:continue
            best=min(outcome['samples'],key=lambda s:abs(s['heading_offset_deg'])+abs(s['distance_m']-25)*.2)
            candidates[records[index]['source_id']].append({'photo_id':pid,'camera_xy_m':list(xy),
                'heading_grid_deg':grid_heading,'rank_cost':abs(best['heading_offset_deg'])+abs(best['distance_m']-25)*.2,
                'wall_sample':best,'status':'geometry_candidate_needs_review','identity_confirmed':False})
    palette=['cream_plaster','warm_stone','grey_concrete','muted_pink_plaster']
    tiles=defaultdict(list)
    for record in records:
        source_id=record['source_id'];observed=observations.get(source_id)
        selected=sorted(candidates[source_id],key=lambda c:(c['rank_cost'],c['photo_id']))[:3]
        assignment={'source_id':source_id,'district':record['district'],'owner_tile':record['owner_tile'],
            'source_index':record['source_index'],'source_metadata':record['source_metadata'],
            'facade':{'status':'observed_historical_visible_portion' if observed else 'estimated',
                'template':'reference_review_required' if observed else palette[int.from_bytes(hashlib.sha256(source_id.encode()).digest()[:2],'little')%len(palette)],
                'reason':'Reviewed historical observation; exact geometry remains estimated' if observed else 'Stable illustrative palette, not inferred from roof color',
                'observation':observed},
            'roof':roofs[source_id],'street_candidates':selected,
            'unreal_application_status':'staged_only; this command does not import'}
        tiles[record['owner_tile']].append(assignment)
    inputs['generator']=digest(Path(__file__).read_bytes());fingerprint=digest(json.dumps(inputs,sort_keys=True).encode())[:16]
    root=output/'runs'/fingerprint;root.mkdir(parents=True,exist_ok=True)
    (root/'inputs.json').write_text(json.dumps(inputs,indent=2));written=0;reused=0
    for tile,assignments in sorted(tiles.items()):
        path=root/(tile.replace(':','_')+'.json');payload=json.dumps({'tile':tile,'records':sorted(assignments,key=lambda r:r['source_id'])},sort_keys=True)
        if path.exists() and path.read_text()==payload:reused+=1
        else:path.write_text(payload);written+=1
    counts=Counter(roof['status'] for roof in roofs.values())
    report={'status':'staged','buildings':len(records),'tiles':len(tiles),'observed_historical_facades':sum(i in observations for i in ids),
        'estimated_facades':sum(i not in observations for i in ids),'roof_status_counts':dict(counts),
        'cached_unique_photos':len(photos),'buildings_with_geometric_photo_candidates':sum(bool(candidates[i]) for i in ids),
        'photo_candidate_pairs':sum(len(c) for c in candidates.values()),'skipped_camera_metadata':skipped,
        'written_tiles':written,'reused_tiles':reused,'seconds':time.perf_counter()-started,'fingerprint':fingerprint,
        'unreal_imported_by_this_command':0,'limits':['Historical photos; geometric candidates require identity review.',
        'Roof candidates are not approvals or facade colors.','All estimated palette assignments are illustrative.',
        'Source inventory is indexed buildings, not a Beirut completeness census.']}
    (root/'report.json').write_text(json.dumps(report,indent=2))
    cards=[]
    for pid,photo in sorted(photos.items()):
        nearby=[i for i,values in candidates.items() if any(c['photo_id']==pid for c in values)]
        cards.append(f'<article><a href="{Path(photo["path"]).as_uri()}"><img loading="lazy" src="{Path(photo["path"]).as_uri()}" alt="Historical street photograph {pid}"></a><p>{pid} · {html.escape(photo["capture_date"])} · {len(nearby)} geometric building candidates</p><p>© Grab and KartaView Contributors · CC BY-SA 4.0</p></article>')
    page=f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Citywide appearance staging</title><style>body{{font:16px/1.6 "Segoe UI",Arial,sans-serif;background:#f4f3ed;color:#203b30;margin:30px auto;max-width:1200px;padding:20px}}h1{{font-size:40px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}}article{{background:white;padding:14px;border-radius:12px}}img{{width:100%}}a{{color:#176652}}</style><a href="../../../appearance-evidence-20261030/beirut-work-and-evidence.html">← Work and evidence</a><h1>Citywide appearance assignments</h1><p>{len(records):,} buildings · {len(tiles)} owner tiles · {report['estimated_facades']:,} estimated facades · {report['observed_historical_facades']} historical visible-facade observation.</p><p><strong>Staged, not imported into Unreal.</strong> Roof candidates stay separate from wall colors. Geometric photo candidates are a review shortlist, not confirmed identity.</p><p>{len(photos)} unique cached photos; {report['buildings_with_geometric_photo_candidates']} buildings with plausible views. Collection is still running; rerun this script to incorporate new cached images.</p><p><a href="report.json">Run report</a> · <a href="inputs.json">Input checksums</a> · <a href="../../../facade-coverage-20261030/review.html">16-building coverage repair</a></p><h2>Historical photo review queue</h2><div class="grid">{''.join(cards)}</div>'''
    (root/'index.html').write_text(page,encoding='utf-8')
    (output/'latest-run.json').write_text(json.dumps({'fingerprint':fingerprint,'run_directory':str(root),'page':str(root/'index.html'),'report':str(root/'report.json')},indent=2))
    print(json.dumps(report))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--collection',default='C:/private/dynacity-data/engine/appearance-citywide-20261030')
    p.add_argument('--output',default='C:/private/dynacity-data/engine/appearance-plan-20261030')
    args=p.parse_args();prepare(args.collection,args.output)
