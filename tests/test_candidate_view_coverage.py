import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('coverage',Path(__file__).parents[1]/'scripts/audit_native_candidate_view_coverage.py')
coverage=importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


def test_camera_rotation_and_translation_preserve_projection():
    view={'location_ue_cm':[100,200,300],'rotation_pitch_yaw_roll':[0,90,0],
          'wide_resolution_px':[1000,800],'render_fov_deg':90,'crop_px':[250,200,500,400]}
    projected=coverage.project_bounds([[90,290,290],[110,310,310]],view)
    assert projected['status']=='CROP_OVERLAP'
    assert projected['fitted_bounds_normalized'][0][0] < .5 < projected['fitted_bounds_normalized'][1][0]
    assert coverage.project_bounds([[90,90,290],[110,110,310]],view)['status']=='BEHIND_CAMERA'
    assert coverage.project_bounds([[400,290,290],[420,310,310]],view)['status']=='OUTSIDE_FITTED_CROP'


def test_straddling_near_plane_is_uncertain():
    view={'location_ue_cm':[0,0,0],'rotation_pitch_yaw_roll':[0,0,0],
          'wide_resolution_px':[1000,800],'render_fov_deg':90,'crop_px':[250,200,500,400]}
    assert coverage.project_bounds([[-1,-1,-1],[1,1,1]],view)['fitted_overlap'] is None
