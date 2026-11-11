"""Reconcile mapped infrastructure with a street export and saved instance index.

An index entry is evidence of a saved manifest record, not proof that the
current Unreal actor is visible, loaded, or uses a finished asset.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def report(source, instances, verifications=(), legacy_verified=()):
    indexed=Counter(row['source_id'] for row in instances.get('instances', []))
    verified=Counter(identifier for verification in verifications
                     if verification.get('reloaded_external_actors') is True
                     for group in verification.get('groups', [])
                     for identifier in group.get('source_ids', []))
    for verification in legacy_verified:
        groups=verification.get('groups', [])
        if sum(g.get('count', -1) for g in groups) != verification.get('total') or any(
            g.get('count') != len(g.get('source_ids', [])) for g in groups
        ):
            raise ValueError('Invalid legacy verification counts')
        verified.update(identifier for group in groups for identifier in group['source_ids'])
    points={row['id'] for row in source.get('points', [])}
    unplaced={row['id'] for row in source.get('unplaced_points', [])}
    records=[]
    for ref in source.get('infrastructure_references', []):
        identifier=ref['id']
        if identifier in verified:status='verified_after_actor_reload'
        elif identifier in indexed:status='listed_in_saved_instance_index'
        elif identifier in points:status='exported_point_not_in_saved_index'
        elif identifier in unplaced:status='missing_ground_no_point_placement'
        else:status='source_reference_only'
        records.append({**ref,'status':status,'saved_index_instances':indexed[identifier],
                        'verified_instances':verified[identifier]})
    counts=Counter(r['status'] for r in records)
    by_kind={}
    for kind in sorted({r['kind'] for r in records}):
        by_kind[kind]=dict(Counter(r['status'] for r in records if r['kind']==kind))
    return {'schema':'dynacity-infrastructure-coverage/1',
            'source_manifest':source.get('inputs',{}),'saved_instance_manifest':instances.get('source_manifest'),
            'source_count':len(records),'status_counts':dict(counts),'by_kind':by_kind,'records':records,
            'limits':['Index records do not prove live visibility, asset quality, actor loading or current scene state.',
                      'Reference geometry is retained but does not represent a 3D object.',
                      'OSM completeness, individual feature accuracy and currency are not established.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--street-context',required=True,type=Path)
    parser.add_argument('--instance-index',required=True,type=Path)
    parser.add_argument('--verification',action='append',type=Path,default=[],
                        help='Native verification JSON; may be repeated')
    parser.add_argument('--legacy-reloaded-verification',action='append',type=Path,default=[],
                        help='Legacy report independently confirmed after actor reload')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    result=report(read(args.street_context),read(args.instance_index),[read(p) for p in args.verification],
                  [read(p) for p in args.legacy_reloaded_verification])
    result['verification_files']=[str(p) for p in args.verification]
    result['legacy_reloaded_verification_files']=[str(p) for p in args.legacy_reloaded_verification]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('source_count','status_counts','by_kind')},indent=2))


if __name__=='__main__':main()
