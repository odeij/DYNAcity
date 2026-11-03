import numpy as np
import pytest

from dynacity.building_appearance_evidence import roof_candidate


def test_coarse_imagery_is_not_building_color_evidence():
    result = roof_candidate(np.full((20, 20, 3), 150), np.ones((20, 20), bool), pixel_size_m=10)
    assert result['status'] == 'unknown'
    assert 'median_rgb' not in result


def test_dark_and_green_pixels_do_not_dominate_candidate():
    rgb = np.full((10, 10, 3), [170, 155, 140], dtype=np.uint8)
    rgb[:2] = [5, 5, 5]
    rgb[2:4] = [30, 150, 40]
    result = roof_candidate(rgb, np.ones((10, 10), bool), pixel_size_m=.5)
    assert result['status'] == 'candidate_needs_review'
    assert result['median_rgb'] == [170, 155, 140]
    assert result['retained_pixels'] == 60


def test_mask_excludes_neighbors():
    rgb = np.full((20, 20, 3), [220, 30, 30], dtype=np.uint8)
    rgb[5:15, 5:15] = [140, 145, 150]
    mask = np.zeros((20, 20), bool); mask[5:15, 5:15] = True
    result = roof_candidate(rgb, mask, pixel_size_m=.5)
    assert result['median_rgb'] == [140, 145, 150]
    assert result['valid_pixels'] == 100


def test_insufficient_pixels_remain_unknown():
    result = roof_candidate(np.full((3, 3, 3), 150), np.ones((3, 3), bool), pixel_size_m=.5)
    assert result['status'] == 'unknown'


def test_shadow_only_roof_remains_unknown():
    result = roof_candidate(np.full((10, 10, 3), 12), np.ones((10, 10), bool), pixel_size_m=.5)
    assert result['status'] == 'unknown'


def test_invalid_resolution_or_shape_rejected():
    with pytest.raises(ValueError):
        roof_candidate(np.zeros((10, 10, 3)), np.ones((10, 10), bool), pixel_size_m=float('nan'))
    with pytest.raises(ValueError):
        roof_candidate(np.zeros((10, 10, 3)), np.ones((8, 8), bool), pixel_size_m=.5)


def test_georeferenced_sampling_retains_unknown_facade_and_provenance(tmp_path):
    rasterio = pytest.importorskip('rasterio')
    pytest.importorskip('pyproj')
    from shapely.geometry import box, mapping
    from dynacity.building_appearance_evidence import observe_roofs
    raster = tmp_path / 'roof.tif'
    data = np.full((3, 40, 40), 160, dtype=np.uint8)
    with rasterio.open(raster, 'w', driver='GTiff', width=40, height=40, count=3,
                       dtype='uint8', crs='EPSG:32636',
                       transform=rasterio.transform.from_origin(730000, 3750020, .5, .5)) as dst:
        dst.write(data)
        dst.colorinterp = (rasterio.enums.ColorInterp.red, rasterio.enums.ColorInterp.green,
                           rasterio.enums.ColorInterp.blue)
    bundle = {'records': [{'source_id': 'b1', 'crs': 'EPSG:32636',
                          'footprint': mapping(box(730002, 3750002, 730018, 3750018)),
                          'facade': {'status': 'unknown'}}]}
    output = observe_roofs(bundle, raster, {'id': 'test-image', 'captured_at': '2020-07-31'},
                           tmp_path / 'evidence.json')
    assert output['records'][0]['roof']['status'] == 'candidate_needs_review'
    assert output['records'][0]['facade'] == {'status': 'unknown'}
    assert len(output['imagery']['cached_sha256']) == 64
    assert output['summary']['observed_facades'] == 0
