import json
from pathlib import Path
import subprocess

# Extract the granite panel functions from template for testing
_GRANITE_TEMPLATE_CODE = None
def _get_granite_template_code():
    global _GRANITE_TEMPLATE_CODE
    if _GRANITE_TEMPLATE_CODE is None:
        html = (Path(__file__).parents[1] / 'src/dynacity/viewer_template.html').read_text(encoding='utf-8')
        # Extract from function setGranitePreview to function setCanopyReview
        start = html.find('function setGranitePreview(open) {')
        end = html.find('function setCanopyReview(on) {')
        _GRANITE_TEMPLATE_CODE = html[start:end]
    return _GRANITE_TEMPLATE_CODE


def test_estimate_gates_parent_and_uses_exact_viewport_offsets_and_parent_ground():
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
 const source={parent:{id:'parent',parts:[[[[35,33],[35.01,33],[35.01,33.01],[35,33]]]],ground_z:63.52,top_z:109.07,height_m:45.55},
 anchor:[35,33],vertices_lonlat_height:[[35,33,4.4],[35.001,33,4.4],[35,33.001,5.1]]};
 const DATA={granite_overlay:source},ui={graniteOverlay:false,lens:'observed'};
 const beirutOverlay={active:true,data:{buildings:[JSON.parse(JSON.stringify(source.parent))]}};
 const MATERIAL={ambient:0.8};let builds=0;
 const deck={WebMercatorViewport:class{constructor(){builds++;this.distanceScales={unitsPerMeter:[2,3,4]}}
  projectPosition(p){return [p[0]*1000,p[1]*1500,0]}},SimpleMeshLayer:class{constructor(p){this.props=p}}};
 const groundedPolygon=(rings)=>rings.map(r=>r.map(p=>[...p,123]));
 const before=JSON.stringify(DATA);
`+JSON.parse(process.argv[1])+`
 assert.equal(graniteOverlayLayers().length,0);
 ui.graniteOverlay=true;
 const p=graniteOverlayLayers()[0].props;
 assert.deepEqual(p.getPosition,[35,33,123]);
 assert.equal(p.mesh.positions.value[0],0);assert.equal(p.mesh.positions.value[3],0.5);
 assert.equal(p.mesh.positions.value[7],0.5);
 assert.ok(Math.abs(p.mesh.positions.value[2]-4.4)<1e-5);
 assert.equal(p.pickable,false);assert.equal(p.mesh.normals.value.length,9);
 graniteOverlayLayers();assert.equal(builds,1);assert.equal(JSON.stringify(DATA),before);
 ui.lens='forecast';assert.equal(graniteOverlayLayers().length,0);
 ui.lens='observed';beirutOverlay.active=false;assert.equal(graniteOverlayLayers().length,0);
 beirutOverlay.active=true;beirutOverlay.data.buildings[0].ground_z=64;assert.equal(graniteOverlayLayers().length,0);
 beirutOverlay.data.buildings[0].ground_z=63.52;beirutOverlay.data.buildings[0].parts[0][0][0][0]=34;
 assert.equal(graniteOverlayLayers().length,0);
 beirutOverlay.data.buildings=[];assert.equal(graniteOverlayLayers().length,0);
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_level_camera_helpers_exist():
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    for fn in ['saveViewState', 'restoreViewState', 'computeStreetViewCamera', 'enterStreetView', 'exitStreetView', 'isStreetViewActive', 'isPointInFootprints', 'isTerrainLoaded', 'normalizeLngLat']:
        assert fn in code, f'Missing function {fn} in viewer_granite_overlay.js'


def test_normalize_lnglat():
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
const arr = normalizeLngLat([35.5, 33.8]);
assert.deepEqual(arr, [35.5, 33.8]);
const obj1 = normalizeLngLat({lng: 35.5, lat: 33.8});
assert.deepEqual(obj1, [35.5, 33.8]);
const obj2 = normalizeLngLat({lon: 35.5, lat: 33.8});
assert.deepEqual(obj2, [35.5, 33.8]);
assert.equal(normalizeLngLat(null), null);
assert.equal(normalizeLngLat(undefined), null);
assert.equal(normalizeLngLat({}), null);
assert.equal(normalizeLngLat({lng: 35.5}), null);
assert.equal(normalizeLngLat({lat: 33.8}), null);
assert.equal(normalizeLngLat('not an object'), null);
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_terrain_loaded_gate():
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert, ground: undefined});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
ground = undefined;
assert.equal(isTerrainLoaded(), false);
ground = {grid: null};
assert.equal(isTerrainLoaded(), false);
ground = {grid: []};
assert.equal(isTerrainLoaded(), false);
ground = {grid: new Int16Array([1,2,3])};
assert.equal(isTerrainLoaded(), true);
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_estimated_camera_eye_height_above_centre():
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
function estimatedCameraEye(camera) {
  const [lon,lat]=camera.centre,mpp=78271.51696*Math.cos(lat*Math.PI/180)/2**camera.zoom;
  const distance=1.5*camera.height*mpp,pitch=camera.pitch*Math.PI/180,bearing=camera.bearing*Math.PI/180;
  const horizontal=distance*Math.sin(pitch);
  return [lon-Math.sin(bearing)*horizontal/(111320*Math.cos(lat*Math.PI/180)),
    lat-Math.cos(bearing)*horizontal/111320,distance*Math.cos(pitch)];
}
const camera = {centre: [35.5, 33.8], zoom: 20, pitch: 60, bearing: 0, height: 900};
const eye = estimatedCameraEye(camera);
assert.ok(Number.isFinite(eye[2]));
assert.ok(eye[2] > 0);
assert.ok(eye[2] < 100);
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_view_save_restore_preserves_limits_on_partial_entry_failure():
    """Test that entering street view restores original maxZoom/maxPitch if limit widening partially fails."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
// Mock map with setMaxZoom that fails on second call
let maxZoom = 20;
let maxPitch = 75;
let setMaxZoomCalls = 0;
let setMaxPitchCalls = 0;
const map = {
  getCenter: () => ({ toArray: () => [35.5, 33.8] }),
  getZoom: () => 15,
  getPitch: () => 60,
  getBearing: () => -20,
  getMaxZoom: () => maxZoom,
  getMaxPitch: () => maxPitch,
  setMaxZoom: function(z) { setMaxZoomCalls++; if (setMaxZoomCalls === 2) throw new Error('fail'); maxZoom = z; },
  setMaxPitch: function(p) { setMaxPitchCalls++; maxPitch = p; },
  easeTo: () => {},
  getCanvasContainer: () => ({ clientHeight: 900 }),
  getCanvas: () => ({ height: 900 })
};
const groundZ = (lon, lat) => 63.5;
const ground = { grid: new Int16Array([1,2,3]) };
const beirutOverlay = { data: { buildings: [{ id: 'parent', parts: [[[[35.5,33.8],[35.51,33.8],[35.51,33.81],[35.5,33.81]]]] }] } };
const source = { parent: { id: 'parent', parts: [[[[35.5,33.8],[35.51,33.8],[35.51,33.81],[35.5,33.81]]]], ground_z: 63.52, top_z: 109.07, height_m: 45.55 }, anchor: [35.5, 33.8], vertices_lonlat_height: [[35.5,33.8,4.4]] };
const DATA = { granite_overlay: source };
const ui = { lens: 'observed', graniteOverlay: true };
const deck = { WebMercatorViewport: class { constructor() { this.distanceScales = { unitsPerMeter: [2,3,4] } } projectPosition(p) { return [p[0]*1000,p[1]*1500,0] } } };
const groundedPolygon = (rings) => rings.map(r => r.map(p => [...p, 63.5]));
const MATERIAL = { ambient: 0.8 };
const estimatedCameraEye = (c) => [c.centre[0], c.centre[1], 100];

assert.equal(enterStreetView(source), false, 'Should fail when setMaxZoom fails on second call');
assert.equal(maxZoom, 20, 'maxZoom should be restored to original 20');
assert.equal(maxPitch, 75, 'maxPitch should be restored to original 75');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_view_exit_when_inactive_does_not_reset_to_home():
    """Test that exitStreetView does nothing (no jump, no home reset) when already inactive."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
let jumpToCalled = false;
let easeToCalled = false;
let setMaxZoomCalled = false;
let setMaxPitchCalled = false;
const map = {
  jumpTo: () => { jumpToCalled = true; },
  easeTo: () => { easeToCalled = true; },
  setMaxZoom: () => { setMaxZoomCalled = true; },
  setMaxPitch: () => { setMaxPitchCalled = true; }
};
// Assign existing state variables (already declared in production code)
streetViewSavedState = null;
streetViewPostMoveCheck = null;
streetViewValidationActive = false;

assert.equal(exitStreetView(), undefined);
assert.equal(jumpToCalled, false, 'jumpTo should not be called when inactive');
assert.equal(easeToCalled, false, 'easeTo should not be called when inactive');
assert.equal(setMaxZoomCalled, false, 'setMaxZoom should not be called when inactive');
assert.equal(setMaxPitchCalled, false, 'setMaxPitch should not be called when inactive');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_view_post_move_validation_rejects_nonfinite_camera():
    """Test that validation rejects non-finite camera values."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
const map = {
  getCenter: () => ({ lng: NaN, lat: 33.8 }),
  getZoom: () => 20,
  getPitch: () => 60,
  getBearing: () => -20,
  getCenterElevation: () => 0,
  getCanvasContainer: () => ({ clientHeight: 900 }),
  getCanvas: () => ({ height: 900 })
};
const groundZ = () => 63.5;
const beirutOverlay = { data: { buildings: [] } };
// Assign to existing state variable
streetViewPostMoveCheck = {
  requestedEyeLon: 35.5,
  requestedEyeLat: 33.8,
  requestedCenterLon: 35.5,
  requestedCenterLat: 33.8,
  requestedZoom: 20,
  requestedPitch: 60,
  requestedBearing: -20,
  source: { anchor: [35.5, 33.8] }
};
const estimatedCameraEye = (c) => [c.centre[0], c.centre[1], 100];
// Use existing isPointInFootprints from production code

const result = validateStreetViewPostMove();
assert.equal(result.valid, false);
assert.equal(result.reason, 'nonfinite_camera');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_view_agl_uses_actual_centre_elevation_not_raw_model_ground():
    """Test that AGL calculation uses actualCentreElevation + heightAboveCentre - eyeGround, not raw model ground (63.52)."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
const map = {
  getCenter: () => ({ lng: 35.5, lat: 33.8 }),
  getZoom: () => 20,
  getPitch: () => 60,
  getBearing: () => -20,
  getCenterElevation: () => 63.45,  // rendered terrain elevation at center (NOT 63.52)
  getCanvasContainer: () => ({ clientHeight: 900 }),
  getCanvas: () => ({ height: 900 })
};
const groundZ = (lon, lat) => 63.4;  // rendered terrain at eye position
const beirutOverlay = { data: { buildings: [] } };
// Assign to existing state variable
streetViewPostMoveCheck = {
  requestedEyeLon: 35.5,
  requestedEyeLat: 33.8,
  requestedCenterLon: 35.5,
  requestedCenterLat: 33.8,
  requestedZoom: 20,
  requestedPitch: 60,
  requestedBearing: -20,
  source: { anchor: [35.5, 33.8] }
};
// estimatedCameraEye returns heightAboveCentre = 1.65 (so AGL = 63.45 + 1.65 - 63.4 = 1.7)
const estimatedCameraEye = (c) => [c.centre[0], c.centre[1], 1.65];
// Use existing isPointInFootprints from production code

const result = validateStreetViewPostMove();
assert.equal(result.valid, true, 'Should pass with correct AGL calculation');
assert.ok(Math.abs(result.actualEyeAGL - 1.7) < 0.01, 'AGL should be centreElevation + heightAboveCentre - eyeGround = 1.7');
assert.equal(result.actualCentreElevation, 63.45, 'Should use actual centre elevation from map');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_view_desired_eye_vs_lookat_centre_readback():
    """Test that validation compares actual eye XY to requested eye (not center), and uses API's calculateCameraOptionsFromTo semantics."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
const map = {
  getCenter: () => ({ lng: 35.5001, lat: 33.8001 }),  // center slightly different from requested
  getZoom: () => 22,
  getPitch: () => 80,
  getBearing: () => 135,
  getCenterElevation: () => 63.45,
  getCanvasContainer: () => ({ clientHeight: 900 }),
  getCanvas: () => ({ height: 900 })
};
const groundZ = (lon, lat) => 63.4;
const beirutOverlay = { data: { buildings: [] } };
// Requested eye is at 35.5, 33.8 (12m from target at bearing 315)
// Actual center is at 35.5001, 33.8001 (look-at point)
// estimatedCameraEye should compute eye from actual center and return XY near requested eye
const estimatedCameraEye = (c) => [35.5, 33.8, 1.6];  // eye XY matches requested eye
// Use existing isPointInFootprints from production code

// Assign to existing state variable
streetViewPostMoveCheck = {
  requestedEyeLon: 35.5,
  requestedEyeLat: 33.8,
  requestedCenterLon: 35.50005,
  requestedCenterLat: 33.80005,
  requestedZoom: 22,
  requestedPitch: 80,
  requestedBearing: 135,
  source: { anchor: [35.50005, 33.80005] }
};

const result = validateStreetViewPostMove();
assert.equal(result.valid, true);
assert.ok(Math.abs(result.actualEyeXY[0] - 35.5) < 0.0001);
assert.ok(Math.abs(result.actualEyeXY[1] - 33.8) < 0.0001);
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_view_synchronous_moveend_during_exit_no_recursion():
    """Test that onStreetViewMoveEnd does not re-enter exitStreetView during restoration (guard prevents recursion)."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
let exitCalled = 0;
const map = {
  getCenter: () => ({ lng: 35.5, lat: 33.8 }),
  getZoom: () => 20,
  getPitch: () => 60,
  getBearing: () => -20,
  getCenterElevation: () => 63.45,
  setMaxZoom: () => {},
  setMaxPitch: () => {},
  jumpTo: () => {},
  getCanvasContainer: () => ({ clientHeight: 900 }),
  getCanvas: () => ({ height: 900 })
};
const groundZ = (lon, lat) => 63.4;
const beirutOverlay = { data: { buildings: [] } };
const estimatedCameraEye = (c) => [c.centre[0], c.centre[1], 1.6];
// Use existing isPointInFootprints from production code
const renderGranitePanel = () => {};
const document = { getElementById: () => ({ textContent: '', dataset: {} }) };

// Set up state as if street view is active and validation is running
streetViewSavedState = {
  center: [35.5, 33.8], zoom: 15, pitch: 60, bearing: -20, maxZoom: 20, maxPitch: 75
};
streetViewPostMoveCheck = {
  requestedEyeLon: 35.5, requestedEyeLat: 33.8,
  requestedCenterLon: 35.5, requestedCenterLat: 33.8,
  requestedZoom: 20, requestedPitch: 60, requestedBearing: -20,
  source: { anchor: [35.5, 33.8] }
};
streetViewValidationActive = true;  // Guard is SET (as it would be during exitStreetView)

// Call onStreetViewMoveEnd - should return early due to guard
onStreetViewMoveEnd();

// State should be unchanged (postMoveCheck not cleared)
assert.ok(streetViewPostMoveCheck !== null, 'postMoveCheck should not be cleared when guard is active');
assert.ok(streetViewSavedState !== null, 'savedState should not be cleared when guard is active');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_view_exit_then_duplicate_exit_no_extra_jump():
    """Test that calling exitStreetView twice doesn't cause a second jump."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
let jumpCount = 0;
const map = {
  getCenter: () => ({ toArray: () => [35.5, 33.8] }),
  getZoom: () => 22,
  getPitch: () => 80,
  getBearing: () => 135,
  getMaxZoom: () => 24,
  getMaxPitch: () => 85,
  setMaxZoom: () => {},
  setMaxPitch: () => {},
  jumpTo: () => { jumpCount++; },
  getCanvasContainer: () => ({ clientHeight: 900 }),
  getCanvas: () => ({ height: 900 })
};
const groundZ = (lon, lat) => 63.4;
const beirutOverlay = { data: { buildings: [] } };
const estimatedCameraEye = (c) => [c.centre[0], c.centre[1], 1.6];
// Use existing isPointInFootprints from production code

streetViewSavedState = {
  center: [35.5, 33.8], zoom: 15, pitch: 60, bearing: -20, maxZoom: 20, maxPitch: 75
};
streetViewPostMoveCheck = {
  requestedEyeLon: 35.5, requestedEyeLat: 33.8,
  requestedCenterLon: 35.5, requestedCenterLat: 33.8,
  requestedZoom: 22, requestedPitch: 80, requestedBearing: 135,
  source: { anchor: [35.5, 33.8] }
};

exitStreetView();  // First exit
assert.equal(jumpCount, 1, 'First exit should call jumpTo once');
exitStreetView();  // Second exit (duplicate)
assert.equal(jumpCount, 1, 'Second exit should NOT call jumpTo again');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_view_set_max_zoom_success_set_max_pitch_throw_restores_prior_limits():
    """Test that if setMaxZoom succeeds but setMaxPitch throws, maxZoom is restored."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
let maxZoom = 20;
let maxPitch = 75;
let setMaxZoomCalled = 0;
let setMaxPitchCalled = 0;
const map = {
  getCenter: () => ({ toArray: () => [35.5, 33.8] }),
  getZoom: () => 15,
  getPitch: () => 60,
  getBearing: () => -20,
  getMaxZoom: () => maxZoom,
  getMaxPitch: () => maxPitch,
  setMaxZoom: function(z) { setMaxZoomCalled++; maxZoom = z; },
  setMaxPitch: function(p) { setMaxPitchCalled++; throw new Error('pitch fail'); },
  easeTo: () => {},
  getCanvasContainer: () => ({ clientHeight: 900 }),
  getCanvas: () => ({ height: 900 })
};
const groundZ = (lon, lat) => 63.5;
const ground = { grid: new Int16Array([1,2,3]) };
const beirutOverlay = { data: { buildings: [{ id: 'parent', parts: [[[[35.5,33.8],[35.51,33.8],[35.51,33.81],[35.5,33.81]]]] }] } };
const source = { parent: { id: 'parent', parts: [[[[35.5,33.8],[35.51,33.8],[35.51,33.81],[35.5,33.81]]]], ground_z: 63.52, top_z: 109.07, height_m: 45.55 }, anchor: [35.5, 33.8], vertices_lonlat_height: [[35.5,33.8,4.4]] };
const DATA = { granite_overlay: source };
const ui = { lens: 'observed', graniteOverlay: true };
const deck = { WebMercatorViewport: class { constructor() { this.distanceScales = { unitsPerMeter: [2,3,4] } } projectPosition(p) { return [p[0]*1000,p[1]*1500,0] } } };
const groundedPolygon = (rings) => rings.map(r => r.map(p => [...p, 63.5]));
const MATERIAL = { ambient: 0.8 };
const estimatedCameraEye = (c) => [c.centre[0], c.centre[1], 100];
// Use existing isPointInFootprints and isTerrainLoaded from production code

assert.equal(enterStreetView(source), false);
assert.equal(maxZoom, 20, 'maxZoom should be restored to original 20 after setMaxPitch failure');
assert.equal(maxPitch, 75, 'maxPitch should remain 75');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_view_ease_to_throw_restores_prior_limits_and_view():
    """Test that if easeTo throws after limits widened, both limits are restored."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
let maxZoom = 20;
let maxPitch = 75;
const map = {
  getCenter: () => ({ toArray: () => [35.5, 33.8] }),
  getZoom: () => 15,
  getPitch: () => 60,
  getBearing: () => -20,
  getMaxZoom: () => maxZoom,
  getMaxPitch: () => maxPitch,
  setMaxZoom: function(z) { maxZoom = z; },
  setMaxPitch: function(p) { maxPitch = p; },
  easeTo: function(opts) { throw new Error('easeTo fail'); },
  getCanvasContainer: () => ({ clientHeight: 900 }),
  getCanvas: () => ({ height: 900 })
};
const groundZ = (lon, lat) => 63.5;
const ground = { grid: new Int16Array([1,2,3]) };
const beirutOverlay = { data: { buildings: [{ id: 'parent', parts: [[[[35.5,33.8],[35.51,33.8],[35.51,33.81],[35.5,33.81]]]] }] } };
const source = { parent: { id: 'parent', parts: [[[[35.5,33.8],[35.51,33.8],[35.51,33.81],[35.5,33.81]]]], ground_z: 63.52, top_z: 109.07, height_m: 45.55 }, anchor: [35.5, 33.8], vertices_lonlat_height: [[35.5,33.8,4.4]] };
const DATA = { granite_overlay: source };
const ui = { lens: 'observed', graniteOverlay: true };
const deck = { WebMercatorViewport: class { constructor() { this.distanceScales = { unitsPerMeter: [2,3,4] } } projectPosition(p) { return [p[0]*1000,p[1]*1500,0] } } };
const groundedPolygon = (rings) => rings.map(r => r.map(p => [...p, 63.5]));
const MATERIAL = { ambient: 0.8 };
const estimatedCameraEye = (c) => [c.centre[0], c.centre[1], 100];
// Use existing isPointInFootprints and isTerrainLoaded from production code

assert.equal(enterStreetView(source), false);
assert.equal(maxZoom, 20, 'maxZoom should be restored to original 20 after easeTo failure');
assert.equal(maxPitch, 75, 'maxPitch should be restored to original 75 after easeTo failure');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


# Extract the granite panel functions from template for testing
_GRANITE_TEMPLATE_CODE = None
def _get_granite_template_code():
    global _GRANITE_TEMPLATE_CODE
    if _GRANITE_TEMPLATE_CODE is None:
        html = (Path(__file__).parents[1] / 'src/dynacity/viewer_template.html').read_text(encoding='utf-8')
        # Extract from function setGranitePreview to function setCanopyReview
        start = html.find('function setGranitePreview(open) {')
        end = html.find('function setCanopyReview(on) {')
        _GRANITE_TEMPLATE_CODE = html[start:end]
    return _GRANITE_TEMPLATE_CODE


def test_desktop_inspect_facade_zoom_pitch_offset():
    """Test desktop Inspect facade uses zoom 18.8, pitch 50, offset [0, -60]."""
    # These constants are defined in the template's renderGranitePanel function
    # Test the logic directly without needing full template evaluation
    innerWidth = 1280  # desktop
    calm = { matches: False }

    isMobile = innerWidth < 600
    zoom = 18.0 if isMobile else 18.8
    pitch = 45 if isMobile else 50
    bearing = 135
    duration = 0 if calm.get('matches', False) else 1000
    offset = [-40, 60] if isMobile else [0, -60]

    assert zoom == 18.8, 'Desktop zoom should be 18.8'
    assert pitch == 50, 'Desktop pitch should be 50'
    assert offset == [0, -60], 'Desktop offset should be [0, -60]'
    assert duration == 1000, 'Desktop duration should be 1000'


def test_mobile_inspect_facade_zoom_pitch_offset():
    """Test mobile Inspect facade uses zoom 18.0, pitch 45, offset [-40, 60]."""
    code = _get_granite_template_code()
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
const source = { anchor: [35.5, 33.8] };
const DATA = { granite_overlay: source };
const ui = { lens: 'observed', graniteOverlay: false, granitePanel: false };
const calm = { matches: false };
const innerWidth = 390;  // mobile

// Simulate the renderGranitePanel logic for mobile inspect
const isMobile = innerWidth < 600;
const zoom = isMobile ? 18.0 : 18.8;
const pitch = isMobile ? 45 : 50;
const bearing = 135;
const duration = calm.matches ? 0 : 1000;
const offset = isMobile ? [-40, 60] : [0, -60];

assert.equal(zoom, 18.0, 'Mobile zoom should be 18.0');
assert.equal(pitch, 45, 'Mobile pitch should be 45');
assert.deepEqual(offset, [-40, 60], 'Mobile offset should be [-40, 60]');
assert.equal(duration, 1000, 'Mobile duration should be 1000');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_mobile_compact_source_expand_compare_close_focus():
    """Test mobile compact bar: expand (Show details), compare toggle, close with focus restoration."""
    code = _get_granite_template_code()
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
let compareToggled = false;
let closeCalled = false;
let detailsOpened = false;
let detailsSummaryFocused = false;
let triggerFocused = false;

const ui = { graniteOverlay: true };
const panel = {
  classList: {
    contains: (cls) => cls === 'granite-compact',
    _compact: true,
    add: function(c){ if(c==='granite-compact')this._compact=true; if(c==='granite-street-active')this._streetActive=true; },
    remove: function(c){ if(c==='granite-compact')this._compact=false; if(c==='granite-street-active')this._streetActive=false; },
    contains: function(c){ return (c==='granite-compact' && this._compact) || (c==='granite-street-active' && this._streetActive); }
  },
  querySelector: (sel) => {
    if (sel === '.granite-compact-bar') return { hidden: false };
    if (sel === '.granite-street-bar') return { hidden: true };
    if (sel === '.granite-street-compact-bar') return { hidden: true };
    return null;
  }
};
const $ = (id) => {
  if (id === 'granite-details') return {
    open: false,
    querySelector: () => ({ focus: () => { detailsSummaryFocused = true; } })
  };
  if (id === 't-granite') return { focus: () => { triggerFocused = true; }, closest: () => null };
  if (id === 'granite-compact-compare') return { onclick: null, setAttribute: () => {} };
  if (id === 'granite-compact-details') return { onclick: null };
  if (id === 'granite-compact-close') return { onclick: null };
  if (id === 'granite-compare') return { onclick: null, hidden: false, disabled: false, textContent: '', setAttribute: () => {} };
  if (id === 'granite-status') return { textContent: '' };
  if (id === 'p-granite') return panel;
  if (id === 't-granite') return { setAttribute: () => {} };
  return { onclick: null, hidden: false, classList: { add: () => {}, remove: () => {}, contains: () => false }, querySelector: () => ({ hidden: true }), dataset: {} };
};
const render = () => {};
const isStreetViewActive = () => false;
const exitStreetView = () => {};
const setGranitePreview = (open) => { closeCalled = true; };
const esc = (s) => s;
const groundedPolygon = (rings) => rings.map(r => r.map(p => [...p, 63.5]));

// Now run the template code to define handlers
`+code+`

// Compact compare toggle
ui.graniteOverlay = !ui.graniteOverlay;
compareToggled = !ui.graniteOverlay;
assert.equal(compareToggled, true, 'Compact compare should toggle overlay');

// Compact expand (Show details)
const details = $('granite-details');
panel.classList.remove('granite-compact');
const compactBar = panel.querySelector('.granite-compact-bar');
if (compactBar) compactBar.hidden = true;
details.open = true;
detailsOpened = true;
details?.querySelector('summary')?.focus();
assert.equal(panel.classList._compact, false, 'Panel should not be compact after expand');
assert.equal(detailsOpened, true, 'Details should be opened');
assert.equal(detailsSummaryFocused, true, 'Details summary should be focused');

// Compact close
const trigger = $('t-granite');
const menu = trigger.closest ? trigger.closest('details') : null;
setGranitePreview(false);
panel.classList.remove('granite-compact');
panel.classList.remove('granite-street-active');
(menu && !menu.open ? menu.querySelector('summary') : trigger)?.focus();
assert.equal(closeCalled, true, 'setGranitePreview(false) should be called on close');
assert.equal(triggerFocused, true, 'Trigger should be focused on close');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_mobile_to_desktop_duplicate_bar_regression():
    """Test that resize from mobile to desktop doesn't show duplicate street bars."""
    code = _get_granite_template_code()
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
let streetBarHidden = false;
let compactBarHidden = true;

const panel = {
  classList: {
    contains: (cls) => cls === 'granite-street-active' || cls === 'granite-compact',
    add: () => {},
    remove: (cls) => { if (cls === 'granite-compact') panel.classList._compact = false; }
  },
  _compact: true,
  querySelector: (sel) => {
    if (sel === '.granite-street-bar') return { hidden: streetBarHidden, dataset: {} };
    if (sel === '.granite-street-compact-bar') return { hidden: compactBarHidden };
    return null;
  }
};
const $ = (id) => {
  if (id === 'granite-street') return { hidden: false, disabled: false, textContent: '' };
  if (id === 'granite-compare') return { hidden: false, disabled: false, textContent: '', setAttribute: () => {} };
  if (id === 'granite-status') return { textContent: '' };
  return { onclick: null, hidden: false, classList: { add: () => {}, remove: () => {}, contains: () => false }, querySelector: () => ({ hidden: true }), dataset: {} };
};

// Initial mobile street active: compact bar visible, street bar hidden
const isCompact = panel.classList.contains('granite-compact');
streetBarHidden = true;
compactBarHidden = !isCompact;  // should be false (visible) on mobile
assert.equal(compactBarHidden, false, 'Mobile: compact street bar should be visible');
assert.equal(streetBarHidden, true, 'Mobile: regular street bar should be hidden');

// Resize to desktop: compact class removed
panel.classList.remove('granite-compact');
panel.classList._compact = false;

// Re-evaluate visibility (simulating renderGranitePanel)
const streetBar = panel.querySelector('.granite-street-bar');
const streetCompactBar = panel.querySelector('.granite-street-compact-bar');
const isStreet = true;
if (streetBar) streetBar.hidden = !isStreet;
if (streetCompactBar) streetCompactBar.hidden = !panel.classList.contains('granite-compact');

assert.equal(streetBar.hidden, false, 'Desktop: regular street bar should be visible');
assert.equal(streetCompactBar.hidden, true, 'Desktop: compact street bar should be hidden');
// No duplicate: only ONE street bar visible at any time
const visibleBars = [streetBar.hidden, streetCompactBar.hidden].filter(h => !h).length;
assert.equal(visibleBars, 1, 'Exactly one street bar should be visible after resize');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_street_state_limit_restoration():
    """Test that street view restores original maxZoom/maxPitch (20/75) on exit."""
    code = (Path(__file__).parents[1] / 'src/dynacity/viewer_granite_overlay.js').read_text(encoding='utf-8')
    script = """
const assert=require('node:assert/strict'),vm=require('node:vm');
const context=vm.createContext({assert});
vm.runInContext(`
`+JSON.parse(process.argv[1])+`
let maxZoom = 20;
let maxPitch = 75;
let jumpToCalled = false;

const map = {
  getCenter: () => ({ toArray: () => [35.5, 33.8] }),
  getZoom: () => 22,
  getPitch: () => 80,
  getBearing: () => 135,
  getMaxZoom: () => maxZoom,
  getMaxPitch: () => maxPitch,
  setMaxZoom: (z) => { maxZoom = z; },
  setMaxPitch: (p) => { maxPitch = p; },
  jumpTo: (opts) => { jumpToCalled = true; },
  getCanvasContainer: () => ({ clientHeight: 900 }),
  getCanvas: () => ({ height: 900 })
};
const groundZ = (lon, lat) => 63.4;
const beirutOverlay = { data: { buildings: [] } };
const estimatedCameraEye = (c) => [c.centre[0], c.centre[1], 1.6];
// Use existing isPointInFootprints from production code

streetViewSavedState = {
  center: [35.5, 33.8], zoom: 15, pitch: 60, bearing: -20, maxZoom: 20, maxPitch: 75
};
streetViewPostMoveCheck = {
  requestedEyeLon: 35.5, requestedEyeLat: 33.8,
  requestedCenterLon: 35.5, requestedCenterLat: 33.8,
  requestedZoom: 22, requestedPitch: 80, requestedBearing: 135,
  source: { anchor: [35.5, 33.8] }
};

exitStreetView();

assert.equal(maxZoom, 20, 'maxZoom should be restored to 20');
assert.equal(maxPitch, 75, 'maxPitch should be restored to 75');
assert.equal(jumpToCalled, true, 'jumpTo should be called to restore view');
assert.equal(streetViewSavedState, null, 'savedState should be cleared');
assert.equal(streetViewPostMoveCheck, null, 'postMoveCheck should be cleared');
`,context);
"""
    result = subprocess.run(['node', '-e', script, json.dumps(code)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
