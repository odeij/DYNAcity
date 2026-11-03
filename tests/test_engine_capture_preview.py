import numpy as np
from conftest import write_las
from dynacity.engine_capture_preview import prepare_preview
from dynacity.las import LASHeader


def test_subset_keeps_exact_source_records_and_updates_header(tmp_path):
    source=write_las(tmp_path/'source.las',[(73297000+i,375300000+i,8000+i,0,10+i,20+i,30+i) for i in range(10)])
    original=source.read_bytes();output=tmp_path/'preview.las'
    result=prepare_preview(source,output,max_points=3)
    a=LASHeader.read(source);b=LASHeader.read(output)
    assert result['stride']==4 and b.point_count==3
    assert source.read_bytes()==original
    source_records=original[a.point_data_offset:]
    expected=b''.join(source_records[i*a.point_record_length:(i+1)*a.point_record_length] for i in [0,4,8])
    assert output.read_bytes()[b.point_data_offset:]==expected
    assert b.scale==a.scale and b.offset==a.offset
    assert b.bounds[0]==a.bounds[0]
    assert b.bounds[1]<a.bounds[1]
