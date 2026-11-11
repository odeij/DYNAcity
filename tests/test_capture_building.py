import hashlib
import json

import numpy as np
import pytest

from dynacity.capture_building import fit_xy,omit_source_triangles
from dynacity.engine_asset import export_glb
from dynacity.engine_city_batches import Glb,batch_glb


def test_control_fit_preserves_scale_and_has_no_reflection():
    a=np.array([[0,0],[17,0],[17,18],[0,18.]])
    theta=np.deg2rad(182.);r=np.array([[np.cos(theta),-np.sin(theta)],[np.sin(theta),np.cos(theta)]])
    b=a@r.T+[732600,3753000]
    rr,t,error=fit_xy(a,b)
    assert np.allclose(rr,r)
    assert np.allclose(t,[732600,3753000])
    assert max(error)<1e-8
    assert np.linalg.det(rr)==pytest.approx(1)
    with pytest.raises(ValueError):fit_xy([[0,0],[1,0],[2,0]],[[0,0],[1,0],[2,0]])


def test_targeted_patch_retains_every_other_triangle_and_original_bytes(tmp_path):
    meshes=[{'id':'beirut001:'+name,'kind':'building','vertices':[[x,2,3],[x+2,2,3],[x,4,3]],
             'triangles':[[0,1,2]],'metadata':{'height_m':5,'source_index':x}}
            for name,x in [('a',0),('b',10),('c',20)]]
    scene={'coordinates':{'origin_utm_m':[730000,3750000,40]},'limitations':[],
           'bounds_lonlat':None,'meshes':meshes,'trees':[]}
    source=tmp_path/'scene.glb';export_glb(scene,source,include_calibration=False)
    jobs=batch_glb(source,lambda i,e:('Test','source'),tmp_path/'batch',kind='buildings')
    job=jobs[0];before=hashlib.sha256(open(job['asset'],'rb').read()).hexdigest()
    metadata=json.loads(open(job['metadata']).read());selected=metadata['objects'][1]
    output=tmp_path/'patched.glb';result=omit_source_triangles(job['asset'],job['metadata'],[selected['source_id']],output)
    assert before==hashlib.sha256(open(job['asset'],'rb').read()).hexdigest()
    original={n['name']:(p,pos,norm,tri) for n,p,pos,norm,tri in Glb(job['asset']).objects()}
    for node,primitive,pos,norm,tri in Glb(output).objects():
        _,old_pos,old_norm,old_tri=original[node['name']]
        np.testing.assert_array_equal(pos,old_pos);np.testing.assert_array_equal(norm,old_norm)
        start=selected['first_triangle'];count=selected['triangle_count']
        np.testing.assert_array_equal(tri,np.concatenate([old_tri[:start],old_tri[start+count:]]))
    assert result['removed'][0]['source_id']==selected['source_id']
    assert not result['source_modified']
    with pytest.raises(ValueError,match='Missing selected'):
        omit_source_triangles(job['asset'],job['metadata'],['missing'],tmp_path/'bad.glb')
