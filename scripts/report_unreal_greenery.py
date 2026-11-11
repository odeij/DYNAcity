"""Reconcile each mapped vegetation source against exported and imported Unreal layers."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True);p.add_argument('--project',required=True)
    p.add_argument('--raw',required=True);p.add_argument('--output',required=True)
    p.add_argument('--boundary-audit');p.add_argument('--references');p.add_argument('--gap-patch')
    args=p.parse_args();read=lambda f:json.loads(Path(f).read_text(encoding='utf-8'))
    source=read(args.manifest);raw=read(args.raw);saved=Path(args.project)/'Saved/DynaCity'
    instances=read(saved/'street-instances.json');covers=read(saved/'street-covers.json')
    point_ids=Counter(r['source_id'] for r in instances['instances'])
    cover_ids=Counter(obj['source_id'] for package in covers['packages'] if package['key'] in covers['completed']
                      for obj in package['objects'])
    audits=defaultdict(list)
    boundary={r['id']:r for r in read(args.boundary_audit)} if args.boundary_audit else {}
    references={r['id']:r for r in read(args.references)['records']} if args.references else {}
    patch=read(args.gap_patch) if args.gap_patch else {}
    for row in source.get('feature_audit',[]):audits[row['id']].append(row)
    records=[]
    for row in source.get('greenery_inventory',[]):
        identifier=row['id'];stages=audits[identifier]
        exported=[s for s in stages if s['status']=='exported']
        placed=point_ids[identifier];surfaces=cover_ids[identifier]
        status='rendered' if placed or surfaces else 'skipped'
        reasons=sorted({s['reason'] for s in stages if s.get('reason')})
        if not stages:reasons=['No supported display geometry; original tags retained']
        if row['kind']=='tree_row' and not stages:
            reasons=['Mapped row generated no extra trunks after overlap suppression']
            status='represented_by_existing_trees'
        record={**row,'status':status,'exported_representations':len(exported),
                'imported_point_instances':placed,'imported_surface_objects':surfaces,'reasons':reasons}
        for key in ('source_area_m2','road_masked_area_m2','rendered_area_m2','unrendered_ground_area_m2'):
            values=[s[key] for s in stages if key in s]
            if values:record[key]=round(sum(values),3)
        if record.get('unrendered_ground_area_m2',0)>.05:record['status']='partially_rendered'
        if identifier in boundary:
            record.update(boundary[identifier])
            missing=record['missing_within_bbox_m2']
            if identifier==patch.get('source_id') and patch.get('package',{}).get('key') in covers['completed']:
                record['estimated_gap_area_m2']=patch['area_m2']
                record['gap_elevation_source']=patch['elevation_source']
                missing=max(0,missing-patch['area_m2'])
            record['remaining_missing_within_bbox_m2']=missing
            if missing<.05 and (placed or surfaces):record['status']='rendered'
        if identifier in references and surfaces:
            record.update(status='reference_only',representation=references[identifier]['representation'],reasons=[])
        records.append(record)
    totals=Counter(r['status'] for r in records);by_kind={}
    for kind in sorted({r['kind'] or 'unclassified' for r in records}):
        subset=[r for r in records if (r['kind'] or 'unclassified')==kind]
        by_kind[kind]={'fetched_sources':len(subset),**dict(Counter(r['status'] for r in subset))}
    result={'schema':'dynacity-greenery-coverage/1','raw_source':str(Path(args.raw).resolve()),
        'greenery_refresh':raw.get('greenery_refresh'),'bounds_lonlat':raw.get('query_bbox'),
        'source_objects':len(records),'status_counts':dict(totals),'by_kind':by_kind,
        'tree_display_instances':sum(point_ids[r['id']] for r in records if r['kind'] in ('tree','tree_row')),
        'cover_import_complete':len(covers['completed'])==len(covers['packages']) and not covers.get('pending'),
        'records':records,'limits':['OSM real-world completeness unknown. Counts apply only within the stated bounding box.',
        'Rendered means an imported source-linked mesh or instance, not that every feature was visually inspected.',
        'Tree-row trunks and missing dimensions are illustrative. No individual trees inferred inside park polygons.',
        'Original terrain retained; extended ground is an approximate filtered Copernicus surface estimate.']}
    output=Path(args.output);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Beirut OSM greenery coverage','',f"Mapped source objects: **{len(records):,}**.",
        f"Tree display instances imported: **{result['tree_display_instances']:,}**.",
        '', '| Category | Fetched | Rendered | Reference only | Partial | Skipped |', '| --- | ---: | ---: | ---: | ---: | ---: |']
    for kind,counts in by_kind.items():
        lines.append(f"| {kind} | {counts['fetched_sources']} | {counts.get('rendered',0)} | {counts.get('reference_only',0)} | {counts.get('partially_rendered',0)} | {counts.get('skipped',0)} |")
    lines+=['','Every source ID, tag set, extraction date, missing-area amount and skip reason is in the companion JSON.',
            '',*result['limits']]
    output.with_suffix('.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ['source_objects','status_counts','tree_display_instances','cover_import_complete','by_kind']},indent=2))


if __name__=='__main__':main()
