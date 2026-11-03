"""Resumable street-photo discovery and evidence packets for one or all passes.

No LLM calls. No Unreal changes. Geometry shortlists never establish identity.
The default command fetches bounded additional sequence pages and photos.
Use --offline to process only already-cached evidence, or --batch 1 for pass 1.
"""
import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
import html
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlencode

import httpx
from PIL import Image
from pyproj import Geod, Transformer
from shapely.geometry import Point, shape
from shapely.strtree import STRtree
from dynacity.street_photo_visibility import visible_wall_samples

ENDPOINT='https://api.kartaview.org/2.0/photo/'
ATTRIBUTION='© Grab and KartaView Contributors'
STYLE='body{font:16px/1.5 "Segoe UI",Arial,sans-serif;background:#f4f3ed;color:#203b30;max-width:1250px;margin:30px auto;padding:24px}a{color:#176652}table{width:100%;border-collapse:collapse;background:white}td,th{text-align:left;padding:10px;border-bottom:1px solid #ddd}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}article{background:white;padding:15px;border-radius:12px}img{width:100%}.notice{padding:15px;background:#fff0d8;border-left:4px solid #b58132}'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8');os.replace(temporary,path)


def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def response_hash(response):return hashlib.sha256(json.dumps(response,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@contextmanager
def exclusive(path):
    """Same byte lock as the existing collector; kernel releases on interruption."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)


def cached_query(path,params):
    try:
        data=read_json(path)
        if data.get('response_sha256') and data['response_sha256']!=response_hash(data['response']):raise RuntimeError('Changed metadata query cache')
        if data['params']==params and data['response']['status']['apiCode']==600:return data['response']
    except (OSError,ValueError,KeyError,TypeError):pass
    return None


def wait_rate(path,interval):
    last=read_json(path)['time'] if Path(path).exists() else 0
    while last+interval>time.time():time.sleep(min(10,last+interval-time.time()))
    write_json(path,{'time':time.time()})


class Fetcher:
    def __init__(self,args,root):
        self.args=args;self.root=root;self.requests=0;self.downloads=0;self.bytes=0
        self.client=httpx.Client(timeout=30,follow_redirects=False,headers={'User-Agent':'DYNAcity-research-evidence/1.0'})

    def query(self,path,params):
        cached=cached_query(path,params)
        if cached is not None:return cached
        if self.args.offline or self.requests>=self.args.max_queries:return None
        wait_rate(Path(self.args.collection)/'last-street-request.json',3600/self.args.requests_per_hour)
        self.requests+=1
        response=self.client.get(ENDPOINT,params=params);response.raise_for_status();data=response.json()
        if data.get('status',{}).get('apiCode')!=600:raise ValueError('Provider rejected metadata query')
        if not isinstance(data.get('result',{}).get('data'),list):raise ValueError('Invalid provider rows')
        write_json(path,{'params':params,'request_url':str(response.url),'response':data,
                         'response_sha256':response_hash(data),'license':'CC BY-SA 4.0','attribution':ATTRIBUTION})
        return data

    def photo(self,row):
        identity=str(row['id']);path=self.root/'photos'/(identity+'.jpg');meta=path.with_suffix('.json')
        if meta.exists() and path.exists():
            record=read_json(meta)
            if record.get('sha256')==sha(path):return record
            raise ValueError('Changed cached photo: '+identity)
        if self.args.offline or self.downloads>=self.args.max_photos or self.args.max_download_mb==0:return None
        url=row.get('imageProcUrl','')
        if not identity.isdigit() or row.get('visibility')!='public' or not url.startswith('https://cdn.kartaview.org/'):return None
        wait_rate(self.root/'last-photo-request.json',3)
        temporary=path.with_suffix('.download');path.parent.mkdir(parents=True,exist_ok=True)
        size=0;self.downloads+=1
        try:
            with self.client.stream('GET',url,timeout=45) as response:
                response.raise_for_status()
                if not response.headers.get('content-type','').startswith('image/'):raise ValueError('Non-image photo response')
                with temporary.open('wb') as file:
                    for part in response.iter_bytes():
                        size+=len(part)
                        if size>15_000_000 or self.bytes+size>self.args.max_download_mb*1_000_000:raise ValueError('Photo download byte budget reached')
                        file.write(part)
            with Image.open(temporary) as image:image.verify()
            os.replace(temporary,path);self.bytes+=size
        finally:
            if temporary.exists():temporary.unlink()
        record={'id':identity,'path':str(path.resolve()),'sha256':sha(path),'bytes':size,
                'capture_date':row.get('shotDate'),'url':url,'license':'CC BY-SA 4.0','attribution':ATTRIBUTION}
        write_json(meta,record);return record


def load_inputs(args):
    pointer=read_json(Path(args.passes)/'latest-run.json');run=Path(pointer['run_directory'])
    selected=[];members={};assignment_cache={}
    for path in sorted(run.glob('pass-*.json')):
        data=read_json(path)
        if args.batch and data['pass_number']!=args.batch:continue
        selected.append(data['pass_number'])
        for entry in data['records']:
            source=Path(entry['assignment_manifest'])
            if str(source) not in assignment_cache:
                if sha(source)!=entry['assignment_manifest_sha256']:raise ValueError('Changed assignment manifest')
                assignment_cache[str(source)]={r['source_id']:r for r in read_json(source)['records']}
            if entry['source_id'] in members:raise ValueError('Duplicate building in selected passes')
            members[entry['source_id']]={'pass':data['pass_number'],'assignment':assignment_cache[str(source)][entry['source_id']]}
    if not selected:raise ValueError('Requested pass does not exist')
    collection=Path(read_json(Path(args.collection)/'latest-run.json')['run_directory'])
    inventory=read_json(collection/'inventory.json')
    for source in inventory['sources']:
        if sha(source['index'])!=source['sha256']:raise ValueError('Source index changed')
    if not set(members)<=set(r['source_id'] for r in inventory['records']):raise ValueError('Batch IDs missing from source inventory')
    return selected,members,inventory,collection


def ingest(paths,frames,photos):
    """Reuse cached metadata and checksum-verified processed images."""
    for path in sorted(paths):
        data=read_json(path)
        if data.get('response_sha256') and data['response_sha256']!=response_hash(data['response']):raise ValueError('Changed metadata query cache')
        if data.get('response',{}).get('status',{}).get('apiCode')!=600:continue
        rows=data['response']['result']['data']
        for row in rows:
            if str(row.get('id','')).isdigit():frames[str(row['id'])]=row
        result=path.parent/'result.json'
        if path.name=='street-query.json' and result.exists():
            saved=read_json(result)
            if sha(path)!=saved['query_sha256']:raise ValueError('Changed existing query cache')
            for photo in saved['photos']:
                image=path.parent/photo['file']
                if sha(image)!=photo['sha256']:raise ValueError('Changed existing photo')
                photos[str(photo['id'])]={**photo,'path':str(image.resolve())}


def prime_sequence_cache(pilot,root):
    """Copy existing sequence responses into the parameter-keyed query cache."""
    for path in sorted(pilot.glob('street-sequence-*-page-*.json')):
        data=read_json(path);params=data.get('params',{})
        sid=str(params.get('sequenceId',''));page=params.get('page')
        if not sid.isdigit() or not isinstance(page,int) or data.get('response',{}).get('status',{}).get('apiCode')!=600:continue
        target=root/'queries'/f'sequence-{sid}-page-{page}.json'
        if not target.exists():write_json(target,{**data,'response_sha256':response_hash(data['response']),'source_cache_sha256':sha(path)})


def reuse_other_pass_caches(root,photos):
    """Reuse other pass runs without redownloading their metadata or images."""
    for path in sorted(root.parent.glob('*/queries/*.json')):
        if path.parent.parent==root:continue
        data=read_json(path)
        if data.get('response_sha256') and data['response_sha256']!=response_hash(data['response']):raise ValueError('Changed shared metadata cache')
        target=root/'queries'/path.name
        if not target.exists():write_json(target,data)
    for path in sorted(root.parent.glob('*/photos/*.json')):
        if path.parent.parent==root:continue
        photo=read_json(path)
        if sha(photo['path'])!=photo['sha256']:raise ValueError('Changed shared photo cache')
        photos[str(photo['id'])]=photo


def normalize_camera(row,convert,geod):
    if row.get('projection')!='PLANE':return None
    try:lon,lat,heading=map(float,(row['lng'],row['lat'],row['heading']))
    except (KeyError,TypeError,ValueError):return None
    if not all(math.isfinite(v) for v in (lon,lat,heading)) or not -180<=lon<=180 or not -90<=lat<=90:return None
    xy=convert.transform(lon,lat);lon2,lat2,_=geod.fwd(lon,lat,heading,100);x2,y2=convert.transform(lon2,lat2)
    return xy,math.degrees(math.atan2(x2-xy[0],y2-xy[1]))%360


def greedy_frames(candidates,already_cached,maximum):
    """Prefer frames covering the most not-yet-covered buildings, ties by ID."""
    remaining=dict(candidates);chosen=[];covered=set()
    # A cached image that can cover a building should be used before a download.
    for pid in sorted(set(remaining)&set(already_cached)):
        chosen.append(pid);covered.update(c['source_id'] for c in remaining.pop(pid))
    while remaining and len([p for p in chosen if p not in already_cached])<maximum:
        pid=min(remaining,key=lambda p:(-len({c['source_id'] for c in remaining[p]}-covered),p))
        new={c['source_id'] for c in remaining[pid]}-covered
        if not new:break
        chosen.append(pid);covered.update(new);remaining.pop(pid)
    return chosen


def screen_frames(root,frames,records,members,inventory_hash):
    polygons=[shape(r['footprint']) for r in records];tree=STRtree(polygons)
    convert=Transformer.from_crs('EPSG:4326',records[0]['crs'],always_xy=True);geod=Geod(ellps='WGS84')
    candidates={};unsupported=0
    for pid,row in sorted(frames.items()):
        camera=normalize_camera(row,convert,geod)
        if camera is None:unsupported+=1;continue
        xy,heading=camera
        nearby=[int(i) for i in tree.query(Point(xy).buffer(80)) if records[i]['source_id'] in members]
        if not nearby:continue
        key=hashlib.sha256(json.dumps({'camera':camera,'inventory':inventory_hash,'algorithm':sha(Path(__file__)),
                                    'screening_algorithm':sha(Path(__file__).resolve().parents[1]/'src/dynacity/street_photo_visibility.py'),
                                    'targets':[records[i]['source_id'] for i in nearby]},sort_keys=True).encode()).hexdigest()[:20]
        cache=root/'screen-cache'/(pid+'-'+key+'.json')
        if cache.exists():found=read_json(cache)
        else:
            found=[]
            for index in nearby:
                outcome=visible_wall_samples(xy,heading,polygons[index],tree)
                if not outcome['samples']:continue
                sample=min(outcome['samples'],key=lambda s:abs(s['heading_offset_deg'])+.2*abs(s['distance_m']-25))
                found.append({'source_id':records[index]['source_id'],'photo_id':pid,'camera_xy_m':list(xy),
                    'heading_grid_deg':heading,'wall_sample':sample,
                    'rank_cost':abs(sample['heading_offset_deg'])+.2*abs(sample['distance_m']-25),
                    'identity_confirmed':False,'status':'geometry_candidate_needs_review'})
            write_json(cache,found)
        if found:candidates[pid]=found
    return candidates,unsupported


def map_svg(entries,records):
    by_id={r['source_id']:r for r in records};xy=entries[0]['camera_xy_m']
    def pt(x,y):return (250+(x-xy[0])*2.5,250-(y-xy[1])*2.5)
    parts=['<svg viewBox="0 0 500 500" role="img" aria-label="Camera and candidate footprints; north up"><rect width="500" height="500" fill="#edf2ed"/>']
    for n,entry in enumerate(entries,1):
        geometry=shape(by_id[entry['source_id']]['footprint'])
        for polygon in (geometry.geoms if geometry.geom_type=='MultiPolygon' else [geometry]):
            coords=' '.join(f'{px:.1f},{py:.1f}' for px,py in (pt(x,y) for x,y in polygon.exterior.coords))
            parts.append(f'<polygon points="{coords}" fill="#f4cd64" stroke="#69552c"/>')
        cx,cy=pt(geometry.centroid.x,geometry.centroid.y);parts.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="Segoe UI,Arial" font-size="12">{n}</text>')
    angle=math.radians(entries[0]['heading_grid_deg']);ex,ey=pt(xy[0]+20*math.sin(angle),xy[1]+20*math.cos(angle))
    parts.append(f'<circle cx="250" cy="250" r="6" fill="#b52a25"/><line x1="250" y1="250" x2="{ex:.1f}" y2="{ey:.1f}" stroke="#b52a25" stroke-width="4"/><text x="12" y="24">North ↑ · yellow = candidates · red = camera</text></svg>')
    return ''.join(parts)


def publish(root,selected,members,records,frames,photos,candidates,fetcher,phase,notes):
    by_building=defaultdict(list)
    for pid,entries in candidates.items():
        for entry in entries:by_building[entry['source_id']].append({**entry,'photo_cached':pid in photos})
    views=root/'views';views.mkdir(exist_ok=True);cards=[]
    for pid,entries in sorted(candidates.items()):
        if pid not in photos:continue
        photo=photos[pid];image=Path(photo['path']).as_uri();labels=''.join(f'<li>{html.escape(e["source_id"])}</li>' for e in entries)
        page=f'<!doctype html><meta charset="utf-8"><title>Photo {pid} · candidate map</title><style>{STYLE}</style><a href="../index.html">← Evidence runs</a><h1>Photo {pid}</h1><p>Historical view; identity is unconfirmed. Camera calibration and GPS uncertainty remain unverified.</p><div class="grid"><article><a href="{image}"><img src="{image}" alt="Historical street photo {pid}"></a></article><article>{map_svg(entries,records)}<ol>{labels}</ol></article></div><p>{html.escape(str(photo.get("capture_date")))} · {ATTRIBUTION} · CC BY-SA 4.0</p><p>SHA-256: {photo["sha256"]}</p>'
        (views/(pid+'.html')).write_text(page,encoding='utf-8')
        # Cached thumbnails keep the main gallery light; original bytes are retained.
        thumb=root/'thumbnails'/(pid+'.jpg');thumb.parent.mkdir(exist_ok=True)
        thumb_meta=thumb.with_suffix('.json')
        if not thumb.exists() or not thumb_meta.exists() or read_json(thumb_meta).get('source_sha256')!=photo['sha256']:
            with Image.open(photo['path']) as source:
                source=source.convert('RGB');source.thumbnail((480,360));source.save(thumb,quality=80)
            write_json(thumb_meta,{'source_sha256':photo['sha256']})
        cards.append(f'<article><a href="views/{pid}.html"><img loading="lazy" src="thumbnails/{pid}.jpg" alt="Street evidence {pid}">Photo {pid}: {len(entries)} candidate buildings →</a></article>')
    passes=[]
    for number in selected:
        output=[]
        for identity,item in sorted(members.items()):
            if item['pass']!=number:continue
            assignment=item['assignment'];choices=sorted(by_building[identity],key=lambda c:(not c['photo_cached'],c['rank_cost'],c['photo_id']))[:3]
            observation=assignment['facade'].get('observation')
            # Previous reviewed observations are retained with their own evidence.
            status='historical_observation_retained' if observation else ('cached_candidates_need_review' if any(c['photo_cached'] for c in choices) else 'metadata_candidates_only' if choices else 'no_usable_view_found')
            output.append({'source_id':identity,'owner_tile':assignment['owner_tile'],'district':assignment['district'],
                'status':status,'candidates':choices,'reviewed_observation':observation,
                'roof_evidence':assignment['roof'],'baseline_assignment':assignment['facade'],
                'automatic_facade_approval':False,'unreal_applied':False})
        counts=dict(Counter(r['status'] for r in output));write_json(root/f'pass-{number:02}.json',{'pass':number,'buildings':len(output),'counts':counts,'records':output})
        passes.append({'pass':number,'buildings':len(output),'counts':counts})
    report={'status':phase,'selected_buildings':len(members),'passes':passes,'metadata_frames':len(frames),
        'candidate_photo_frames':len(candidates),'buildings_with_geometry_candidates':sum(bool(values) for values in by_building.values()),
        'buildings_with_cached_photo_candidates':sum(any(c['photo_cached'] for c in values) for values in by_building.values()),
        'new_metadata_requests_this_run':fetcher.requests,'new_photos_this_run':fetcher.downloads,
        'new_download_bytes_this_run':fetcher.bytes,'automatic_confirmations':0,'unreal_changes':0,'notes':notes,
        'limits':['Historical photos and geometric candidates require identity and architectural review.',
                  'No guarantee of image coverage for all selected buildings.','Roof colors are never used as wall colors.']}
    write_json(root/'report.json',report);write_json(root/'photo-catalog.json',{'photos':list(photos.values()),'license':'CC BY-SA 4.0','attribution':ATTRIBUTION})
    rows=''.join(f'<tr><td><a href="pass-{p["pass"]:02}.json">Pass {p["pass"]}</a></td><td>{p["buildings"]}</td><td>{p["counts"].get("cached_candidates_need_review",0)}</td><td>{p["counts"].get("no_usable_view_found",0)}</td></tr>' for p in passes)
    (root/'index.html').write_text(f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>Automated facade evidence</title><style>{STYLE}</style><h1>Automated facade evidence</h1><p>{html.escape(phase)} · {len(members):,} selected buildings · {len(frames):,} metadata frames · {report["buildings_with_cached_photo_candidates"]} buildings with cached candidate views.</p><p class="notice">No automatic building confirmations or Unreal edits. Empty coverage is reported honestly. Photos are historical; materials and dimensions require review.</p><table><tr><th>Pass</th><th>Buildings</th><th>Cached candidates needing review</th><th>No usable view</th></tr>{rows}</table><p><a href="report.json">Detailed report and limitations</a> · <a href="photo-catalog.json">Photo catalog and checksums</a></p><h2>Photographs beside candidate building maps</h2><div class="grid">'+''.join(cards)+'</div>',encoding='utf-8')
    return report


def run(args):
    selected,members,inventory,collection=load_inputs(args)
    root=Path(args.output).resolve()/('all-batches' if not args.batch else f'batch-{args.batch:02}');root.mkdir(parents=True,exist_ok=True)
    # Hold the output lock for the whole run; alternate outputs share provider lock.
    with exclusive(root/'.run.lock'):
        inputs={'selected_ids':sorted(members),'pass_numbers':selected,'inventory_hash':sha(collection/'inventory.json')}
        if (root/'inputs.json').exists() and read_json(root/'inputs.json')!=inputs:raise ValueError('Output belongs to a different inventory or pass selection; choose a new output')
        write_json(root/'inputs.json',inputs)
        fetcher=Fetcher(args,root);frames={};photos={};notes=[]
        ingest((collection/'street').glob('*/street-query.json'),frames,photos)
        pilot=Path(args.collection).parent/'appearance-evidence-20261030'
        prime_sequence_cache(pilot,root)
        reuse_other_pass_caches(root,photos)
        ingest(pilot.glob('street-sequence-*-page-*.json'),frames,photos)
        if (pilot/'street-photo-review-report.json').exists():
            for photo in read_json(pilot/'street-photo-review-report.json')['photos']:
                image=pilot/'street-photos'/(str(photo['photo_id'])+'.jpg')
                if sha(image)!=photo['sha256']:raise ValueError('Changed pilot photo')
                photos[str(photo['photo_id'])]={**photo,'id':str(photo['photo_id']),'path':str(image.resolve()),'capture_date':photo.get('captured_at')}
        ingest((root/'queries').glob('*.json'),frames,photos)
        for meta in (root/'photos').glob('*.json'):
            photo=read_json(meta)
            if sha(photo['path'])!=photo['sha256']:raise ValueError('Changed cached downloaded image')
            photos[str(photo['id'])]=photo
        candidates,unsupported=screen_frames(root,frames,inventory['records'],members,inputs['inventory_hash'])
        notes.append({'unsupported_camera_metadata':unsupported})
        initial_phase='cached_evidence_processed' if args.offline else 'network_collection_starting'
        report=publish(root,selected,members,inventory['records'],frames,photos,candidates,fetcher,initial_phase,notes)
        write_json(Path(args.output)/'latest-run.json',{'run_directory':str(root),'page':str(root/'index.html'),'report':str(root/'report.json')})
        if args.offline:
            fetcher.client.close();print(json.dumps(report));return report
        # Existing collector owns this lock until it has finished; do not run two
        # uncoordinated API clients or change its scripts/fingerprint underneath it.
        deadline=time.time()+args.wait_for_collector_minutes*60
        while True:
            try:
                provider=exclusive(Path(args.collection)/'.citywide.lock');provider.__enter__();break
            except OSError:
                write_json(root/'network-state.json',{'status':'waiting_for_existing_collector','time':time.time()})
                if time.time()>=deadline:
                    report=publish(root,selected,members,inventory['records'],frames,photos,candidates,fetcher,'cached_review_ready_network_deferred',notes)
                    fetcher.client.close();print(json.dumps(report));return report
                time.sleep(10)
        errors=[];last_published_requests=0
        def refresh(phase):
            nonlocal candidates,last_published_requests
            candidates,invalid=screen_frames(root,frames,inventory['records'],members,inputs['inventory_hash'])
            notes[0]={'unsupported_camera_metadata':invalid}
            for pid in greedy_frames(candidates,photos,max(0,args.max_photos-fetcher.downloads)):
                if pid in photos:continue
                try:
                    photo=fetcher.photo(frames[pid])
                    if photo:photos[pid]=photo
                except (httpx.HTTPError,ValueError) as error:
                    errors.append(str(error))
                    if len(errors)>=3:break
            last_published_requests=fetcher.requests
            return publish(root,selected,members,inventory['records'],frames,photos,candidates,fetcher,phase,notes+[{'errors':errors}])
        try:
            # Reuse newly completed nearby queries after waiting for the collector.
            ingest((collection/'street').glob('*/street-query.json'),frames,photos)
            reuse_other_pass_caches(root,photos)
            ingest((root/'queries').glob('*.json'),frames,photos)
            seed_sequences=sorted({str(r['sequenceId']) for r in frames.values() if str(r.get('sequenceId','')).isdigit()})
            # Existing nearby groups cover the source area; fill gaps with one
            # query per shared 500 m group, never one request per building.
            grouped=defaultdict(list)
            for record in inventory['records']:
                if record['source_id'] in members:
                    tx,ty=map(int,record['owner_tile'].split(':'));grouped[f'{tx//2}:{ty//2}'].append(record)
            for name,records in sorted(grouped.items()):
                existing=collection/'street'/name.replace(':','_')/'street-query.json'
                if existing.exists():continue
                centers=[shape(r['footprint']).centroid for r in records];x=sum(p.x for p in centers)/len(centers);y=sum(p.y for p in centers)/len(centers)
                lon,lat=Transformer.from_crs(records[0]['crs'],'EPSG:4326',always_xy=True).transform(x,y)
                params={'lat':lat,'lng':lon,'radius':500,'itemsPerPage':100}
                try:
                    data=fetcher.query(root/'queries'/('nearby-'+name.replace(':','_')+'.json'),params)
                    if data is not None:
                        for row in data['result']['data']:frames[str(row['id'])]=row
                except (httpx.HTTPError,ValueError) as error:
                    errors.append(str(error))
                    if len(errors)>=3:break
            seed_sequences=sorted(set(seed_sequences)|{str(r['sequenceId']) for r in frames.values() if str(r.get('sequenceId','')).isdigit()})
            # Breadth first: first page from every route before extending routes.
            complete=set();truncated=[]
            for page in range(1,args.pages_per_sequence+1):
                for sid in seed_sequences:
                    if sid in complete or len(errors)>=3:continue
                    params={'sequenceId':sid,'itemsPerPage':100,'page':page}
                    try:
                        data=fetcher.query(root/'queries'/f'sequence-{sid}-page-{page}.json',params)
                        if data is None:continue
                        for row in data['result']['data']:frames[str(row['id'])]=row
                        if not data['result'].get('hasMoreData'):complete.add(sid)
                        elif page==args.pages_per_sequence:truncated.append(sid)
                        write_json(root/'network-state.json',{'status':'fetching_sequence_metadata','sequence':sid,'page':page,'requests':fetcher.requests,'frames':len(frames)})
                        if fetcher.requests-last_published_requests>=10 or (fetcher.requests and fetcher.requests>=args.max_queries and fetcher.requests!=last_published_requests):
                            refresh('collecting_more_metadata_and_photos')
                    except (httpx.HTTPError,ValueError) as error:errors.append(str(error))
            refresh('finalizing_bounded_evidence')
            notes.extend([{'sequences':len(seed_sequences),'sequence_page_caps_reached':truncated,'errors':errors},
                          {'metadata_query_budget':args.max_queries,'metadata_requests_per_hour':args.requests_per_hour,
                           'metadata_budget_reached':fetcher.requests>=args.max_queries,
                           'photo_budget':args.max_photos,'download_mb_budget':args.max_download_mb}])
            phase='review_ready_with_errors' if errors else 'bounded_collection_finished_review_needed'
            report=publish(root,selected,members,inventory['records'],frames,photos,candidates,fetcher,phase,notes)
            write_json(root/'network-state.json',{'status':phase,'requests':fetcher.requests,'new_photos':fetcher.downloads})
        finally:
            provider.__exit__(None,None,None);fetcher.client.close()
        print(json.dumps(report));return report


def arguments():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--passes',default='C:/private/dynacity-data/engine/appearance-passes-20261030')
    parser.add_argument('--collection',default='C:/private/dynacity-data/engine/appearance-citywide-20261030')
    parser.add_argument('--output',default='C:/private/dynacity-data/engine/facade-batch-evidence-20261030')
    parser.add_argument('--batch',type=int,help='Omit to process all passes in one command')
    parser.add_argument('--offline',action='store_true',help='No network requests; reuse verified caches only')
    parser.add_argument('--max-queries',type=int,default=200,help='Maximum new metadata calls per invocation')
    parser.add_argument('--pages-per-sequence',type=int,default=5)
    parser.add_argument('--max-photos',type=int,default=500,help='Maximum new selected photos per invocation')
    parser.add_argument('--max-download-mb',type=int,default=750)
    parser.add_argument('--requests-per-hour',type=int,default=60)
    parser.add_argument('--wait-for-collector-minutes',type=float,default=180)
    args=parser.parse_args()
    if args.batch is not None and args.batch<1:parser.error('Batch number must be positive')
    if not 1<=args.requests_per_hour<=60 or min(args.max_queries,args.max_photos,args.max_download_mb)<0 or args.pages_per_sequence<1 or args.wait_for_collector_minutes<0:parser.error('Invalid rate or budget')
    return args


if __name__=='__main__':run(arguments())
