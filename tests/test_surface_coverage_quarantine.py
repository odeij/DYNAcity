import importlib.util
from pathlib import Path
from unittest.mock import patch
import numpy as np

spec=importlib.util.spec_from_file_location('surface_coverage',Path(__file__).parents[1]/'scripts/query_native_surface_coverage.py')
surface=importlib.util.module_from_spec(spec);spec.loader.exec_module(surface)

def test_invalid_range_quarantines_entire_package_but_preserves_other_geometry():
    box={'x_min':0,'x_max':2,'y_min':0,'y_max':2,'z_min':0,'z_max':2}
    base={'global_object_id':0,'package_kind':'footway','production_area':'test','source_id':'valid-looking-other-object','material_index':0,'triangle_start':0,'triangle_count':1,'bounds_local_m':{'min':[0,0,0],'max':[1,1,1]}}
    bad=dict(base,package_key='uncertain',asset_path='must-not-open.glb')
    good=dict(base,package_key='valid',asset_path='valid.glb',global_object_id=1)
    class Geometry:
        def __init__(self,path):
            assert str(path)=='valid.glb'
        def objects(self):
            yield {},{},np.array([[0,0,0],[1,0,0],[0,0,-1.]]),None,np.array([[0,1,2]])
    with patch.object(surface,'Glb',Geometry):
        result=surface.query_aabb({'objects':[bad,good],'invalid_source_ranges':[{'package':'uncertain','source_id':'different-truncated-object'}]},box)
    assert result['quarantined_packages']==['uncertain']
    assert [m['package_key'] for m in result['matches']]==['valid']
    assert result['matches'][0]['triangles_local_enu']==[[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]]
