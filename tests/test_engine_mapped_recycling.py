import pytest
from dynacity.engine_mapped_recycling import recycling_placements

def test_only_explicit_containers_retained():
    p={'id':'node/1','kind':'recycling','position_local_m':[1,2,3], 'tags':{'recycling_type':'container'}}
    context={'schema':'dynacity-engine-street-context/1','points':[p,{**p,'id':'node/2','tags':{'recycling_type':'centre'}}]}
    rows=recycling_placements(context)['placements']
    assert len(rows)==1 and rows[0]['source_id']=='node/1' and rows[0]['position_local_m']==[1,2,3]
    assert rows[0]['orientation_source'].startswith('unknown')

def test_duplicate_and_invalid_rejected():
    p={'id':'node/1','kind':'recycling','position_local_m':[1,2,3], 'tags':{'recycling_type':'container'}}
    with pytest.raises(ValueError):recycling_placements({'schema':'dynacity-engine-street-context/1','points':[p,p]})
