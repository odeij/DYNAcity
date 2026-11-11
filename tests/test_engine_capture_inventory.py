from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import struct
from dynacity.engine_capture_inventory import las_record,glb_record


def test_online_only_file_is_not_opened(tmp_path):
    path=tmp_path/'placeholder.las';path.write_bytes(b'not a real LAS')
    with patch('dynacity.engine_capture_inventory.LASHeader.read',side_effect=AssertionError('must not hydrate')):
        record,footprint=las_record(path,resident=False)
    assert record['availability']=='online_only' and footprint is None


def test_export_creation_date_is_not_used_as_acquisition_date(tmp_path):
    path=tmp_path/'capture.las';path.write_bytes(b'x')
    header=SimpleNamespace(creation_year=2026,creation_day_of_year=201,bounds=(730000,730010,3750000,3750010,5,25),
                           point_count=8,has_rgb=True,as_dict=lambda:{'embedded_crs':None})
    with patch('dynacity.engine_capture_inventory.LASHeader.read',return_value=header):
        record,feature=las_record(path,resident=True)
    assert record['las_creation_date']=='2026-07-20'
    assert record['acquisition_date'] is None
    assert feature['geometry']['coordinates'][0][0]==feature['geometry']['coordinates'][0][-1]
