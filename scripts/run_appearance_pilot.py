"""Run the existing Charles Malek evidence stages in a bounded, resumable pass."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


PROJECT = Path(__file__).resolve().parents[1]


def run(output, city_batches, photo_budget):
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    status = {'status': 'running', 'scope': 'Charles Malek 100-building evidence pilot',
              'output': str(root), 'photo_budget': photo_budget, 'stages': [],
              'limits': ['Camera views and roof colors are candidates, not automatic facade observations',
                         'Historical imagery and existing licenses apply', 'No Unreal assets are modified']}

    def save():
        status['elapsed_seconds'] = round(time.perf_counter()-started, 2)
        (root/'pipeline-status.json').write_text(json.dumps(status, indent=2), encoding='utf-8')

    def stage(name, arguments):
        start = time.perf_counter()
        result = subprocess.run([sys.executable, str(PROJECT/'scripts'/name), *arguments],
                                cwd=PROJECT, text=True, encoding='utf-8', errors='replace',
                                capture_output=True)
        status['stages'].append({'script': name, 'exit_code': result.returncode,
                                 'elapsed_seconds': round(time.perf_counter()-start, 2),
                                 'stdout': result.stdout.strip(), 'stderr': result.stderr.strip()})
        save()
        if result.returncode:
            status['status'] = 'failed'
            status['failed_stage'] = name
            save()
            raise RuntimeError('Evidence pipeline failed at '+name+': '+result.stderr[-1200:])

    save()
    common = ['--output', str(root)]
    stage('prepare_appearance_evidence.py', [*common, '--city-batches', str(city_batches)])
    stage('discover_street_photo_evidence.py', common)
    stage('screen_street_photo_evidence.py', common)
    stage('prepare_street_evidence_review.py', [*common, '--photo-budget', str(photo_budget)])

    # Existing manual reviews are authoritative only for the exact set of photo
    # bytes they reviewed. Never fabricate reviews for newly discovered photos.
    assessments = root/'photo-visual-assessments.json'
    reviewed = root/'reviewed-observations.json'
    photo_report = json.loads((root/'street-photo-review-report.json').read_text(encoding='utf-8'))
    photo_ids = {item['photo_id'] for item in photo_report['photos']}
    assessment_ids = ({item['photo_id'] for item in json.loads(assessments.read_text(encoding='utf-8'))['records']}
                      if assessments.exists() else set())
    if reviewed.exists() and assessments.exists() and photo_ids == assessment_ids:
        stage('apply_appearance_review.py', common)
        stage('report_appearance_evidence.py', common)
        stage('update_appearance_photo_catalog.py', common)
        stage('update_simulation_work_page.py', common)
        status['status'] = 'completed_with_existing_reviews'
    else:
        status['status'] = 'collection_complete_review_needed'
        status['review_needed'] = {'photos_without_assessments': sorted(photo_ids-assessment_ids),
                                   'assessments_outside_current_selection': sorted(assessment_ids-photo_ids),
                                   'reviewed_observations_present': reviewed.exists(),
                                   'note': 'Earlier consolidated reports may describe a previous run; use the newly generated review pages until reviews are updated.'}
    save()
    print(json.dumps({key: value for key, value in status.items() if key != 'stages'}))
    return status


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--city-batches', default='C:/private/dynacity-data/engine/city-batches')
    parser.add_argument('--photo-budget', type=int, default=17, choices=range(1, 25))
    parser.add_argument('--citywide', action='store_true', help='Collect across all indexed districts')
    parser.add_argument('--requests-per-hour', type=int, default=60)
    parser.add_argument('--photos-per-group', type=int, default=1)
    parser.add_argument('--max-tiles', type=int, help='Optional bounded citywide test')
    parser.add_argument('--skip-street', action='store_true', help='Optional roof-only citywide test')
    args = parser.parse_args()
    if args.citywide:
        command = [sys.executable, str(PROJECT/'scripts/run_appearance_citywide.py'),
                   '--output', args.output, '--city-batches', args.city_batches,
                   '--requests-per-hour', str(args.requests_per_hour),
                   '--photos-per-group', str(args.photos_per_group)]
        if args.max_tiles is not None:
            command.extend(['--max-tiles', str(args.max_tiles)])
        if args.skip_street:
            command.append('--skip-street')
        sys.exit(subprocess.call(command, cwd=PROJECT))
    run(args.output, args.city_batches, args.photo_budget)
