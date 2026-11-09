from types import SimpleNamespace
import importlib.util
from pathlib import Path
import numpy as np
import pytest

from dynacity.landscape import landscape_payload
from dynacity.greenery import vegetation_kind
from dynacity.terrain import GroundModel


def test_new_greenery_tags_preserve_identity_tags_and_holes():
    ring=[{'lon':35.5,'lat':33.9},{'lon':35.501,'lat':33.9},
          {'lon':35.501,'lat':33.901},{'lon':35.5,'lat':33.9}]
    osm={'elements':[
        {'type':'node','id':1,'lon':35.5,'lat':33.9,'tags':{'natural':'shrub','height':'1.5 m'}},
        {'type':'way','id':2,'geometry':ring,'tags':{'landuse':'village_green'}},
        {'type':'way','id':3,'geometry':ring,'tags':{'landcover':'flowerbed'}},
        {'type':'way','id':4,'geometry':ring[:-1],'tags':{'barrier':'hedge','height':'2'}},
    ]}
    payload=landscape_payload(osm)
    rows={f['properties']['id']:f for f in payload['features']}
    assert len(payload['greenery_inventory'])==4
    assert rows['node/1']['properties']['height_m']==1.5
    assert rows['way/2']['properties']['kind']=='village_green'
    assert rows['way/3']['properties']['tags']['landcover']=='flowerbed'
    assert rows['way/4']['geometry']['type']=='LineString'
    assert payload['tree_nodes']==0


def test_roof_and_wetland_semantics():
    assert vegetation_kind({'building':'yes','roof:material':'grass'})=='green_roof'
    assert vegetation_kind({'natural':'wetland','wetland':'reedbed'})=='wetland'
    assert vegetation_kind({'highway':'footway','surface':'grass'}) is None


def test_terrain_extension_preserves_original_samples_and_unknown_sea():
    path=Path(__file__).resolve().parents[1]/'scripts/prepare_unreal_terrain_extension.py'
    spec=importlib.util.spec_from_file_location('terrain_extension',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source=np.full((12,12),15.);source[0,:]=np.nan
    primary=GroundModel('EPSG:32636',0,120,10,source,'primary')
    fallback_array=np.full((30,30),14.);fallback_array[:3,:]=np.nan
    fallback=GroundModel('EPSG:32636',-50,170,10,fallback_array,'coarse')
    merged,mask,report=module.merge_ground(primary,fallback,[-10,-10,130,130])
    finite=np.isfinite(primary.ground)
    r=int(round((merged.y0-primary.y0)/10));c=int(round((primary.x0-merged.x0)/10))
    assert np.array_equal(merged.ground[r:r+12,c:c+12][finite],source[finite])
    assert report['estimated_samples_added']>0
    assert report['median_alignment_offset_m']==pytest.approx(1.)
    assert np.isnan(merged.ground[0,0])
