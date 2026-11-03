"""Check saved scene coverage and present a newly supported conifer at its mapped location."""
from collections import Counter
import json
from pathlib import Path
import unreal


def verify():
    import city_world,street_context
    city_world._current();street_context.load_editor_geometry()
    root=city_world._root()/'Saved/DynaCity'
    instances=json.loads((root/'street-instances.json').read_text())
    covers=json.loads((root/'street-covers.json').read_text())
    actors={a.get_path_name():a for a in city_world._actors()}
    mismatches=[];tree_counts=Counter()
    for group in instances['groups']:
        actor=actors.get(group['actor'])
        count=actor.get_street_instance_count() if actor else None
        if count!=group['instances']:mismatches.append({'actor':group['actor'],'expected':group['instances'],'actual':count})
        if group['kind'].startswith('tree_') and count is not None:tree_counts[group['kind']]+=count
    missing_covers=[r['actor'] for r in covers['completed'].values() if r['actor'] not in actors]
    labels=Counter(a.get_actor_label() for a in actors.values() if 'DYNACITY_STREET_COVER' in a.tags)
    duplicates={name:count for name,count in labels.items() if count>1}
    saved=unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True,True)
    result={'tree_instances':dict(tree_counts),'total_tree_instances':sum(tree_counts.values()),
            'instance_groups':len(instances['groups']),'instance_count_mismatches':mismatches,
            'cover_packages':len(covers['packages']),'completed_cover_packages':len(covers['completed']),
            'missing_cover_actors':missing_covers,'duplicate_cover_labels':duplicates,
            'saved':bool(saved),'passed':not mismatches and not missing_covers and not duplicates and bool(saved)}
    (root/'greenery-native-verification.json').write_text(json.dumps(result,indent=2))
    return result


def focus_conifer():
    import city_world,explore_city,street_context
    street_context.load_editor_geometry();root=city_world._root()/'Saved/DynaCity'
    source=json.loads(Path(json.loads((root/'street-layer.json').read_text())['manifest']).read_text())
    point=next(p for p in source['points'] if p.get('leaf_type')=='needleleaved')
    centre=explore_city._world(point['position_local_m']);_,east,north,up=explore_city._basis()
    explore_city.stop_following();explore_city._look_at(centre-east*10-north*7+up*3,centre+up*3)
    return point['id']
