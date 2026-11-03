import json
import numpy as np
import pytest
from dynacity.engine_asset import export_glb
from dynacity.engine_city_batches import Glb,batch_glb


def scene():
    meshes=[]
    for name,x in [('a',0),('b',10),('c',20)]:
        meshes.append({'id':'beirut001:'+name,'kind':'building','vertices':[[x,2,3],[x+2,2,3],[x,4,3]],
                       'triangles':[[0,1,2]],'metadata':{'height_m':5,'source_index':x}})
    return {'coordinates':{'origin_utm_m':[730000,3750000,40]},'limitations':[],
            'bounds_lonlat':None,'meshes':meshes,'trees':[]}


def test_lossless_batches_preserve_every_object_geometry_normals_and_material(tmp_path):
    source=tmp_path/'source.glb';export_glb(scene(),source,include_calibration=False)
    original={node['extras']['source_id']:(primitive,pos.copy(),norm.copy(),tri.copy())
              for node,primitive,pos,norm,tri in Glb(source).objects()}
    jobs=batch_glb(source,lambda identifier,extras:('West' if extras['source_index']<20 else 'East','test owner'),tmp_path/'batch',kind='buildings')
    assert sum(job['object_count'] for job in jobs)==3
    found=[]
    for job in jobs:
        assert job['mesh_count']<=job['object_count']
        index=json.loads(open(job['metadata']).read())
        combined={node['name']:(primitive,pos,norm,tri) for node,primitive,pos,norm,tri in Glb(job['asset']).objects()}
        for item in index['objects']:
            found.append(item['source_id']);old_primitive,old_pos,old_norm,old_tri=original[item['source_id']]
            primitive,pos,norm,tri=combined[item['batch']]
            first=item['first_vertex'];last=first+item['vertex_count']
            np.testing.assert_array_equal(pos[first:last],old_pos)
            np.testing.assert_array_equal(norm[first:last],old_norm)
            start=item['first_triangle'];stop=start+item['triangle_count']
            np.testing.assert_array_equal(tri[start:stop]-first,old_tri)
            assert primitive['material']==old_primitive['material']
            local=old_pos[:,[0,2,1]].copy();local[:,1]*=-1
            assert item['local_bounds_m']==[local.min(axis=0).tolist(),local.max(axis=0).tolist()]
    assert sorted(found)==sorted(original)
    assert len(found)==len(set(found))


def test_transformed_or_duplicate_source_objects_fail_clearly(tmp_path):
    source=tmp_path/'calibration.glb';export_glb(scene(),source)
    with pytest.raises(ValueError,match='untransformed'):
        batch_glb(source,lambda identifier,extras:('A','test'),tmp_path/'batch',kind='buildings')
    duplicate=scene();duplicate['meshes'][1]['id']=duplicate['meshes'][0]['id']
    source=tmp_path/'duplicate.glb';export_glb(duplicate,source,include_calibration=False)
    with pytest.raises(ValueError,match='Duplicate source'):
        batch_glb(source,lambda identifier,extras:('A','test'),tmp_path/'batch',kind='buildings')
