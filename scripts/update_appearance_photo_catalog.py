"""Generate a traceable index of the cached, reviewed street photographs."""
import argparse
import json
from pathlib import Path

from dynacity.building_appearance_evidence import digest


def update(root):
    root = Path(root)
    report = json.loads((root / 'street-photo-review-report.json').read_text(encoding='utf-8'))
    assessments = json.loads((root / 'photo-visual-assessments.json').read_text(encoding='utf-8'))
    reviews = {row['photo_id']: row for row in assessments['records']}
    lines = [
        '# Street photograph catalog', '',
        'The source images remain in `street-photos/` beside this catalog.',
        'They are historical evidence; capture dates are recorded per image.',
        '© Grab and KartaView Contributors; [CC BY-SA 4.0 and provider terms](https://kartaview.org/terms).',
        '', '[Photo and map review](street-identity-review.html) · [Roof review](review.html)', '',
    ]
    seen = set()
    for photo in report['photos']:
        identity = str(photo['photo_id'])
        if not identity.isdigit() or identity in seen:
            raise ValueError('Invalid or duplicate photo ID')
        seen.add(identity)
        path = root / 'street-photos' / (identity + '.jpg')
        map_path = root / 'street-photos' / (identity + '-map.png')
        sidecar = path.with_suffix('.json')
        checksum = digest(path)
        review = reviews.get(identity)
        if checksum != photo['sha256'] or review is None or review['photo_sha256'] != checksum:
            raise ValueError('Photo or review hash mismatch: ' + identity)
        if not map_path.is_file() or not sidecar.is_file():
            raise ValueError('Missing map or provenance sidecar: ' + identity)
        source = photo['url']
        if not source.startswith('https://cdn.kartaview.org/'):
            raise ValueError('Unexpected source URL')
        notes = str(review['notes']).replace('\n', ' ')
        lines.extend([
            '## Photo ' + identity, '',
            f'- [Original processed photograph](street-photos/{identity}.jpg) · [Building context map](street-photos/{identity}-map.png) · [Metadata](street-photos/{identity}.json)',
            f'- Captured: {photo["captured_at"]}; source: [KartaView image]({source}).',
            f'- Review: {review["status"]} — {notes}',
            f'- SHA256: `{checksum}`; size: {photo["bytes"]} bytes.', '',
        ])
    if len(seen) != len(reviews):
        raise ValueError('Review and catalog photo sets differ')
    target = root / 'photo-catalog.md'
    target.write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'catalog': str(target), 'photos_verified': len(seen)}))
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    update(parser.parse_args().output)
