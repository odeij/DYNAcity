import importlib.util,json,hashlib
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('pilot',ROOT/'scripts/build_granite_parapet_pilot.py');pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)
DATA=Path('D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction')
MANIFEST=DATA/'iteration3/corridor-manifest-v2.json'

def components(v,f):
    adj=[set() for _ in v]
    for tri in f:
        for i in tri:adj[int(i)].update(map(int,tri))
    unseen=set(range(len(v)));out=[]
    while unseen:
        seed=unseen.pop();todo=[seed];seen={seed}
        while todo:
            new=adj[todo.pop()]&unseen;unseen-=new;seen|=new;todo.extend(new)
        out.append(sorted(seen))
    return out

def assert_closed_positive(v,f):
    edges={}
    for tri in f:
        for a,b in zip(tri,np.roll(tri,-1)):
            key=tuple(sorted((int(a),int(b))));edges.setdefault(key,[]).append(1 if a<b else -1)
    assert all(len(x)==2 and sum(x)==0 for x in edges.values())
    v=v-v.mean(0);t=v[f];assert np.einsum('ij,ij->i',t[:,0],np.cross(t[:,1],t[:,2])).sum()/6>0

@pytest.mark.parametrize('edge,count',[(4,39),(1,52)])
def test_actual_parent_support_and_closed_volumes(edge,count):
    b=next(b for b in json.loads(MANIFEST.read_text())['buildings'] if b['key']=='granite');w=next(w for w in b['walls'] if w['edge']==edge);fr=w['frontage']
    parts=pilot.build_overlay_for_wall(edge,fr,w['plan_depth_m'],w['params']['floor_height_m'],.7)
    fixture=json.loads((DATA/'iteration27-granite-parapet/root-independent-parent-panels.json').read_text());parent=next(p for p in fixture if p['edge']==edge)['panel_bounds_local']
    mesh=pilot.rfg.mesh_for_profile(b['family'],width_m=fr['length_m'],height_m=fr['top_z']-fr['base_z'],depth_m=w['plan_depth_m'],floor_height_m=w['params']['floor_height_m'],side_facades={'left':'none','right':'none'})
    original=mesh.parts['glass_parapets'];digest=hashlib.sha256(np.asarray(original['verts']).tobytes()+np.asarray(original['faces']).tobytes()).hexdigest()
    assert digest==next(p for p in fixture if p['edge']==edge)['glass_arrays_sha256']
    assert len(parts)==count==len(parent)
    tangent=np.array(fr['tangent']);normal=np.array(fr['normal']);start=np.array(fr['start'])
    for p,box in zip(parts,parent):
        v=np.asarray(p['vertices']);rel=v[:,:2]-start
        local=np.column_stack([rel@tangent,-rel@normal,v[:,2]-fr['base_z']]);lo,hi=np.array(box['min']),np.array(box['max'])
        # Independent fixture component traversal may change order: match by x/z.
        matches=[b for b in parent if abs(b['min'][0]-local[:,0].min())<1e-5 and abs(b['min'][2]-local[:,2].min())<1e-5]
        assert len(matches)==1;lo,hi=np.array(matches[0]['min']),np.array(matches[0]['max'])
        np.testing.assert_allclose(local.min(0),[lo[0],lo[1]-.14,lo[2]],atol=2e-5)
        np.testing.assert_allclose(local.max(0),[hi[0],lo[1]-.02,lo[2]+.7*(hi[2]-lo[2])],atol=2e-5)
        assert local[:,2].ptp()<1.1 if hasattr(local[:,2],'ptp') else np.ptp(local[:,2])<1.1
        assert_closed_positive(v,np.asarray(p['triangles']))

def test_zero_and_rotated_translated_fixture():
    fr={'length_m':12.,'base_z':90.,'top_z':112.,'start':[345.,678.],'tangent':[.6,.8],'normal':[.8,-.6]}
    assert pilot.build_overlay_for_wall(4,fr,10.,3.,0)==[]
    parts=pilot.build_overlay_for_wall(4,fr,10.,3.,.7)
    for p in parts:
        assert p['vertices'][:,2].min()>=90
        assert p['vertices'][:,2].max()<=112
        assert_closed_positive(p['vertices'],p['triangles'])
    with pytest.raises(ValueError):pilot.build_overlay_for_wall(4,fr,10.,3.,float('nan'))

def test_exported_glb_components_and_axes():
    from dynacity.engine_city_batches import Glb
    path=DATA/'iteration27-granite-parapet/corrected/fraction-0.7/granite_parapet_overlay.glb'
    glb=Glb(path)
    objects=[]
    for node in glb.doc['nodes']:
        for primitive in glb.doc['meshes'][node['mesh']]['primitives']:
            objects.append((node,primitive,glb.accessor(primitive['attributes']['POSITION']),None,glb.accessor(primitive['indices']).reshape(-1,3)))
    assert objects
    count=0
    for node,primitive,pos,norm,faces in objects:
        enu=np.column_stack([pos[:,0],-pos[:,2],pos[:,1]])
        for ids in components(enu,faces):
            mapping={v:i for i,v in enumerate(ids)};selected=np.array([f for f in faces if int(f[0]) in mapping]);f=np.array([[mapping[int(i)] for i in t] for t in selected]);v=enu[ids]
            assert np.ptp(v[:,2])<1.1;assert v[:,2].min()>16;assert v[:,2].max()<58
            assert_closed_positive(v,f);count+=1
    assert count==91
