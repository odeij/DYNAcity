import pytest

from dynacity.engine_barrier_placements import barrier_placements


def test_barrier_models_follow_source_line_without_extending_it():
    context={'origin_utm_m':[1,2,3],'lines':[
        {'id':'way/10','instance_id':'way/10#0','kind':'jersey_barrier',
         'points_local_m':[[0,0,1],[3,0,2]],'as_of':'2026-10-27'},
        {'id':'way/11','kind':'wall','points_local_m':[[0,1,0],[5,1,0]]},
        {'id':'way/12','kind':'jersey_barrier','points_local_m':[], 'render_geometry':False}]}
    output=barrier_placements(context,1.5)
    assert output['origin_utm_m']==[1,2,3]
    assert [p['source_id'] for p in output['placements']]==['way/10','way/10']
    assert [p['position_local_m'] for p in output['placements']]==[[.75,0,1.25],[2.25,0,1.75]]
    assert all(p['scale_x']==1 for p in output['placements'])
    assert output['omitted']==[{'source_id':'way/12','reason':'no usable mapped ground line'}]


def test_reject_invalid_model_and_source_geometry():
    with pytest.raises(ValueError):barrier_placements({},0)
    with pytest.raises(ValueError):
        barrier_placements({'lines':[{'id':'way/1','kind':'jersey_barrier',
            'points_local_m':[[0,0,0],[float('nan'),1,0]]}]},1)
