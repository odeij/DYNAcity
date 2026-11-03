"""Visual repairs must not replace measured heights or fill open water."""
import importlib.util
from pathlib import Path
import numpy as np
from shapely.geometry import box
from dynacity.terrain import GroundModel

spec=importlib.util.spec_from_file_location('terrain_gap_repair',Path(__file__).resolve().parents[1]/'scripts/prepare_unreal_terrain_gap_repair.py')
repair=importlib.util.module_from_spec(spec);spec.loader.exec_module(repair)


def ground(values):return GroundModel('EPSG:32636',0,70,10,values,'test')


def test_only_enclosed_gap_changes_and_source_heights_are_preserved():
    a=np.arange(49,dtype=float).reshape(7,7);a[3,3]=np.nan;a[0:2,0]=np.nan
    fixed,records=repair.repair_grid(ground(a))
    assert np.isfinite(fixed.ground[3,3])
    assert np.isnan(fixed.ground[0:2,0]).all()
    assert np.array_equal(a[np.isfinite(a)],fixed.ground[np.isfinite(a)])
    assert len(records)==1


def test_mapped_water_and_large_gaps_remain_missing():
    a=np.ones((7,7));a[3,3]=np.nan
    fixed,records=repair.repair_grid(ground(a),box(30,30,40,40))
    assert np.isnan(fixed.ground[3,3])
    assert records[0]['status']=='retained water'
    fixed,records=repair.repair_grid(ground(a),max_distance_m=5)
    assert np.isnan(fixed.ground[3,3])
    assert records[0]['status']=='retained large gap'
