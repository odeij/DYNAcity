"""Navigation contracts: pin semantics, coordinate alignment and bounded road placement."""
import importlib.util
from pathlib import Path
import pytest

path = Path(__file__).resolve().parents[1]/'engine/unreal/Content/Python/location_core.py'
spec = importlib.util.spec_from_file_location('location_core', path)
core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)


@pytest.mark.parametrize('text,expected', [
    ('33.8974,35.52735', (33.8974,35.52735)),
    ('https://www.google.com/maps/search/?api=1&query=33.8974%2C35.52735', (33.8974,35.52735)),
    ('https://www.google.com/maps/place/Test/@33.8,35.4,16z/data=!4m2!3d33.8974!4d35.52735', (33.8974,35.52735)),
    ('https://www.openstreetmap.org/?mlat=33.8974&mlon=35.52735#map=15/33.8/35.4', (33.8974,35.52735)),
    ('https://www.openstreetmap.org/#map=18/33.8974/35.52735', (33.8974,35.52735)),
    ('geo:33.8974,35.52735', (33.8974,35.52735)),
])
def test_pin_coordinates(text, expected):
    row = core.parse_location(text)
    assert (row['lat'], row['lon']) == expected


@pytest.mark.parametrize('text', ['91,35', '33,181', 'https://google.com.evil.test/maps/@33,35',
                                 'https://user@google.com/maps/@33,35', 'file:///etc/passwd',
                                 'https://www.google.com:8443/maps/@33,35'])
def test_invalid_or_untrusted_links(text):
    with pytest.raises(ValueError): core.parse_location(text)


def test_names_objects_and_short_links():
    assert core.parse_location('https://www.google.com/maps/search/?api=1&query=Sanayeh+Garden') == {'query':'Sanayeh Garden'}
    assert core.parse_location('https://www.openstreetmap.org/way/123#map=18/33.8/35.4') == {'osm_id':'way/123'}
    assert 'short_link' in core.parse_location('https://maps.app.goo.gl/example')
    index = {'places':[{'id':'node/1','name':'René Moawad Garden','aliases':['حديقة رينيه معوض']}]}
    assert core.search(index, {'query':'rene moawad'})[0]['id'] == 'node/1'
    assert core.search(index, {'query':'رينيه'})[0]['id'] == 'node/1'
    assert core.search(index, {'query':'missing'}) == []


def test_coordinate_alignment_with_pyproj():
    from pyproj import Transformer
    transformer = Transformer.from_crs(4326,32636,always_xy=True)
    actual = core.lonlat_to_utm(35.52735,33.8974)
    expected = transformer.transform(35.52735,33.8974)
    assert actual == pytest.approx(expected, abs=.002)


def test_road_projection_height_heading_and_coverage():
    index = {'roads':[{'id':1,'name':'Road','bounds':[0,0,10,0],'path':[[0,0,2],[10,0,4]]}]}
    near = core.nearest_roads(index,[5,3])[0]
    assert near['position'] == pytest.approx([5,0,3])
    assert near['distance_m'] == 3
    assert near['direction'] == [10,0]
    assert core.nearest_roads(index,[500,0]) == []
