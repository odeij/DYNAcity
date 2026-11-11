"""Independent acceptance checks of exported upper-front geometry."""
import copy
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
import numpy as np
import pytest
from dynacity.engine_city_batches import Glb
from dynacity import reference_facade_geometry as G
from scripts.build_tower_infill_pilot import build_additive_pilot, load_tower_record

@pytest.fixture
def tower():
    t=np.array([.6,.8]); n=np.array([.8,-.6])
    return dict(key="tower",source_id_full="beirut001:bde30f76-7868-4187-92a7-a18270c58815",
        source_height_m=30.,base_z_local_m=11.,height_override={"est":50.},
        walls=[dict(edge=6,plan_depth_m=20.,params={"floor_height_m":3.1,"open_floors":0},
        frontage=dict(index=6,start=[123.,456.],tangent=t.tolist(),normal=n.tolist(),
        length_m=16.,base_z=11.,top_z=41.))])

def primitives(report):
    g=Glb(report["geometry"])
    for mesh in g.doc["meshes"]:
        for p in mesh["primitives"]:
            a=g.accessor(p["attributes"]["POSITION"]).astype(float)
            v=np.column_stack([a[:,0],-a[:,2],a[:,1]])
            yield g.doc["materials"][p["material"]]["name"],v,g.accessor(p["indices"]).reshape(-1,3)

def build(tmp_path,tower,frac=.7):
    return json.loads(Path(build_additive_pilot(frac,tmp_path,tower_data=tower)).read_text())

def local(v,tower):
    f=tower["walls"][0]["frontage"]; xy=v[:,:2]-f["start"]
    return np.column_stack([xy@np.array(f["tangent"]),-xy@np.array(f["normal"]),v[:,2]-f["base_z"]])

def test_wrong_identity_refused(tmp_path,tower):
    tower["source_id_full"]="beirut001:other"
    with pytest.raises(ValueError,match="Wrong source"):
        build(tmp_path,tower)
    path=tmp_path/"wrong.json";path.write_text(json.dumps({"buildings":[tower]}))
    with pytest.raises(ValueError):
        load_tower_record(path)

def test_zero_has_no_mesh(tmp_path,tower):
    r=build(tmp_path,tower,0)
    assert r["geometry"] is None and r["verification"]["n_parts"]==0
    assert not list(tmp_path.glob("*.glb"))

@pytest.mark.parametrize("frac",[.7,.85])
def test_export_world_height_and_coverage(tmp_path,tower,frac):
    r=build(tmp_path,tower,frac)
    vs=np.concatenate([v for _,v,_ in primitives(r)])
    assert np.isfinite(vs).all()
    assert vs[:,2].min()>=41.-1e-4 and vs[:,2].max()<=61.+1e-4
    assert abs(vs[:,2].min()-41.)<.01
    assert abs(vs[:,2].max()-61.)<.01
    q=local(vs,tower)
    assert q[:,0].min()>=16*(1-frac)-1e-4 and q[:,0].max()<=16+1e-4
    assert q[:,1].min()>=.049 and q[:,1].max()<=.281

def test_each_export_component_closed_outward(tmp_path,tower):
    r=build(tmp_path,tower)
    for _,v,faces in primitives(r):
        adj={}
        for i,tri in enumerate(faces):
            for vertex in tri: adj.setdefault(int(vertex),set()).add(i)
        remaining=set(range(len(faces)))
        while remaining:
            stack=[remaining.pop()]; group=[]
            while stack:
                i=stack.pop(); group.append(i)
                for vertex in faces[i]:
                    for j in adj[int(vertex)]:
                        if j in remaining: remaining.remove(j);stack.append(j)
            ff=faces[group]; edges=Counter()
            for a,b,c in ff:
                edges.update([(int(a),int(b)),(int(b),int(c)),(int(c),int(a))])
            assert all(count==1 and edges[(b,a)]==1 for (a,b),count in edges.items())
            centered=v-v[np.unique(ff)].mean(axis=0)
            volume=sum(np.dot(centered[a],np.cross(centered[b],centered[c])) for a,b,c in ff)/6
            assert volume>1e-7

def covered_at(vertices,faces,x,z):
    for tri in vertices[faces][:,:,[0,2]]:
        a,b,c=tri
        matrix=np.column_stack([b-a,c-a])
        if abs(np.linalg.det(matrix))<1e-10:continue
        u,w=np.linalg.solve(matrix,np.array([x,z])-a)
        if u>=-1e-8 and w>=-1e-8 and u+w<=1+1e-8:return True
    return False

def test_actual_window_aperture(tmp_path,tower):
    r=build(tmp_path,tower)
    concrete=[(local(v,tower),f) for name,v,f in primitives(r) if name=="concrete_pale"]
    center=(16*(1-.7)+16)/2; z=30+3.1/2
    assert not any(covered_at(v,f,center,z) for v,f in concrete)
    assert any(covered_at(v,f,16*(1-.7)+.2,z) for v,f in concrete)

def test_default_tower_parts_unchanged():
    oldpath=Path("D:/private/dynacity-data/engine/resemblance-20261101-2355/reconstruction/iteration15-corner-bands/root-verification/old_geometry.py")
    if not oldpath.exists(): pytest.skip("Retained private baseline unavailable")
    spec=importlib.util.spec_from_file_location("tower_acceptance_baseline",oldpath)
    old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
    kwargs=dict(depth_m=23.62,floor_height_m=3.1,params={"open_floors":0},side_facades={"left":"none","right":"none"})
    before=old.mesh_for_profile("construction_tower_frame",16.171,40.,**kwargs)
    after=G.mesh_for_profile("construction_tower_frame",16.171,40.,**kwargs)
    assert before.parts.keys()==after.parts.keys()
    for name in before.parts:
        np.testing.assert_array_equal(before.parts[name]["verts"],after.parts[name]["verts"])
        np.testing.assert_array_equal(before.parts[name]["faces"],after.parts[name]["faces"])
