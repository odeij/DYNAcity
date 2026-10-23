from shapely.geometry import box
from dynacity.beirut_overlay import match_footprints


def test_only_strong_unique_overlap_links_and_missing_remains_missing():
    links = match_footprints([box(0, 0, 10, 10), box(50, 50, 60, 60), box(8, 0, 18, 10)], [box(0, 0, 10, 10)])
    assert links[0]['status'] == 'provisional' and links[0]['bbed_index'] == 0
    assert links[1]['status'] == 'unlinked' and 'bbed_index' not in links[1]
    assert links[2]['status'] == 'unlinked' and 'bbed_index' not in links[2]


def test_duplicate_solids_and_duplicate_records_do_not_get_silent_links():
    p = box(0, 0, 10, 10)
    assert all(x['status'] == 'unlinked' for x in match_footprints([p, p], [p]))
    link = match_footprints([p], [p, p])[0]
    assert link['status'] == 'unlinked' and 'bbed_index' not in link


def test_main_map_serves_only_explicit_overlay_assets(tmp_path):
    from fastapi.testclient import TestClient
    from dynacity.service import create_app
    from test_models_engine_api import engine_fixture

    (tmp_path / 'data.json').write_text('{"matched": 1}')
    (tmp_path / 'private.txt').write_text('not a map asset')
    client = TestClient(create_app(engine_fixture(), beirut_overlay_dir=tmp_path))
    assert client.get('/v1/beirut-overlay').json() == {'available': True}
    response = client.get('/v1/beirut-overlay/data.json')
    assert response.json()['matched'] == 1
    assert response.headers['cache-control'] == 'private, no-store'
    assert client.get('/v1/beirut-overlay/private.txt').status_code == 404
    client = TestClient(create_app(engine_fixture()))
    assert client.get('/v1/beirut-overlay').json() == {'available': False}
    assert client.get('/v1/beirut-overlay/data.json').status_code == 404
