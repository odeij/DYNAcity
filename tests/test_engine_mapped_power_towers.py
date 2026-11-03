import pytest
from dynacity.engine_mapped_power_towers import power_tower_placements

def test_source_nodes_only_and_unknown_dimensions_stay_explicit():
    row={'id':'node/123','kind':'power_tower','position_local_m':[1,2,3],'tags':{'power':'tower'}}
    result=power_tower_placements({'points':[row,{'kind':'bench'}],'origin_utm_m':[0,0,0]})
    assert result['placements'][0]['source_id']=='node/123'
    assert result['placements'][0]['position_local_m']==[1,2,3]
    assert 'unknown' in result['placements'][0]['height_source']
    assert 'unknown' in result['placements'][0]['heading_source']

def test_duplicate_and_nonfinite_points():
    row={'id':'node/1','kind':'power_tower','position_local_m':[1,2,float('nan')]}
    assert len(power_tower_placements({'points':[row]})['skipped'])==1
    with pytest.raises(ValueError):power_tower_placements({'points':[row,row]})
