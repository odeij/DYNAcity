import hashlib
import pytest

from dynacity.appearance_observations import apply_reviewed_observations, geometry_signature


def fixture(tmp_path):
    (tmp_path/'123.jpg').write_bytes(b'photo-fixture')
    bundle = {'records': [{'source_id': 'b1', 'roof': {'status': 'candidate_needs_review'},
                           'facade': {'status': 'unknown'}}]}
    review = {'source_ids_sha256': hashlib.sha256(b'b1').hexdigest(),
              'source_geometry_sha256': geometry_signature(bundle), 'observations': [{
        'source_id': 'b1', 'evidence_kind': 'street_facade', 'identity_status': 'corroborated',
        'status': 'observed_historical', 'capture_date': '2018-07-21', 'photo_id': '123',
        'photo_sha256': hashlib.sha256(b'photo-fixture').hexdigest(),
        'image_region_normalized_xyxy': [.5, 0, 1, .6],
        'corroboration': ['readable sign and independent mapped point'],
        'visible_features': ['horizontal bands']}]}
    return bundle, review


def test_review_does_not_mutate_base_or_promote_roofs(tmp_path):
    bundle, review = fixture(tmp_path)
    result = apply_reviewed_observations(bundle, review, tmp_path)
    assert bundle['records'][0]['facade']['status'] == 'unknown'
    assert result['records'][0]['facade']['status'] == 'observed_historical'
    assert result['records'][0]['roof']['status'] == 'candidate_needs_review'


def test_stale_photo_rejected(tmp_path):
    bundle, review = fixture(tmp_path)
    (tmp_path/'123.jpg').write_bytes(b'changed')
    with pytest.raises(ValueError, match='photo bytes'):
        apply_reviewed_observations(bundle, review, tmp_path)


def test_roof_or_uncorroborated_claim_cannot_become_facade(tmp_path):
    bundle, review = fixture(tmp_path)
    review['observations'][0]['evidence_kind'] = 'roof'
    with pytest.raises(ValueError):
        apply_reviewed_observations(bundle, review, tmp_path)


def test_changed_selection_rejected(tmp_path):
    bundle, review = fixture(tmp_path)
    bundle['records'][0]['source_id'] = 'b2'
    with pytest.raises(ValueError, match='selection changed'):
        apply_reviewed_observations(bundle, review, tmp_path)
