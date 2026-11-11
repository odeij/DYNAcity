"""Tests for corridor registration coordinate conventions, landmark projection, and ambiguity diagnosis."""


def test_native_metadata_detects_stale_crop_assumption():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('registration_diagnostic', Path(__file__).parents[1] / 'scripts/diagnose_corridor_registration.py')
    diagnostic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)
    capture = {'fitted_hfov_deg': 54.5, 'views': {'probe': {
        'native_capture_metadata': {'cameraFOV': 90},
        'wide_resolution_px': [2035, 1181], 'output_resolution_px': [1600, 1200],
        'crop_px': [516, 214, 1003, 752]}}}
    bad = diagnostic.audit_coordinate_conventions(capture)
    assert bad['status'] == 'capture_crop_unverified_or_mismatch'
    assert bad['views'][0]['focal_error_fraction'] > .04
    capture['views']['probe']['crop_px'] = [493, 197, 1048, 786]
    good = diagnostic.audit_coordinate_conventions(capture)
    assert good['status'] == 'capture_crop_consistent'
    assert abs(good['views'][0]['focal_error_fraction']) < .001
    capture['views']['probe']['native_capture_metadata'] = {}
    assert diagnostic.audit_coordinate_conventions(capture)['status'] == 'capture_crop_unverified_or_mismatch'
