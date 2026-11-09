from types import SimpleNamespace
import numpy as np
import pytest
from shapely.geometry import LineString
from dynacity.terrain import GroundModel
from dynacity.engine_city_surface import terrain_mesh, road_ribbon, build_surface_scenes, kerb_walls, ROAD_LIFT, KERB_M
from dynacity.engine_street_context import TerrainSurface


def test_source_terrain_omits_missing_cells_and_keeps_upward_faces():
    z=np.arange(16,dtype=float).reshape(4,4); z[0,0]=np.nan
    ground=GroundModel('EPSG:32636',100,240,10,z)
    mesh=terrain_mesh(ground,[100,200,0])
    assert mesh['metadata']['valid_cells']==8
    vertices=np.asarray(mesh['vertices']); faces=np.asarray(mesh['triangles'])
    for tri in vertices[faces]:
        assert np.cross(tri[1]-tri[0],tri[2]-tri[0])[2]>0
        for vertex in tri:
            c=int(vertex[0]/10); r=int((40-vertex[1])/10)
            assert vertex[2]==z[r,c]
    assert [5.,35.,0.] not in vertices[np.unique(faces)].tolist()


def test_continuous_ribbon_retains_grade_and_rejects_unknown_ground():
    ground=GroundModel('EPSG:32636',0,100,10,np.full((10,10),30.))
    vertices,faces,path=road_ribbon(LineString([(20,50),(40,50),(40,70)]),6,ground,[20,50,30],0,5)
    assert np.asarray(path)[0]==pytest.approx([0,0,.12])
    assert np.asarray(path)[-1]==pytest.approx([20,20,5.12])
    assert len(vertices)==len(path)*2
    assert len(faces)==2*(len(path)-1)
    v=np.asarray(vertices)
    assert (np.cross(v[np.asarray(faces)[:,1]]-v[np.asarray(faces)[:,0]],
                     v[np.asarray(faces)[:,2]]-v[np.asarray(faces)[:,0]])[:,2]>0).all()
    with pytest.raises(ValueError,match='missing source ground'):
        road_ribbon(LineString([(0,50),(20,50)]),6,ground,[20,50,30])


def test_shared_network_has_no_district_cut_and_source_structure_triangles():
    ground=GroundModel('EPSG:32636',0,100,10,np.full((10,10),30.))
    source=np.array([[[20,20,35],[30,20,35],[20,30,35]]])
    pack=SimpleNamespace(meta={'crs':'EPSG:32636'},ground=ground,bridges=[],bridge_triangles=source)
    traffic=SimpleNamespace(crs='EPSG:32636',geometry=[[(20,50),(80,50)],[(20,60),(80,60)]],
        lanes=np.array([1,1]),oneway=np.array([0,0]),highway=['residential']*2,
        level=np.array([0,-1]),u=[1,3],v=[2,4],way_id=[123,124],name=['Example','Tunnel'])
    scenes,skipped=build_surface_scenes(pack,traffic,[20,50,30])
    assert [m['id'] for m in scenes['roads']['meshes']]==['road:0','marking:0','pavement:0']
    assert skipped==[{'road_id':1,'reason':'underground road'}]
    assert scenes['roads']['roads'][0]['source_way']==123
    mesh=scenes['structures']['meshes'][0]
    assert np.asarray(mesh['vertices'])==pytest.approx(source.reshape(-1,3)-[20,50,30])


def test_surface_cache_reuses_assets_and_recovers_missing_road_metadata(tmp_path,monkeypatch):
    import json
    import dynacity.engine_city_surface as module
    from dynacity.engine_city_surface import prepare_surface
    ground=GroundModel('EPSG:32636',0,100,10,np.full((10,10),30.))
    pack=SimpleNamespace(meta={'crs':'EPSG:32636'},ground=ground,bridges=[],bridge_triangles=np.zeros((0,3,3)))
    traffic=SimpleNamespace(crs='EPSG:32636',geometry=[[(20,50),(80,50)]],
        lanes=np.array([1]),oneway=np.array([0]),highway=['residential'],level=None,
        u=[1],v=[2],way_id=[123],name=['Example'])
    monkeypatch.setattr(module.Beirut001Pack,'load',lambda path:pack)
    monkeypatch.setattr(module.TrafficModel,'load',lambda path:traffic)
    source=tmp_path/'source'; source.write_bytes(b'source')
    first=prepare_surface(source,source,[20.,50.,30.],tmp_path/'assets')
    from pathlib import Path
    stamps={layer['asset']:Path(layer['asset']).stat().st_mtime_ns for layer in first['layers']}
    second=prepare_surface(source,source,[20.,50.,30.],tmp_path/'assets')
    assert second['cache_hit']
    assert stamps=={p:Path(p).stat().st_mtime_ns for p in stamps}
    Path(second['roads_metadata']).unlink()
    third=prepare_surface(source,source,[20.,50.,30.],tmp_path/'assets')
    assert not third['cache_hit']
    assert Path(third['roads_metadata']).is_file()
    changed=prepare_surface(source,source,[21.,50.,30.],tmp_path/'assets')
    assert changed['signature']!=third['signature']


def _flat_scene(geometry, lanes, oneway, highway, level=None, u=None, v=None):
    ground=GroundModel('EPSG:32636',0,200,10,np.full((20,20),30.))
    pack=SimpleNamespace(meta={'crs':'EPSG:32636'},ground=ground,bridges=[],bridge_triangles=np.zeros((0,3,3)))
    n=len(geometry)
    traffic=SimpleNamespace(crs='EPSG:32636',geometry=geometry,lanes=np.asarray(lanes),oneway=np.asarray(oneway),
        highway=highway,level=level,u=u or list(range(0,2*n,2)),v=v or list(range(1,2*n,2)),
        way_id=list(range(100,100+n)),name=['']*n)
    return build_surface_scenes(pack,traffic,[0,0,30])[0]['roads']['meshes']


def _area(mesh):
    v=np.asarray(mesh['vertices']);f=np.asarray(mesh['triangles'])
    a,b,c=v[f[:,0]],v[f[:,1]],v[f[:,2]]
    return np.abs(np.cross(b-a,c-a)[:,2]).sum()/2


def test_crossing_roads_share_one_unstacked_junction():
    from shapely.geometry import LineString
    lines=[[(20,100),(180,100)],[(100,20),(100,180)]]
    meshes=_flat_scene(lines,[1,1],[0,0],['residential','residential'])
    asphalt=[m for m in meshes if m['kind']=='road']
    assert sorted(m['id'] for m in asphalt)==['road:0','road:1']
    union=LineString(lines[0]).buffer(3.2,quad_segs=4).union(LineString(lines[1]).buffer(3.2,quad_segs=4))
    # Pieces add up to the union: the junction box is drawn exactly once.
    assert sum(_area(m) for m in asphalt)==pytest.approx(union.area,rel=1e-3)
    for m in asphalt:
        assert np.asarray(m['vertices'])[:,2]==pytest.approx(ROAD_LIFT)


def test_pavements_stand_one_kerb_above_asphalt_and_stay_off_the_carriageway():
    meshes=_flat_scene([[(20,100),(180,100)]],[1],[0],['residential'])
    pavement=next(m for m in meshes if m['kind']=='pavement')
    v=np.asarray(pavement['vertices'])
    top=v[np.isclose(v[:,2],ROAD_LIFT+KERB_M)]
    along=top[(top[:,0]>=20)&(top[:,0]<=180)]
    assert len(along) and (np.abs(along[:,1]-100)>=3.2-1e-6).all()
    assert (np.abs(along[:,1]-100)<=3.2+1.6+1e-6).all()
    assert v[:,2].min()==pytest.approx(-.05)
    # Fast roads carry no pavement.
    assert [m['kind'] for m in _flat_scene([[(20,100),(180,100)]],[2],[1],['trunk'])]==['road','marking']


def test_kerb_walls_face_out_of_the_slab():
    from shapely.geometry import box
    surface=TerrainSurface(GroundModel('EPSG:32636',0,100,10,np.zeros((10,10))))
    vertices,faces=kerb_walls(box(20,20,40,40).difference(box(25,25,35,35)),surface,[0,0,0],.3)
    v=np.asarray(vertices)
    for face in faces:
        a,b,c=v[face];normal=np.cross(b-a,c-a)[:2];middle=(a+b+c)[:2]/3
        inside=max(abs(middle[0]-30),abs(middle[1]-30))<5+1e-6
        # Shell faces point away from the centre; hole faces point into the hole.
        outward=np.dot(normal,middle-30)
        assert (outward<0) if inside else (outward>0)


def test_bridge_ramps_keep_ribbons_and_leave_a_gap_in_the_street_surface():
    lines=[[(20,100),(100,100)],[(100,100),(180,100)]]
    meshes=_flat_scene(lines,[1,1],[0,0],['primary','primary'],level=np.array([0,1]),u=[0,1],v=[1,2])
    ramp=next(m for m in meshes if m['id']=='road:1')
    assert len(ramp['vertices'])==2*len(np.unique(np.asarray(ramp['vertices'])[:,0]))
    street=next(m for m in meshes if m['id']=='road:0')
    assert np.asarray(street['vertices'])[:,0].max()<=100+1e-6


def _paint(meshes):
    return [m for m in meshes if m['kind']=='marking']


def test_two_way_road_gets_dashed_centre_and_solid_edge_lines():
    meshes=_flat_scene([[(20,100),(180,100)]],[1],[0],['residential'])
    paint=_paint(meshes)
    assert [m['id'] for m in paint]==['marking:0']
    v=np.asarray(paint[0]['vertices'])
    assert np.allclose(v[:,2],ROAD_LIFT+.02)
    # Centre line (y=100) is dashed; edge lines (y=100+-2.9) run unbroken.
    v=v-[0,100,0]
    centre=v[np.abs(v[:,1])<.1]; edge=v[np.abs(v[:,1]-2.9)<.1]
    assert len(centre) and len(edge)
    assert (np.diff(np.unique(np.round(centre[:,0],1)))>3).any()
    xs=np.unique(np.round(edge[:,0],1))
    assert xs.min()<=21 and xs.max()>=179
    # Paint stays inside the carriageway, below pavement height.
    assert (np.abs(v[:,1])<=3.2).all()


def test_multi_lane_oneway_gets_lane_dividers_and_service_roads_none():
    meshes=_flat_scene([[(20,100),(180,100)]],[3],[1],['primary'])
    v=np.asarray(_paint(meshes)[0]['vertices'])
    v=v-[0,100,0]
    # Width 9.6: dividers at +-1.6 from the centre, edge lines at +-4.5.
    assert any(abs(y-1.6)<.2 for y in np.unique(v[:,1])) and any(abs(y+1.6)<.2 for y in np.unique(v[:,1]))
    assert not _paint(_flat_scene([[(20,100),(180,100)]],[1],[0],['service']))
    assert not _paint(_flat_scene([[(20,100),(180,100)]],[1],[0],['living_street']))


def test_marking_is_not_painted_in_a_junction_owned_by_another_road():
    lines=[[(20,100),(180,100)],[(100,20),(100,180)]]
    meshes=_flat_scene(lines,[1,1],[0,0],['residential','residential'])
    owner_two=[m for m in _paint(meshes) if m['id']=='marking:1']
    v=np.asarray(owner_two[0]['vertices'])
    assert not ((np.abs(v[:,0]-100)<3.2-1e-6)&(np.abs(v[:,1]-100)<3.2-1e-6)).any()


def _half_known_primary(offset=3.):
    """Primary knows x<100 (NaN right half); fallback covers the whole 200 m square, `offset` below it."""
    truth=np.add.outer(np.zeros(20),np.linspace(30,50,20))
    primary=truth.copy(); primary[:,10:]=np.nan
    fallback=truth-offset
    return (GroundModel('EPSG:32636',0,200,10,primary),GroundModel('EPSG:32636',0,200,10,fallback),truth)


def test_merge_ground_fills_gap_without_a_seam_step():
    from dynacity.terrain import merge_ground,SRC_PACK,SRC_BLENDED,SRC_GLO30
    primary,fallback,truth=_half_known_primary()
    merged=merge_ground(primary,fallback)
    g=merged.ground
    assert np.isfinite(g).all()
    assert (g[:,:10]==primary.ground[:,:10]).all()
    # Offset recovered: the fallback is 3 m low everywhere.
    assert g[:,10:]==pytest.approx(truth[:,10:],abs=.05)
    assert np.abs(np.diff(g,axis=1)).max()<2.5
    assert set(np.unique(merged.provenance))=={SRC_PACK,SRC_BLENDED,SRC_GLO30}
    assert merged.source_at([5,105,195],[5,5,5]).tolist()==[SRC_PACK,SRC_BLENDED,SRC_GLO30]
    # Sea in the fallback stays a gap.
    fallback.ground[:,15:]=np.nan
    assert np.isnan(merge_ground(primary,fallback).ground[:,15:]).all()


def test_merge_ground_extends_the_grid_and_extrapolates_nearest_known():
    from dynacity.terrain import merge_ground,SRC_EXTRAPOLATED
    primary,fallback,_=_half_known_primary()
    merged=merge_ground(primary,fallback,extrapolate_m=30)
    assert merged.ground.shape==(26,26)
    assert np.isfinite(merged.sample([215],[100])).all()
    assert merged.source_at([215],[100])[0]==SRC_EXTRAPOLATED
    assert np.isnan(merged.sample([240],[100])).all()


def test_road_across_the_seam_is_finite_and_smooth():
    from dynacity.terrain import merge_ground
    primary,fallback,_=_half_known_primary(offset=4.)
    line=LineString([(20,100),(180,100)])
    with pytest.raises(ValueError,match='missing source ground'):
        road_ribbon(line,6,primary,[0,0,30])
    merged=merge_ground(primary,fallback)
    vertices,_,path=road_ribbon(line,6,merged,[0,0,30])
    z=np.asarray(path)[:,2]
    assert np.isfinite(vertices).all() and np.abs(np.diff(z)).max()<.55  # natural slope is .526 per 5 m
    from dynacity.engine_city_surface import ground_source
    assert ground_source(merged,line)=='blended'
    assert ground_source(merged,LineString([(20,100),(60,100)]))=='pack'
    assert ground_source(merged,LineString([(160,100),(190,100)]))=='glo30'


def test_surface_scenes_with_fallback_draw_roads_missing_pack_ground():
    primary,fallback,_=_half_known_primary()
    pack=SimpleNamespace(meta={'crs':'EPSG:32636'},ground=primary,bridges=[],bridge_triangles=np.zeros((0,3,3)))
    traffic=SimpleNamespace(crs='EPSG:32636',geometry=[[(20,50),(80,50)],[(20,100),(180,100)],[(120,60),(180,60)]],
        lanes=np.array([1,1,1]),oneway=np.array([0,0,0]),highway=['residential']*3,level=None,
        u=[0,2,4],v=[1,3,5],way_id=[1,2,3],name=['']*3)
    _,without=build_surface_scenes(pack,traffic,[0,0,30])
    assert {s['road_id'] for s in without}=={1,2}
    scenes,skipped=build_surface_scenes(pack,traffic,[0,0,30],fallback)
    assert skipped==[]
    assert [r['ground_source'] for r in scenes['roads']['roads']]==['pack','blended','glo30']
    assert scenes['terrain']['meshes'][0]['metadata']['valid_cells']>=19*(19+9)
