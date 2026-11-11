from dynacity.engine_mapped_fixed_cameras import fixed_camera_placements

def test_explicit_type_mount_and_bearing_are_respected():
    row={'id':'node/1','kind':'man_made_surveillance','position_local_m':[1,2,3],
         'tags':{'camera:type':'fixed','camera:mount':'pole','camera:direction':'180'}}
    result=fixed_camera_placements({'points':[row]})
    assert result['placements'][0]['heading_degrees']==270
    assert result['placements'][0]['source_id']=='node/1'
    row['tags']['camera:direction']='unknown'
    assert fixed_camera_placements({'points':[row]})['skipped'][0]['reason']=='missing numeric camera direction'
    row['tags']['camera:type']='dome'
    assert fixed_camera_placements({'points':[row]})['placements']==[]
