from types import SimpleNamespace
import math
import numpy as np
import pytest
from pyproj import Transformer
from shapely.geometry import Polygon,Point,mapping

from dynacity.engine_street_context import TerrainSurface,parse_heading,arrow_tag,build_street_context
from dynacity.terrain import GroundModel


def test_surface_matches_source_triangles_not_bilinear_saddle():
    ground=GroundModel('EPSG:32636',0,20,10,np.array([[0.,0.],[0.,100.]]))
    surface=TerrainSurface(ground)
    assert surface.sample(10,10)==pytest.approx(0)
    assert ground.sample([10],[10])[0]==pytest.approx(25)
    assert surface.sample(12.5,7.5)==pytest.approx(50)
    assert surface.sample(-1,10) is None


def test_cover_clips_exact_terrain_and_preserves_polygon_holes():
    ground=GroundModel('EPSG:32636',0,40,10,np.arange(16,dtype=float).reshape(4,4))
    surface=TerrainSurface(ground)
    poly=Polygon([(5,5),(35,5),(35,35),(5,35)],holes=[[(15,15),(25,15),(25,25),(15,25)]])
    vertices,faces,missing=surface.drape(poly,[0,0,0])
    assert not missing
    covered=0
    for face in faces:
        tri=Polygon([vertices[i][:2] for i in face]);covered+=tri.area
        assert poly.covers(tri.representative_point())
        for i in face:
            x,y,z=vertices[i]
            # Boundary samples at the max grid edges are intentionally outside
            # sample(), while draping still uses their known source vertices.
            expected=surface.sample(x,y)
            if expected is not None:assert z==pytest.approx(expected+.03,abs=1e-4)
    assert covered==pytest.approx(poly.area)


def test_unknown_ground_is_omitted_instead_of_filled():
    ground=GroundModel('EPSG:32636',0,20,10,np.array([[0.,0.],[0.,np.nan]]))
    surface=TerrainSurface(ground)
    assert surface.sample(10,10) is None
    vertices,faces,missing=surface.drape(Polygon([(5,5),(15,5),(15,15),(5,15)]),[0,0,0])
    assert not vertices and not faces and missing


def test_heading_and_arrow_tags_do_not_invent_lane_arrows():
    assert parse_heading('N')==90
    assert parse_heading('90')==0
    assert parse_heading('backward') is None
    assert parse_heading('nan') is None
    assert arrow_tag({'turn:lanes':'left|through|right'}) is None
    assert arrow_tag({'traffic_signals:arrow':'left'})=='left'
    assert arrow_tag({'traffic_signals:arrows':'left;right'})=='left_right'


def test_signal_curb_inference_crossing_markings_and_missing_ground():
    x0,y0=730000.,3750000.
    ground=GroundModel('EPSG:32636',x0,y0+100,10,np.full((10,10),12.))
    pack=SimpleNamespace(ground=ground)
    traffic=SimpleNamespace(geometry=[[(x0+10,y0+50),(x0+90,y0+50)]],
        level=np.array([0]),lanes=np.array([1]),oneway=np.array([0]),highway=['residential'],name=['Mapped Street'])
    inverse=Transformer.from_crs(32636,4326,always_xy=True).transform
    features=[]
    for kind,offset,tags in [('traffic_signals',(50,50),{'traffic_signals:arrow':'right'}),
                              ('crossing',(60,50),{'crossing':'unmarked'}),
                              ('tree',(200,50),{})]:
        lonlat=inverse(x0+offset[0],y0+offset[1])
        features.append({'type':'Feature','geometry':mapping(Point(*lonlat)),
                         'properties':{'id':'node/'+kind,'kind':kind,'tags':tags}})
    result=build_street_context({'type':'FeatureCollection','features':features},pack,traffic,[x0,y0,10])
    signal=next(p for p in result['points'] if p['kind']=='traffic_signals')
    assert signal['position_local_m']==pytest.approx([50,46.15,2],abs=1e-3)
    assert signal['heading_degrees']==pytest.approx(180)
    assert signal['arrow']=='right' and signal['signal_phase'].startswith('unknown')
    assert signal['position_source'].startswith('Inferred right curb')
    assert not next(p for p in result['points'] if p['kind']=='crossing')['paint_markings']
    assert result['skipped']['tree:missing_source_ground']==1


def test_hydrant_export_preserves_mapped_position_and_source_identity():
    x0,y0=730000.,3750000.
    ground=GroundModel('EPSG:32636',x0,y0+100,10,np.full((10,10),12.))
    traffic=SimpleNamespace(geometry=[[(x0+10,y0+50),(x0+90,y0+50)]],
        level=np.array([0]),lanes=np.array([1]),oneway=np.array([0]),highway=['residential'],name=['Street'])
    lonlat=Transformer.from_crs(32636,4326,always_xy=True).transform(x0+50,y0+51)
    feature={'type':'Feature','geometry':mapping(Point(*lonlat)),
             'properties':{'id':'node/42','kind':'fire_hydrant','tags':{'emergency':'fire_hydrant'}}}
    result=build_street_context({'type':'FeatureCollection','features':[feature]},
                                SimpleNamespace(ground=ground),traffic,[x0,y0,10])
    point=result['points'][0]
    assert point['id']=='node/42' and point['tags']['emergency']=='fire_hydrant'
    assert point['position_local_m']==pytest.approx([50,51,2],abs=1e-3)
    assert point['source_position_lonlat']==pytest.approx(lonlat)
    assert point['position_source'].startswith('OSM mapped node')


def test_infrastructure_points_and_nonpoint_references_keep_source_geometry():
    from shapely.geometry import LineString
    x0,y0=730000.,3750000.
    ground=GroundModel('EPSG:32636',x0,y0+100,10,np.full((10,10),12.))
    traffic=SimpleNamespace(geometry=[[(x0+10,y0+50),(x0+90,y0+50)]],
        level=np.array([0]),lanes=np.array([1]),oneway=np.array([0]),highway=['residential'],name=['Street'])
    inverse=Transformer.from_crs(32636,4326,always_xy=True).transform
    point=mapping(Point(*inverse(x0+50,y0+51)))
    line=mapping(LineString([inverse(x0+40,y0+51),inverse(x0+60,y0+51)]))
    features=[{'type':'Feature','geometry':point,'properties':{'id':'node/1','kind':'man_made_street_cabinet',
                 'context_group':'infrastructure','tags':{'man_made':'street_cabinet'}}},
              {'type':'Feature','geometry':line,'properties':{'id':'way/2','kind':'power_line',
                 'context_group':'infrastructure','tags':{'power':'line'}}}]
    result=build_street_context({'type':'FeatureCollection','features':features},
                                SimpleNamespace(ground=ground),traffic,[x0,y0,10])
    assert result['points'][0]['id']=='node/1'
    assert result['points'][0]['position_local_m']==pytest.approx([50,51,2],abs=1e-3)
    assert [r['id'] for r in result['infrastructure_references']]==['node/1','way/2']
    assert result['infrastructure_references'][1]['geometry_lonlat']==line
    assert next(a for a in result['feature_audit'] if a['id']=='way/2')['status']=='reference_only'
    assert not result['lines']

