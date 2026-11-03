"""Validate durable review decisions without modifying a running collector.

Reviewed photo bytes are pinned. Rejected images are excluded from downstream
assignments; uncertain identities never become approved Unreal jobs.
"""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def save(path,value):Path(path).write_text(json.dumps(value,indent=2),encoding='utf-8')


def validate_decision(decision,photo):
    allowed={'rejected_non_facade','visible_facades_identity_unresolved','identity_conflict','corroborated'}
    if decision['status'] not in allowed:raise ValueError('Unknown review status')
    if decision['photo_sha256']!=photo['sha256'] or sha(photo['path'])!=photo['sha256']:
        raise ValueError('Photo differs from reviewed bytes')
    if not decision.get('reason'):raise ValueError('Review requires a reason')
    if decision['status']=='corroborated':
        if not decision.get('source_id') or not decision.get('corroboration'):raise ValueError('Missing identity corroboration')
        if not decision.get('visible_features'):raise ValueError('Missing visible facade observations')
        anchor=decision['corroboration']
        if not anchor.get('visible_sign_matches_anchor'):raise ValueError('Unmatched landmark cannot approve identity')
        if sha(anchor['cached_source'])!=anchor['cached_sha256']:raise ValueError('Changed corroboration source')


def apply(root):
    root=Path(root);collector=root/'all-batches';out=root/'reviewed';out.mkdir(exist_ok=True)
    catalog={str(p['id']):p for p in read(collector/'photo-catalog.json')['photos']}
    decisions=read(root/'review-decisions.json')['photos'];by_photo={}
    for decision in decisions:
        pid=decision['photo_id']
        if pid in by_photo:raise ValueError('Duplicate photo review')
        validate_decision(decision,catalog[pid]);by_photo[pid]=decision
    records=[];source_ids=set();retained={};rejected_pairs=0
    for path in sorted(collector.glob('pass-*.json')):
        data=read(path);result=[]
        for record in data['records']:
            identity=record['source_id']
            if identity in source_ids:raise ValueError('Duplicate building ownership')
            source_ids.add(identity)
            keep=[];excluded=[]
            for candidate in record['candidates']:
                decision=by_photo.get(candidate['photo_id'])
                if decision and (decision['status']=='rejected_non_facade' or
                    decision['status']=='identity_conflict' and decision.get('source_id')==identity):
                    excluded.append({**candidate,'review_reason':decision['reason']});rejected_pairs+=1
                else:keep.append(candidate)
            observation=record.get('reviewed_observation')
            if observation:retained[identity]=observation
            result.append({**record,'candidates':keep,'excluded_candidates':excluded,
                           'review_status':'historical_observation_retained' if observation else 'identity_review_needed' if keep else 'no_eligible_candidates'})
        save(out/path.name,{'pass':data['pass'],'records':result,'buildings':len(result)})
        records.extend(result)
    jobs=[]
    for decision in decisions:
        if decision['status']!='corroborated':continue
        if decision['source_id'] not in source_ids:raise ValueError('Unknown source ID in reviewed identity')
        jobs.append({'source_id':decision['source_id'],'photo_id':decision['photo_id'],
                     'visible_features':decision['visible_features'],'status':'reviewed_visible_portion_only',
                     'material_identity':'unknown','physical_albedo_rgb':None,'unreal_applied':False})
    report={'reviewed_photos':len(decisions),'review_status_counts':dict(Counter(d['status'] for d in decisions)),
            'building_records':len(records),'excluded_candidate_pairs':rejected_pairs,
            'new_corroborated_jobs':len(jobs),'previous_historical_observations_retained':len(retained),
            'unreal_changes':0,'review_decisions_sha256':sha(root/'review-decisions.json')}
    save(out/'report.json',report);save(out/'new-facade-jobs.json',{'jobs':jobs,'limits':['Visible historical facade portions only; geometry and material identity remain unmeasured.']})
    save(out/'retained-observations.json',{'observations':list(retained.values())})
    save(out/'reviewed-photo-catalog.json',{'photos':[{**catalog[d['photo_id']],'review':d} for d in decisions]})
    cards=[]
    for d in decisions:
        photo=catalog[d['photo_id']];pid=d['photo_id'];features='; '.join(d.get('visible_features',[]))
        thumb=collector/'thumbnails'/(pid+'.jpg');image=thumb if thumb.exists() else Path(photo['path'])
        cards.append(f'<article><a href="{Path(photo["path"]).as_uri()}"><img loading="lazy" src="{image.as_uri()}" alt="Reviewed historical photo {pid}"></a><h2>{pid}</h2><strong>{html.escape(d["status"].replace("_"," "))}</strong><p>{html.escape(d["reason"])}</p><p>{html.escape(features)}</p><p>{html.escape(str(photo.get("capture_date")))} · © Grab and KartaView Contributors · CC BY-SA 4.0</p><a href="{(collector/"views"/(pid+".html")).as_uri()}">Candidate footprint map</a></article>')
    page=f'''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Reviewed facade evidence</title><style>body{{font:16px/1.6 "Segoe UI",Arial,sans-serif;background:#f4f3ed;color:#203b30;max-width:1200px;margin:auto;padding:30px}}h1{{font-size:40px}}h2{{font-size:18px}}a{{color:#176652}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:20px}}article{{padding:18px;background:white;border-radius:12px}}img{{width:100%}}</style><a href="../../appearance-evidence-20261030/beirut-work-and-evidence.html">← Work log</a><h1>Photo review decisions</h1><p>{len(decisions)} photos reviewed · {rejected_pairs} candidate pairs excluded · {len(jobs)} newly corroborated facade jobs.</p><p>Prior LGB Bank historical observation retained separately. No new Unreal edits. Indoor/tunnel images are rejected; ambiguous or contradictory identity stays unresolved. Clear architecture in a photo does not by itself establish a source-building match.</p><p><a href="report.json">Review report</a> · <a href="new-facade-jobs.json">New supported facade jobs</a> · <a href="../all-batches/index.html">Live collection</a></p><div class="grid">{''.join(cards)}</div>'''
    (out/'index.html').write_text(page,encoding='utf-8');print(json.dumps(report));return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='C:/private/dynacity-data/engine/facade-batch-evidence-20261030')
    apply(p.parse_args().root)
