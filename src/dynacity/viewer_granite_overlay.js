// One explicit historical estimate. The mapped parent is never replaced.
const graniteMeshCache = new WeakMap();

// Street-level camera state (saved for restoration)
let streetViewSavedState = null;

// Post-movement validation state
let streetViewPostMoveCheck = null;

// Token to prevent reentrant validation during restoration
let streetViewValidationActive = false;

function saveViewState() {
  if (!map) return null;
  return {
    center: map.getCenter().toArray(),
    zoom: map.getZoom(),
    pitch: map.getPitch(),
    bearing: map.getBearing(),
    maxZoom: map.getMaxZoom(),
    maxPitch: map.getMaxPitch()
  };
}

function restoreViewState(state) {
  if (!map || !state) return;
  map.jumpTo({
    center: state.center,
    zoom: state.zoom,
    pitch: state.pitch,
    bearing: state.bearing
  });
  // Restore original limits
  map.setMaxZoom(state.maxZoom);
  map.setMaxPitch(state.maxPitch);
}

// Check if a point is inside any building footprint (outer ring)
function isPointInFootprints(lon, lat, buildings) {
  for (const b of buildings) {
    for (const part of b.parts) {
      const ring = part[0]; // outer ring
      let inside = false;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const xi = ring[i][0], yi = ring[i][1];
        const xj = ring[j][0], yj = ring[j][1];
        const intersect = ((yi > lat) !== (yj > lat)) &&
          (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi);
        if (intersect) inside = !inside;
      }
      if (inside) return true;
    }
  }
  return false;
}

// Check if terrain grid is actually loaded (not just groundZ function existing)
function isTerrainLoaded() {
  // ground is a global variable in the browser (window.ground)
  // In Node.js vm tests, it's a global variable
  return !!(typeof ground !== 'undefined' && ground && ground.grid && ground.grid.length > 0);
}

// Normalize MapLibre LngLatLike to [lon, lat] array
function normalizeLngLat(ll) {
  if (!ll) return null;
  if (Array.isArray(ll)) return [Number(ll[0]), Number(ll[1])];
  if (typeof ll.lng === 'number' && typeof ll.lat === 'number') return [ll.lng, ll.lat];
  if (typeof ll.lon === 'number' && typeof ll.lat === 'number') return [ll.lon, ll.lat];
  return null;
}

function computeStreetViewCamera(source) {
  // Compute a street-level camera pose for the facade estimate using MapLibre's
  // calculateCameraOptionsFromTo API.
  // FROM = desired eye position (near facade, 1.6m above rendered ground at eye XY)
  // TO = look-at point on the facade (anchor point at facade base height)
  // Returns CameraOptions { center, zoom, bearing, pitch, elevation? } or null if not possible.
  if (!map || !source) return null;
  if (typeof map.calculateCameraOptionsFromTo !== 'function') return null;
  if (typeof groundZ !== 'function') return null;
  if (!isTerrainLoaded()) return null; // terrain grid not loaded

  // Get rendered ground elevation at anchor (look-at point)
  const anchor = source.anchor;
  const targetLon = anchor[0];
  const targetLat = anchor[1];

  // Target point: on the facade at anchor, at facade base height
  const parent = (beirutOverlay.data?.buildings ?? []).find(b => b.id === source.parent.id);
  if (!parent) return null;

  const facadeBase = groundedPolygon(parent.parts[0])[0][0][2];
  if (!Number.isFinite(facadeBase)) return null;
  const targetAlt = facadeBase;

  // Desired eye position: offset from target horizontally toward the street side,
  // 1.6m above rendered ground at eye position.
  // For this building the facade faces roughly SE (bearing ~135 from facade to street),
  // so the street side is NW of the facade (bearing ~315 from target to eye).
  const streetBearingFromTarget = 315; // degrees
  const streetDistance = 12; // meters from facade

  // Compute eye lon/lat from target + bearing + distance
  const R = 6371000;
  const bearingRad = streetBearingFromTarget * Math.PI / 180;
  const dLat = (streetDistance / R) * Math.cos(bearingRad) * (180 / Math.PI);
  const dLon = (streetDistance / R) * Math.sin(bearingRad) * (180 / Math.PI) / Math.cos(targetLat * Math.PI / 180);

  const eyeLon = targetLon + dLon;
  const eyeLat = targetLat + dLat;

  // Reject if eye falls inside any known building footprint (parent or neighbors)
  const buildings = beirutOverlay.data?.buildings ?? [];
  if (isPointInFootprints(eyeLon, eyeLat, buildings)) return null;

  // Get ground at eye position from rendered terrain
  const eyeGround = groundZ(eyeLon, eyeLat);
  if (!Number.isFinite(eyeGround)) return null;

  // Get ground at target position from rendered terrain (for sloping ground case)
  const targetGround = groundZ(targetLon, targetLat);
  if (!Number.isFinite(targetGround)) return null;

  // Eye altitude = ground at eye + 1.6m (pedestrian eye height) in MSL
  const eyeAlt = eyeGround + 1.6;

  // FROM = eye position (lon, lat, altitude in metres above sea level)
  // TO = target position (lon, lat, altitude in metres above sea level)
  // MapLibre expects altitudes in metres above sea level (same as groundZ)
  const from = [eyeLon, eyeLat];
  const to = [targetLon, targetLat];

  let camOptions;
  try {
    camOptions = map.calculateCameraOptionsFromTo(from, eyeAlt, to, targetAlt);
  } catch (e) {
    return null;
  }
  if (!camOptions || !Number.isFinite(camOptions.zoom) || !Number.isFinite(camOptions.pitch)) {
    return null;
  }

  // Normalize center to [lon, lat] array
  const center = normalizeLngLat(camOptions.center);
  if (!center || !Number.isFinite(center[0]) || !Number.isFinite(center[1])) return null;

  const bearing = Number(camOptions.bearing);
  if (!Number.isFinite(bearing)) return null;

  // Reject if the API solution requires zoom > 24 or pitch > 85 (MapLibre absolute max)
  // These would be clamped by the map and the validated pose would not match.
  if (camOptions.zoom > 24 || camOptions.pitch > 85) {
    return null;
  }

  // Verify the returned camera actually places the eye near the requested position
  // by checking estimatedCameraEye against our computed eye position.
  // estimatedCameraEye returns [lon, lat, heightAboveCentre] where heightAboveCentre
  // is the vertical distance from the camera's target/center elevation, NOT absolute MSL.
  // We verify XY proximity and true AGL (above ground level) at the eye position.
  // Use the API's own centre elevation (camOptions.elevation if present) for true AGL.
  const viewportHeight = map.getCanvasContainer().clientHeight || map.getCanvas().height;
  const testEye = estimatedCameraEye({
    centre: center,
    zoom: camOptions.zoom,
    pitch: camOptions.pitch,
    bearing: bearing,
    height: viewportHeight
  });
  if (!testEye || !Number.isFinite(testEye[2])) return null;

  // Verify XY proximity: testEye[0], testEye[1] should be near requested eyeLon, eyeLat
  const dx = (testEye[0] - eyeLon) * 111320 * Math.cos(eyeLat * Math.PI / 180);
  const dy = (testEye[1] - eyeLat) * 111320;
  const horizError = Math.sqrt(dx * dx + dy * dy);
  if (horizError > 5.0) return null; // >5m horizontal error

  // Verify true AGL at eye position using API's own centre elevation semantics.
  // estimatedCameraEye[2] is height above camera center elevation.
  // The API's camOptions.elevation is the centre elevation for the solved camera.
  // If elevation is not provided, the API uses the current map center elevation.
  // For preflight validation, we use targetAlt (the TO altitude) as the centre elevation
  // since the camera center is the look-at point (target).
  const apiCentreElevation = Number.isFinite(camOptions.elevation) ? camOptions.elevation : targetAlt;
  // True AGL at eye = apiCentreElevation + heightAboveCentre - eyeGround
  const trueEyeAGL = apiCentreElevation + testEye[2] - eyeGround;
  if (Math.abs(trueEyeAGL - 1.6) > 0.8) return null; // allow 0.8m tolerance

  // Accept what the API returns if finite and verification passes.
  // NO post-validation clamps: reject out-of-bounds rather than alter pose.
  // Preserve valid API options including elevation.
  const result = {
    center: center,
    zoom: camOptions.zoom,
    bearing: bearing,
    pitch: camOptions.pitch
  };
  if (Number.isFinite(camOptions.elevation)) {
    result.elevation = camOptions.elevation;
  }
  return result;
}

function enterStreetView(source) {
  if (!map || !source) return false;
  if (streetViewSavedState !== null) return false; // already active

  // Save current state ONCE (includes current maxZoom/maxPitch)
  streetViewSavedState = saveViewState();
  if (!streetViewSavedState) return false;

  // Compute street view camera
  const cam = computeStreetViewCamera(source);
  if (!cam) {
    streetViewSavedState = null;
    return false;
  }

  // Widen limits for street view to MapLibre absolute max (24/85)
  // App default is 20/75; computeStreetViewCamera validates pose fits within 24/85.
  // Track which setters succeeded for rollback on partial failure.
  let zoomWidened = false;
  let pitchWidened = false;
  try {
    map.setMaxZoom(24);
    zoomWidened = true;
    map.setMaxPitch(85);
    pitchWidened = true;
  } catch (e) {
    // Rollback any limits that were successfully widened
    if (pitchWidened) {
      try { map.setMaxPitch(streetViewSavedState.maxPitch); } catch (_) {}
    }
    if (zoomWidened) {
      try { map.setMaxZoom(streetViewSavedState.maxZoom); } catch (_) {}
    }
    streetViewSavedState = null;
    return false;
  }

  // Animate to street view - respect reduced motion
  const duration = (window.matchMedia('(prefers-reduced-motion: reduce)').matches) ? 0 : 1500;
  const easeOptions = {
    center: cam.center,
    zoom: cam.zoom,
    pitch: cam.pitch,
    bearing: cam.bearing,
    duration: duration,
    essential: true // respect reduced motion
  };
  if (Number.isFinite(cam.elevation)) {
    easeOptions.elevation = cam.elevation;
  }

  // Recompute the true requested eye position (same as in computeStreetViewCamera)
  const anchor = source.anchor;
  const targetLon = anchor[0];
  const targetLat = anchor[1];
  const parent = (beirutOverlay.data?.buildings ?? []).find(b => b.id === source.parent.id);
  if (!parent) {
    // Restore limits before clearing state
    try { map.setMaxPitch(streetViewSavedState.maxPitch); } catch (_) {}
    try { map.setMaxZoom(streetViewSavedState.maxZoom); } catch (_) {}
    streetViewSavedState = null;
    return false;
  }
  const streetBearingFromTarget = 315;
  const streetDistance = 12;
  const R = 6371000;
  const bearingRad = streetBearingFromTarget * Math.PI / 180;
  const dLat = (streetDistance / R) * Math.cos(bearingRad) * (180 / Math.PI);
  const dLon = (streetDistance / R) * Math.sin(bearingRad) * (180 / Math.PI) / Math.cos(targetLat * Math.PI / 180);
  const requestedEyeLon = targetLon + dLon;
  const requestedEyeLat = targetLat + dLat;

  // Store the requested pose for post-movement validation
  streetViewPostMoveCheck = {
    requestedEyeLon: requestedEyeLon,
    requestedEyeLat: requestedEyeLat,
    requestedCenterLon: cam.center[0],
    requestedCenterLat: cam.center[1],
    requestedZoom: cam.zoom,
    requestedPitch: cam.pitch,
    requestedBearing: cam.bearing,
    requestedElevation: cam.elevation,
    source: source
  };

  try {
    map.easeTo(easeOptions);
  } catch (e) {
    // Restore original limits BEFORE clearing state
    try { map.setMaxPitch(streetViewSavedState.maxPitch); } catch (_) {}
    try { map.setMaxZoom(streetViewSavedState.maxZoom); } catch (_) {}
    streetViewSavedState = null;
    streetViewPostMoveCheck = null;
    return false;
  }

  return true;
}

function exitStreetView() {
  if (!map) return;
  if (streetViewSavedState) {
    // Retire pending validation token and saved state BEFORE map state changes
    const saved = streetViewSavedState;
    streetViewSavedState = null;
    streetViewPostMoveCheck = null;
    // Set reentrancy guard
    streetViewValidationActive = true;
    try {
      // Restore original limits FIRST
      map.setMaxZoom(saved.maxZoom);
      map.setMaxPitch(saved.maxPitch);
      // Then restore view
      map.jumpTo({
        center: saved.center,
        zoom: saved.zoom,
        pitch: saved.pitch,
        bearing: saved.bearing
      });
    } finally {
      streetViewValidationActive = false;
    }
  }
  // If already inactive (streetViewSavedState was null), do nothing.
  // Do NOT reset to home, do NOT jump again. Prevents duplicate-exit jumps.
}

function isStreetViewActive() {
  return streetViewSavedState !== null;
}

// Post-movement validation: called after map.easeTo completes
// Validates actual camera pose matches requested pose within tolerance.
function validateStreetViewPostMove() {
  if (!map || !streetViewPostMoveCheck) return null;

  const check = streetViewPostMoveCheck;
  const source = check.source;
  if (!source) return null;

  // Read back actual camera state - reject non-finite explicitly
  const actualCenter = map.getCenter();
  const actualZoom = map.getZoom();
  const actualPitch = map.getPitch();
  const actualBearing = map.getBearing();
  const actualCentreElevation = map.getCenterElevation?.() ?? map.getCameraTargetElevation?.() ?? 0;

  // Reject any non-finite camera values
  if (!Number.isFinite(actualZoom) || !Number.isFinite(actualPitch) ||
      !Number.isFinite(actualBearing) || !Number.isFinite(actualCentreElevation) ||
      !Number.isFinite(actualCenter?.lng) || !Number.isFinite(actualCenter?.lat)) {
    return { valid: false, reason: 'nonfinite_camera' };
  }

  // Verify pose wasn't clamped (actual exceeds MapLibre max)
  if (actualZoom > 24 || actualPitch > 85) {
    return { valid: false, reason: 'clamped' };
  }

  // Verify true AGL at actual eye position
  // estimatedCameraEye returns [lon, lat, heightAboveCentre]
  const viewportHeight = map.getCanvasContainer().clientHeight || map.getCanvas().height;
  const testEye = estimatedCameraEye({
    centre: [actualCenter.lng, actualCenter.lat],
    zoom: actualZoom,
    pitch: actualPitch,
    bearing: actualBearing,
    height: viewportHeight
  });
  if (!testEye || !Number.isFinite(testEye[0]) || !Number.isFinite(testEye[1]) || !Number.isFinite(testEye[2])) {
    return { valid: false, reason: 'eye_compute_failed' };
  }

  // Validate eye footprint at readback (not only preflight)
  const buildings = beirutOverlay.data?.buildings ?? [];
  if (isPointInFootprints(testEye[0], testEye[1], buildings)) {
    return { valid: false, reason: 'eye_in_footprint' };
  }

  // Verify XY proximity to requested eye position (not center)
  const dx = (testEye[0] - check.requestedEyeLon) * 111320 * Math.cos(testEye[1] * Math.PI / 180);
  const dy = (testEye[1] - check.requestedEyeLat) * 111320;
  const horizError = Math.sqrt(dx * dx + dy * dy);
  if (horizError > 5.0) {
    return { valid: false, reason: 'xy_drift' };
  }

  // Verify zoom/pitch/bearing within tolerance of requested (wrap bearing difference)
  const bearingDiff = Math.abs((actualBearing - check.requestedBearing + 180) % 360 - 180);
  if (Math.abs(actualZoom - check.requestedZoom) > 0.5 ||
      Math.abs(actualPitch - check.requestedPitch) > 2.0 ||
      bearingDiff > 5.0) {
    return { valid: false, reason: 'pose_mismatch' };
  }

  // Get ground at actual eye position
  const eyeGround = groundZ(testEye[0], testEye[1]);
  if (!Number.isFinite(eyeGround)) {
    return { valid: false, reason: 'ground_unavailable' };
  }

  // True AGL = actual centre elevation + heightAboveCentre - eyeGround
  const trueEyeAGL = actualCentreElevation + testEye[2] - eyeGround;
  if (!Number.isFinite(trueEyeAGL) || Math.abs(trueEyeAGL - 1.6) > 0.8) {
    return { valid: false, reason: 'agl_mismatch' };
  }

  // All checks passed
  return {
    valid: true,
    actualCenter: [actualCenter.lng, actualCenter.lat],
    actualZoom: actualZoom,
    actualPitch: actualPitch,
    actualBearing: actualBearing,
    actualCentreElevation: actualCentreElevation,
    actualEyeAGL: trueEyeAGL,
    actualEyeXY: [testEye[0], testEye[1]],
    reason: 'validated'
  };
}

// Hook for camera move end - call from template's cameraMoveFinished
function onStreetViewMoveEnd() {
  if (!streetViewPostMoveCheck) return; // not in street view transition
  if (streetViewValidationActive) return; // prevent reentrancy during restoration

  const validation = validateStreetViewPostMove();
  if (!validation || !validation.valid) {
    // Validation failed - restore overview and clear state
    exitStreetView();
    // Re-render panel to show unavailable status immediately
    if (typeof renderGranitePanel === 'function') {
      const statusEl = document.getElementById('granite-status');
      if (statusEl) {
        statusEl.textContent = 'Street-level inspection unavailable: camera pose could not be validated after movement.';
        renderGranitePanel();
      }
    }
    return;
  }

  // Validation passed - update street bar with actual readback data
  const panel = document.getElementById('p-granite');
  if (panel) {
    const streetBar = panel.querySelector('.granite-street-bar');
    if (streetBar && streetBar.dataset) {
      streetBar.dataset.zoomActual = validation.actualZoom.toFixed(2);
      streetBar.dataset.pitchActual = validation.actualPitch.toFixed(1);
      streetBar.dataset.centreElevation = Number.isFinite(validation.actualCentreElevation) ? validation.actualCentreElevation.toFixed(2) : '';
      streetBar.dataset.eyeAglActual = validation.actualEyeAGL.toFixed(2);
      streetBar.dataset.eyeXyActual = `${validation.actualEyeXY[0].toFixed(6)},${validation.actualEyeXY[1].toFixed(6)}`;
      streetBar.dataset.validationReason = validation.reason;
    }
  }

  // Clear the post-move check after successful validation
  streetViewPostMoveCheck = null;
}

function graniteOverlayStatus() {
  const source = DATA.granite_overlay;
  if (!source) return 'No facade estimate was loaded.';
  if (ui.lens !== 'observed') return 'Use the Observed view to inspect this historical estimate.';
  if (!beirutOverlay.active) return 'Choose Beirut 001 building geometry to inspect this estimate.';
  const matches = (beirutOverlay.data?.buildings ?? []).filter((b) => b.id === source.parent.id);
  if (matches.length !== 1) return 'Waiting for the matching source building.';
  const parent = matches[0];
  if (Math.abs(parent.ground_z - source.parent.ground_z) > 0.005 ||
      Math.abs(parent.top_z - source.parent.top_z) > 0.005 ||
      Math.abs(parent.height_m - source.parent.height_m) > 0.005 ||
      JSON.stringify(parent.parts) !== JSON.stringify(source.parent.parts))
    return 'The parent geometry changed. Prepare this estimate again before viewing.';
  return '';
}
function graniteOverlayMesh(source) {
  if (graniteMeshCache.has(source)) return graniteMeshCache.get(source);
  // Ask the exact running deck.gl projection for its local metre scale. Do not
  // treat UTM grid axes as true east/north or assume a degrees/metres constant.
  const [longitude, latitude] = source.anchor;
  const vp = new deck.WebMercatorViewport({longitude, latitude, zoom: 0, width: 512, height: 512});
  const origin = vp.projectPosition([longitude, latitude, 0]), scales = vp.distanceScales.unitsPerMeter;
  const positions = new Float32Array(source.vertices_lonlat_height.length * 3);
  source.vertices_lonlat_height.forEach((p, i) => {
    const world = vp.projectPosition([p[0], p[1], 0]);
    positions.set([(world[0] - origin[0]) / scales[0], (world[1] - origin[1]) / scales[1], p[2]], i * 3);
  });
  const normals = new Float32Array(positions.length);
  for (let i = 0; i < positions.length; i += 9) {
    const ax=positions[i+3]-positions[i], ay=positions[i+4]-positions[i+1], az=positions[i+5]-positions[i+2];
    const bx=positions[i+6]-positions[i], by=positions[i+7]-positions[i+1], bz=positions[i+8]-positions[i+2];
    const n=[ay*bz-az*by, az*bx-ax*bz, ax*by-ay*bx], length=Math.hypot(...n);
    if (!Number.isFinite(length) || length < 1e-10) throw new Error('Degenerate facade estimate triangle');
    for (let v = 0; v < 3; v++) normals.set(n.map((x) => x / length), i + v * 3);
  }
  const mesh = {positions: {value: positions, size: 3}, normals: {value: normals, size: 3},
    texCoords: {value: new Float32Array(source.vertices_lonlat_height.length * 2), size: 2}};
  graniteMeshCache.set(source, mesh);
  return mesh;
}
function graniteOverlayLayers() {
  if (!ui.graniteOverlay || graniteOverlayStatus()) return [];
  const source = DATA.granite_overlay;
  // Exactly the parent's existing ground plane, not the asset's model Z plus DEM.
  const base = groundedPolygon(source.parent.parts[0])[0][0][2];
  return [new deck.SimpleMeshLayer({
    id: 'granite-balcony-estimate', data: [source], mesh: graniteOverlayMesh(source),
    getPosition: [source.anchor[0], source.anchor[1], base], getColor: [118, 121, 124],
    material: MATERIAL, pickable: false,
  })];
}
