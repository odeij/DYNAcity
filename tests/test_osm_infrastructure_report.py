from scripts.report_osm_infrastructure import report
import pytest


def test_reconciliation_distinguishes_export_reference_and_saved_index():
    refs=[{'id':'node/1','kind':'traffic_sign'}, {'id':'node/2','kind':'traffic_sign'},
          {'id':'node/3','kind':'fire_hydrant'}, {'id':'way/4','kind':'power_line'}]
    source={'infrastructure_references':refs,'points':[{'id':'node/1'},{'id':'node/2'}],
            'unplaced_points':[{'id':'node/3'}]}
    instances={'instances':[{'source_id':'node/1'}]}
    result=report(source,instances)
    assert result['status_counts']=={'listed_in_saved_instance_index':1,
        'exported_point_not_in_saved_index':1,'missing_ground_no_point_placement':1,
        'source_reference_only':1}
    assert result['records'][0]['saved_index_instances']==1
    assert 'do not prove live visibility' in result['limits'][0]


def test_only_verified_actor_reload_overrides_old_index_status():
    source={'infrastructure_references':[{'id':'node/1','kind':'camera'},
                                         {'id':'node/2','kind':'camera'}],
            'points':[{'id':'node/1'},{'id':'node/2'}]}
    reports=[{'reloaded_external_actors':True,'groups':[{'source_ids':['node/1']}]},
             {'groups':[{'source_ids':['node/2']}]}]
    result=report(source,{'instances':[]},reports)
    assert result['status_counts']=={'verified_after_actor_reload':1,
                                     'exported_point_not_in_saved_index':1}


def test_explicit_legacy_reload_requires_matching_counts():
    source={'infrastructure_references':[{'id':'node/1','kind':'traffic_calming'}]}
    legacy={'groups':[{'count':1,'source_ids':['node/1']}],'total':1}
    assert report(source,{},legacy_verified=[legacy])['status_counts']=={'verified_after_actor_reload':1}
    with pytest.raises(ValueError):
        report(source,{},legacy_verified=[{**legacy,'total':2}])
