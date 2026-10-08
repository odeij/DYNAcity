from copy import deepcopy
from datetime import date
import json

import pandas as pd
import pytest

from dynacity.bbed import BBEDClient, resolved_object_ids
from dynacity.cli import main
from dynacity.coverage import coverage_report
from dynacity.panel import PanelBuilder
from dynacity.snapshot import snapshot_from_bbed
from dynacity.viewer import build_viewer_payload


def source(collection):
    result = deepcopy(collection)
    result['crs'] = {'properties': {'name': 'EPSG:32636'}}
    return result


def snapshot(c):
    return snapshot_from_bbed(c, snapshot_id='test', as_of=date(2024, 4, 30), data_version='test')


def test_missing_geometry_periods_height_and_joins(bbed_collection):
    c = source(bbed_collection)
    c['features'][0]['geometry'] = None
    p = c['features'][0]['properties']
    p.update(F5__Current_status=' ', Building_Hight_m=None)
    s = snapshot(c)
    assert s.objects[0].features['status_observation_year'] == 2022
    assert '2022' in s.objects[0].provenance['status']
    s.objects.pop()
    report = coverage_report(c, snapshot=s, morphology=pd.DataFrame([{'object_id': 'wrong'}]))
    assert report['geometry']['missing'] == 1
    assert report['height']['floor_estimate'] == 1
    assert report['status_periods']['2024']['missing'] == 1
    assert report['snapshot_join']['source_only'] == 1
    assert report['morphology_join']['morphology_only'] == 1
    assert not report['passed']


def test_invalid_geometry_not_rendered_and_nonfinite_numeric_rejected(bbed_collection):
    c = source(bbed_collection)
    c['features'][0]['geometry']['coordinates'] = [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]
    c['features'][0]['properties']['Building_Hight_m'] = 'inf'
    s = snapshot(c)
    assert s.objects[0].height_m is None and s.objects[0].centroid_x_m is None
    payload = build_viewer_payload(c, s)
    assert payload['coverage']['drawn'] == 1
    assert payload['coverage']['quality']['geometry']['invalid'] == 1


def test_duplicates_fail_instead_of_silent_join_overwrite(bbed_collection):
    c = source(bbed_collection)
    c['features'][1]['properties']['OBJECTID'] = 1
    with pytest.raises(ValueError, match='unique'):
        resolved_object_ids(c['features'])
    frame = pd.DataFrame([{'object_id': 'x'}, {'object_id': 'x'}])
    for build in (snapshot_from_bbed, PanelBuilder().build):
        with pytest.raises(ValueError, match='unique'):
            if build == snapshot_from_bbed:
                build(bbed_collection, snapshot_id='s', as_of=date(2024, 1, 1), data_version='v', lidar_features=frame)
            else:
                build(bbed_collection, frame)


def test_blank_status_does_not_create_transition_and_stale_snapshot_detected(bbed_collection):
    c = source(bbed_collection)
    s = snapshot(c)
    c['features'][0]['properties']['Status2018'] = ' '
    c['features'][0]['properties']['Building_Hight_m'] = 42
    report = coverage_report(c, snapshot=s)
    assert report['panel_intervals']['2018-2022']['skipped_missing_endpoint'] == 1
    assert report['snapshot_join']['height_or_floor_mismatch'] == 1
    assert len(PanelBuilder().build(c)) == 4


def test_manifest_integrity_and_immutable_update(tmp_path, bbed_collection):
    c = source(bbed_collection)
    manifest = BBEDClient().snapshot_manifest(c)
    bbed, m = tmp_path/'source.json', tmp_path/'manifest.json'
    bbed.write_text(json.dumps(c)); m.write_text(json.dumps(manifest))
    dest = tmp_path/'candidate'
    args = ['stage-update', '--bbed', str(bbed), '--manifest', str(m), '--output-dir', str(dest),
            '--snapshot-id', 's', '--data-version', 'v', '--as-of', '2024-04-30']
    main(args)
    assert (dest/'data'/'coverage.json').exists()
    assert json.loads((dest/'data'/'coverage.json').read_text())['passed']
    assert json.loads((dest/'data'/'update.json').read_text())['artifact_sha256']['snapshot.json']
    with pytest.raises(ValueError, match='already exists'):
        main(args)
    manifest['sha256'] = 'wrong'; m.write_text(json.dumps(manifest))
    args[args.index(str(dest))] = str(tmp_path/'bad')
    with pytest.raises(ValueError, match='Manifest'):
        main(args)
    assert not (tmp_path/'bad').exists()


def test_removed_ids_require_acknowledgement(tmp_path, bbed_collection):
    c = source(bbed_collection); old = tmp_path/'old.json'; old.write_text(json.dumps(c))
    c['features'].pop()
    bbed, m = tmp_path/'new.json', tmp_path/'m.json'
    bbed.write_text(json.dumps(c)); m.write_text(json.dumps(BBEDClient().snapshot_manifest(c)))
    args = ['stage-update', '--bbed', str(bbed), '--manifest', str(m), '--previous', str(old),
            '--output-dir', str(tmp_path/'candidate'), '--snapshot-id', 's', '--data-version', 'v', '--as-of', '2024-04-30']
    with pytest.raises(ValueError, match='removes IDs'):
        main(args)
    main(args + ['--acknowledge-removals'])


def test_short_transfer_limited_pages_continue_and_repeats_fail(bbed_collection):
    pages = iter([{'type': 'FeatureCollection', 'features': bbed_collection['features'][:1], 'exceededTransferLimit': True},
                  {'type': 'FeatureCollection', 'features': bbed_collection['features'][1:]}])
    assert len(BBEDClient(page_size=10, transport=lambda _: json.dumps(next(pages)).encode()).fetch_features()['features']) == 2
    with pytest.raises(ValueError, match='unique'):
        BBEDClient(page_size=1, transport=lambda _: json.dumps({'type': 'FeatureCollection', 'features': bbed_collection['features'][:1]}).encode()).fetch_features()


def test_panel_row_drift_and_privacy_fields_are_reported(bbed_collection):
    c = source(bbed_collection)
    panel = PanelBuilder().build(c)
    assert coverage_report(c, panel=panel)['panel_join']['missing_rows'] == 0
    panel = panel.iloc[1:].copy()
    panel.iloc[0, panel.columns.get_loc('current_state')] = 'unknown'
    report = coverage_report(c, panel=panel)
    assert report['panel_join']['missing_rows'] == 1
    assert report['panel_join']['state_value_mismatch'] == 1
    c['features'][0]['properties']['owner_name'] = 'PRIVATE'
    report = coverage_report(c)
    assert not report['passed'] and 'PRIVATE' not in json.dumps(report)


def test_malformed_geometry_is_reported(bbed_collection):
    c = source(bbed_collection)
    c['features'][0]['geometry'] = {'type': 'Polygon', 'coordinates': [[[1, 2], [3, 4]]]}
    assert coverage_report(c)['geometry']['malformed'] == 1
