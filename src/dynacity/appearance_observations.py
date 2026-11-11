"""Apply explicitly reviewed facade observations without altering roof candidates."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from dynacity.building_appearance_evidence import digest


def geometry_signature(bundle):
    payload = {'sources': bundle.get('sources', []),
               'buildings': sorted([(r['source_id'], r.get('footprint')) for r in bundle['records']])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def apply_reviewed_observations(bundle, review, photo_folder):
    if review['source_ids_sha256'] != hashlib.sha256(
            '\n'.join(sorted(r['source_id'] for r in bundle['records'])).encode()).hexdigest():
        raise ValueError('Reviewed building selection changed')
    if review['source_geometry_sha256'] != geometry_signature(bundle):
        raise ValueError('Reviewed source geometry changed')
    result = deepcopy(bundle)
    records = {r['source_id']: r for r in result['records']}
    seen = set()
    for observation in review['observations']:
        identity = observation['source_id']
        if identity not in records or identity in seen:
            raise ValueError('Unknown or duplicate reviewed building ID')
        seen.add(identity)
        if observation['evidence_kind'] != 'street_facade' or observation['identity_status'] != 'corroborated':
            raise ValueError('Facade observation requires reviewed street imagery and corroboration')
        if observation['status'] != 'observed_historical' or not observation.get('capture_date'):
            raise ValueError('Historical capture date and status required')
        if not observation.get('corroboration') or not observation.get('visible_features'):
            raise ValueError('Missing identity basis or observed features')
        box = observation['image_region_normalized_xyxy']
        if not (len(box) == 4 and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise ValueError('Invalid observed image region')
        photo_id = observation['photo_id']
        if not str(photo_id).isdigit():
            raise ValueError('Invalid photo ID')
        path = Path(photo_folder)/(photo_id+'.jpg')
        if digest(path) != observation['photo_sha256']:
            raise ValueError('Reviewed photo bytes changed')
        records[identity]['facade'] = deepcopy(observation)
    result['review_summary'] = {
        'sample_buildings': len(records),
        'historically_observed_facades': len(seen),
        'unknown_facades': len(records)-len(seen),
        'scope': 'Visible facade portion only; current condition and hidden sides unverified',
    }
    return result
