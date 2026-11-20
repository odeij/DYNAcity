"""Pure-Python rules behind the Unreal corridor canopy estimate (no Unreal needed; synthetic fixtures)."""
import importlib.util
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

_PATH = Path(__file__).resolve().parents[1] / 'engine' / 'unreal' / 'Content' / 'Python' / 'canopy_unreal.py'
_spec = importlib.util.spec_from_file_location('canopy_unreal_under_test', _PATH)
cu = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cu  # dataclasses resolves string annotations through sys.modules
_spec.loader.exec_module(cu)

ORIGIN = [730784.7582802251, 3751551.1589951646, 51.10578950470706]


def feature(fid, lon, lat, height, on_building=False):
    return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {'id': fid, 'canopy_height_at_peak_m': height, 'on_building_footprint': on_building}}


def test_city_basis_maps_local_metres_to_unreal_centimetres_with_south_as_plus_y():
    x, y, z = cu.CITY_BASIS.to_unreal(100.0, 50.0, 2.0)
    assert (x, y, z) == (10000.0, -5000.0, 200.0)
    assert cu.CITY_BASIS.to_local(x, y, z) == pytest.approx((100.0, 50.0, 2.0))


def test_basis_round_trips_through_dict_and_a_shifted_origin():
    basis = cu.Basis((500.0, -200.0, 10.0), (100.0, 0.0, 0.0), (0.0, -100.0, 0.0), (0.0, 0.0, 100.0))
    assert cu.Basis.from_dict(basis.as_dict()) == basis
    assert basis.to_unreal(1.0, 1.0, 1.0) == (600.0, -300.0, 110.0)
    assert basis.to_local(600.0, -300.0, 110.0) == pytest.approx((1.0, 1.0, 1.0))


def test_lonlat_chain_reproduces_the_project_local_position_of_a_known_osm_tree():
    pytest.importorskip('pyproj')
    # OSM node/2909398381: source_position_lonlat and the exporter's position_local_m (street-context-fresh.json).
    e, n = cu.lonlat_to_local(35.5586935, 33.8917441, ORIGIN)
    assert (e, n) == pytest.approx((5834.338, 1549.0725), abs=0.01)
    ux, uy, _ = cu.CITY_BASIS.to_unreal(e, n, 0.0)
    assert (ux, uy) == pytest.approx((583433.8, -154907.25), abs=1.0)


def test_select_candidates_drops_footprints_and_low_crowns_and_counts_them():
    feats = [feature('a', 35.5, 33.9, 7.0), feature('b', 35.5, 33.9, 9.0, on_building=True),
             feature('c', 35.5, 33.9, 2.0), feature('d', 35.5, 33.9, 3.0)]
    kept, dropped = cu.select_candidates(feats)
    assert [f['properties']['id'] for f in kept] == ['a', 'd']
    assert dropped == {'on_building_footprint': 1, 'below_min_height': 1}


@pytest.mark.parametrize('label,expected', [
    ('source-ground', ('ground', 'source-ground')),
    ('StreetCover_TerrainExtension', ('ground', 'StreetCover_TerrainExtension')),
    ('terrain-f9403817fb68a10d', ('ground', 'terrain')),
    ('roads_achrafieh_material_2', ('road', 'road mesh')),
    ('structures-f9403817fb68a', ('road', 'transport structure')),
    ('buildings_mazraa_material_3', ('building', 'building mesh')),
    ('buildings-context-e487-n3', ('building', 'building mesh')),
    ('Corridor visual - buildings_remeil_material_9', ('building', 'building mesh')),
    ('DynaCity_Sea', ('water_surface', 'DynaCity_Sea')),
    ('StreetCover_ain_el_mraisse_parking_9f1bda80_scene0', ('road', 'paved vehicle cover')),
    ('StreetCover_zoqaq_el_blat_park_9f1bda80', ('ground', 'street cover')),
    ('Parked car 7', ('other', 'Parked car 7')),
    (None, ('none', 'none')),
])
def test_classify_surface(label, expected):
    assert cu.classify_surface(label) == expected


def test_clearance_verdict_orders_trunk_rules_before_clearance_rules():
    ground = ('ground', 'source-ground')
    road = ('road', 'road mesh')
    building = ('building', 'building mesh')
    ring_ok = [ground] * 8
    assert cu.clearance_verdict(ground, ring_ok) is None
    assert cu.clearance_verdict(road, ring_ok) == 'trunk_on_carriageway'
    assert cu.clearance_verdict(building, ring_ok) == 'trunk_inside_building'
    assert cu.clearance_verdict(('water_surface', 'DynaCity_Sea'), []) == 'no_unreal_terrain'
    assert cu.clearance_verdict(('none', 'none'), []) == 'no_surface_hit'
    assert cu.clearance_verdict(ground, [ground] * 7 + [road]) == 'carriageway_within_clearance'
    assert cu.clearance_verdict(ground, [ground] * 6 + [building, ground]) == 'building_within_clearance'
    # a road in the ring wins over a building in the ring (both are skips; the order is fixed so counts are reproducible)
    assert cu.clearance_verdict(ground, [road, building] + [ground] * 6) == 'carriageway_within_clearance'


def test_mesh_scale_matches_estimated_height_and_rejects_unreasonable_scales():
    assert cu.mesh_scale(4.55674, 455.674) == pytest.approx(1.0)
    assert cu.mesh_scale(24.0, 455.674) == pytest.approx(5.267, abs=0.001)
    assert cu.mesh_scale(3.0, 455.674) == pytest.approx(0.658, abs=0.001)
    with pytest.raises(ValueError):
        cu.mesh_scale(60.0, 455.674)
    with pytest.raises(ValueError):
        cu.mesh_scale(0.0, 455.674)


def test_ring_offsets_are_on_the_requested_radius_and_evenly_spaced():
    ring = cu.ring_offsets_cm(150.0, 8)
    assert len(ring) == 8
    assert all(math.hypot(x, y) == pytest.approx(150.0) for x, y in ring)
    assert ring[0] == pytest.approx((150.0, 0.0))
    assert ring[2] == pytest.approx((0.0, 150.0), abs=1e-9)


def test_land_mask_treats_nan_and_off_grid_as_water():
    grid = np.full((4, 4), 5.0)
    grid[0, 0] = np.nan
    ground = SimpleNamespace(x0=1000.0, y0=2000.0, cell_m=10.0, ground=grid)
    assert cu.land_mask_is_water(ground, 1005.0, 1995.0) is True      # NaN cell (sea)
    assert cu.land_mask_is_water(ground, 1015.0, 1985.0) is False     # land
    assert cu.land_mask_is_water(ground, 999.0, 1995.0) is True       # west of the grid
    assert cu.land_mask_is_water(ground, 1015.0, 2005.0) is True      # north of the grid


def test_carriageway_index_uses_half_the_lane_derived_width_and_a_margin():
    pytest.importorskip('shapely')
    origin = [1000.0, 2000.0, 0.0]
    index = cu.carriageway_index([([(1000.0, 2000.0), (1100.0, 2000.0)], 7.0)], origin)  # east-west road, 7 m wide
    assert cu.on_carriageway(index, 50.0, 3.4) is True
    assert cu.on_carriageway(index, 50.0, 3.6) is False
    assert cu.on_carriageway(index, 50.0, 3.6, margin_m=0.5) is True
    assert cu.on_carriageway(index, 150.0, 0.0) is False              # beyond the flat end cap


def test_height_distribution_percentiles():
    d = cu.height_distribution([3, 3, 4, 5, 9, 24])
    assert d['count'] == 6 and d['min'] == 3 and d['max'] == 24
    assert d['median'] == pytest.approx(4.5)
    assert d['by_metre']['3'] == 2
    assert cu.height_distribution([]) == {'count': 0}


def test_random_sample_is_reproducible_and_bounded():
    items = list(range(100))
    assert cu.random_sample(items, 20, seed=1) == cu.random_sample(items, 20, seed=1)
    assert len(cu.random_sample(items, 500)) == 100


def test_build_plan_filters_and_converts_with_synthetic_inputs():
    pytest.importorskip('pyproj')
    pytest.importorskip('shapely')
    lon, lat = 35.5586935, 33.8917441
    candidates = {'features': [feature('keep', lon, lat, 8.0), feature('roof', lon, lat, 8.0, on_building=True),
                               feature('short', lon, lat, 2.0), feature('sea', 35.40, 33.90, 6.0)]}
    # Land everywhere except a sea cell under the 'sea' candidate (x/y from the same projection).
    from pyproj import Transformer
    to_utm = Transformer.from_crs(4326, 32636, always_xy=True)
    sx, sy = to_utm.transform(35.40, 33.90)
    kx, ky = to_utm.transform(lon, lat)
    x0, y0, cell = min(sx, kx) - 100.0, max(sy, ky) + 100.0, 10.0
    cols, rows = int((abs(kx - sx) + 200) / cell) + 2, int((abs(ky - sy) + 200) / cell) + 2
    grid = np.full((rows, cols), 3.0)
    grid[int((y0 - sy) // cell), int((sx - x0) // cell)] = np.nan
    mask = SimpleNamespace(x0=x0, y0=y0, cell_m=cell, ground=grid)
    # A road passing straight through the 'keep' point would remove it; here the road is 200 m north.
    roads = cu.carriageway_index([([(kx - 50, ky + 200), (kx + 50, ky + 200)], 7.0)], ORIGIN)
    plan = cu.build_plan(candidates, ORIGIN, cu.CITY_BASIS, ground_mask=mask, carriageways=roads)
    assert plan['candidates_in'] == 4
    assert plan['offline_skipped'] == {'on_building_footprint': 1, 'below_min_height': 1, 'water': 1}
    assert [r['id'] for r in plan['rows']] == ['keep']
    row = plan['rows'][0]
    assert (row['local_e_m'], row['local_n_m']) == pytest.approx((5834.338, 1549.0725), abs=0.01)
    assert (row['unreal_x_cm'], row['unreal_y_cm']) == pytest.approx((583433.8, -154907.25), abs=1.0)
    on_road = cu.build_plan({'features': [feature('keep', lon, lat, 8.0)]}, ORIGIN, cu.CITY_BASIS,
                            carriageways=cu.carriageway_index([([(kx - 50, ky), (kx + 50, ky)], 7.0)], ORIGIN))
    assert on_road['offline_skipped'] == {'trunk_on_carriageway_osm_width': 1} and on_road['rows'] == []
