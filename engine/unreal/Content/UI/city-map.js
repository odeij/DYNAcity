'use strict';
window.beirutMap = (() => {
  const element = document.querySelector('#city-map');
  const status = document.querySelector('#map-status');
  const tileStatus = document.querySelector('#map-tile-status');
  const detail = document.querySelector('#map-detail');
  const followControl = document.querySelector('#map-follow');
  const overlayControl = document.querySelector('#map-roads');
  const root = 'https://tiles.maps.eox.at/wmts/1.0.0/';
  const streetsCredit = '<a href="https://maps.eox.at/">EOX::Maps</a> · Data © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a> · Rendering © <a href="https://eox.at/">EOX</a> and <a href="https://github.com/MapServer/basemaps">MapServer</a>';
  const satelliteCredit = '<a href="https://cloudless.eox.at/">EOxCloudless</a> by <a href="https://eox.at/">EOX IT Services GmbH</a> (Contains modified Copernicus Sentinel data 2016 &amp; 2017) · <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>';
  let map, roadLayer, playerMarker, layers = {}, active = [], visible = false;
  let reference = null, player = null, roads = [], loaded = false, mode = null;
  let fitPending = false, tileTimer, dataRequested = false, loadGeneration = 0;
  let routeLayer, route = null, routeDrawn = null, playing = false, fitRouteNext = false;
  const finitePoint = value => Array.isArray(value) && value.length >= 2 && value.slice(0, 2).every(Number.isFinite);

  function tileMessage() {
    const failures = active.reduce((count, layer) => count + layer.failed, 0);
    const loading = active.some(layer => layer.isLoading());
    tileStatus.classList.toggle('text-warning', failures > 0);
    tileStatus.textContent = failures ? 'Some map tiles could not load (network or provider limit). Local roads remain available. Use Retry tiles.' :
      loading ? 'Loading geographic map tiles…' : mode === 'street' ? 'Geographic streets · EOX / OpenStreetMap' :
        'Sentinel-2 cloudless 2016 · regional 10 m imagery · zoom capped at 14. Sharper street-level satellite requires a licensed high-resolution provider.';
    clearTimeout(tileTimer);
    if (loading) tileTimer = setTimeout(() => {
      tileStatus.classList.add('text-warning');
      tileStatus.textContent = 'Map tiles are taking too long. Check your connection or use Retry tiles. Local roads remain available.';
    }, 15000);
  }
  // The embedded browser may draw at a display scale above 100%. A 256 px tile then covers more than 256 device
  // pixels and is stretched, which looks soft. On such displays request each tile one zoom level deeper at half the
  // CSS size (same ground area, 4x the pixels). Layers cannot exceed their native zoom, so no detail is invented.
  const displayScale = () => window.devicePixelRatio || 1;
  const hiDpi = displayScale() > 1.05;
  function tileLayer(id, extension, attribution, maxZoom, maxNativeZoom = maxZoom) {
    const layer = L.tileLayer(`${root}${id}/default/g/{z}/{y}/{x}.${extension}`, Object.assign({
      attribution, maxNativeZoom, maxZoom, minZoom: 2, keepBuffer: 0,
      updateWhenIdle: true, updateWhenZooming: false, detectRetina: false
    }, hiDpi ? {tileSize: 128, zoomOffset: 1, maxNativeZoom: maxNativeZoom - 1} : {}));
    layer.failed = 0;
    layer.on('loading', tileMessage);
    layer.on('tileerror', () => { layer.failed++; tileMessage(); });
    layer.on('load', tileMessage);
    return layer;
  }
  // Ground distance per screen pixel at the map centre, and the finest detail the active imagery can show.
  function detailMessage() {
    if (!map) return;
    const metres = 40075016.686 * Math.cos(map.getCenter().lat * Math.PI / 180) / (256 * Math.pow(2, map.getZoom()));
    const limit = mode === 'street' ? (map.getZoom() <= 15 ? 'Street map: OpenStreetMap-derived raster tiles, real detail to zoom 15.' : 'Street map: real detail ends near zoom 15; EOX enlarges it beyond that, so close views look soft. Tick Exported drivable roads for crisp local geometry.') :
      metres < 10 ? 'Imagery source is 10 m per pixel: closer than this only enlarges it, so it cannot get sharper.' :
        'Imagery source detail: about 10 m per pixel.';
    detail.textContent = `Zoom ${map.getZoom()} · about ${metres < 10 ? metres.toFixed(1) : Math.round(metres)} m per screen pixel · display scale ${Math.round(displayScale() * 100)}%${hiDpi ? ' (sharper tiles requested)' : ''}. ${limit}`;
  }
  function setFollow(enabled) {
    followControl.checked = enabled;
    if (enabled) centrePlayer();
  }
  function centrePlayer() {
    if (!map || !reference || !player || !visible) return;
    const latlng = L.latLng(reference.localToLatLng(player));
    if (map.latLngToContainerPoint(latlng).distanceTo(map.getSize().divideBy(2)) > 2) {
      map.panTo(latlng, {animate: true, duration: .3});
    }
  }
  function init() {
    if (map) return;
    map = L.map(element, {
      zoomControl: false, preferCanvas: true, minZoom: 2, maxZoom: mode === 'street' ? 16 : 14,
      zoomAnimation: true, fadeAnimation: true, wheelDebounceTime: 80
    }).setView([33.895, 35.505], 13);
    L.control.scale({imperial: false}).addTo(map);
    layers.street = tileLayer('osm_3857', 'jpg', streetsCredit, 16, 15);
    layers.satellite = tileLayer('s2cloudless_3857', 'jpg', satelliteCredit, 14);
    layers.streets = tileLayer('streets_3857', 'png', streetsCredit, 18);
    layers.streets.setOpacity(.7);
    layers.labels = tileLayer('overlay_bright_3857', 'png', streetsCredit, 14);
    roadLayer = L.featureGroup();
    map.on('zoomend moveend', detailMessage);
    map.on('dragstart', () => setFollow(false));
    map.on('click', event => choose(event.latlng));
    element.addEventListener('keydown', event => {
      if (event.target !== element) return;
      if (event.key.startsWith('Arrow')) setFollow(false);
      if (event.key === 'Enter') { event.preventDefault(); choose(map.getCenter()); }
    });
    routeLayer = L.featureGroup().addTo(map);
    renderRoads();
    renderRoute();
    updatePlayer();
  }
  function applyMode() {
    if (!map || !mode) return;
    active.forEach(layer => map.removeLayer(layer));
    const maxZoom = mode === 'street' ? 16 : 14;
    if (map.getZoom() > maxZoom) map.setZoom(maxZoom, {animate: visible});
    map.setMaxZoom(maxZoom);
    active = mode === 'street' ? [layers.street] : mode === 'satellite' ? [layers.satellite] : [layers.satellite, layers.streets, layers.labels];
    if (visible) active.forEach(layer => { layer.failed = 0; layer.addTo(map); });

    tileMessage();
    detailMessage();
  }
  function renderRoads() {
    if (!map) return;
    roadLayer.clearLayers();
    if (reference) {
      const paths = roads.map(road => road.path.filter(finitePoint).map(point => reference.localToLatLng(point))).filter(path => path.length > 1);
      L.polyline(paths, {color: '#12b8a6', weight: 4, opacity: .85, interactive: false, lineCap: 'round'}).addTo(roadLayer);
    }
    if (overlayControl.checked) roadLayer.addTo(map);
    fitPending = true;
  }
  function fit() {
    if (!map || !visible) { fitPending = true; return; }
    setFollow(false);
    const bounds = roadLayer.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, {padding: [28, 28], maxZoom: Math.min(16, map.getMaxZoom()), animate: false});
    fitPending = false;
  }
  function updatePlayer() {
    document.querySelector('#map-player').disabled = !player || !reference;
    followControl.disabled = !player || !reference;
    if (!map) return;
    if (!reference || !player) {
      if (playerMarker) map.removeLayer(playerMarker);
      return;
    }
    const latlng = reference.localToLatLng(player);
    if (!playerMarker) playerMarker = L.circleMarker(latlng, {radius: 7, color: '#153d32', weight: 3, fillColor: '#b5f4cb', fillOpacity: 1, interactive: false});
    playerMarker.setLatLng(latlng).addTo(map);
    if (followControl.checked) centrePlayer();
  }
  // GTA-style choice at the clicked point: plan a GPS route there, or jump the car there.
  function choose(latlng) {
    if (!reference || !map) return;
    let local;
    try { local = reference.latLngToLocal(latlng); }
    catch (error) { status.textContent = 'Choose a destination inside UTM zone 36N (longitude 30–36° E).'; return; }
    const box = document.createElement('div');
    box.className = 'map-choice';
    const button = (label, style, run) => {
      const element = document.createElement('button');
      element.type = 'button'; element.className = `btn btn-sm ${style}`; element.textContent = label;
      element.addEventListener('click', () => { map.closePopup(); run(); });
      box.append(element);
      return element;
    };
    const routeButton = button('Set GPS route here', 'btn-primary', () => { fitRouteNext = true; action('route_to', local); });
    button('Go there (move the car)', 'btn-outline-dark', () => action('map_pick', local));
    if (!playing) {
      routeButton.disabled = true;
      const note = document.createElement('p');
      note.textContent = 'GPS routes start from your car: choose Start driving first.';
      box.append(note);
    }
    L.popup({closeButton: true, autoPan: true}).setLatLng(latlng).setContent(box).openOn(map);
    (routeButton.disabled ? box.querySelector('button:not([disabled])') : routeButton).focus();
  }
  const km = metres => metres < 1000 ? `${Math.max(0, Math.round(metres / 10) * 10)} m` : `${(metres / 1000).toFixed(1)} km`;
  const minutes = seconds => { const m = Math.max(1, Math.round(seconds / 60)); return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`; };
  function fitRoute() {
    if (!map || !visible || !routeLayer) return;
    const bounds = routeLayer.getBounds();
    if (!bounds.isValid()) return;
    setFollow(false);
    map.fitBounds(bounds, {padding: [36, 36], maxZoom: Math.min(16, map.getMaxZoom())});
  }
  function renderRoute() {
    const panel = document.querySelector('#map-route');
    const active = route && route.active && Array.isArray(route.points) && route.points.length > 1;
    panel.classList.toggle('d-none', !active);
    if (!active) { if (routeLayer) routeLayer.clearLayers(); routeDrawn = null; return; }
    if (map && reference && routeDrawn !== route.version) {
      routeLayer.clearLayers();
      const path = route.points.filter(finitePoint).map(point => reference.localToLatLng(point));
      L.polyline(path, {color: '#2a0533', weight: 9, opacity: .75, interactive: false, lineCap: 'round', lineJoin: 'round'}).addTo(routeLayer);
      L.polyline(path, {color: '#db38ff', weight: 5, opacity: 1, interactive: false, lineCap: 'round', lineJoin: 'round'}).addTo(routeLayer);
      L.circleMarker(path[path.length - 1], {radius: 8, color: '#2a0533', weight: 3, fillColor: '#db38ff', fillOpacity: 1, interactive: false}).addTo(routeLayer);
      routeDrawn = route.version;
      if (fitRouteNext) { fitRouteNext = false; fitRoute(); }
    }
    document.querySelector('#map-route-title').textContent = route.arrived ? `Arrived · ${route.destination_road}` : `GPS route to ${route.destination_road}`;
    const figures = [
      ['Remaining', km(route.remaining_m)], ['Time left', minutes(route.eta_s)],
      ['Total', `${km(route.length_m)} · ${minutes(route.total_s)}`], ['Average speed', `${Math.round(route.average_kmh)} km/h`],
      ['Turns', String(Math.max(0, route.turns))], ['Recalculated', `${route.recalculations}×`]
    ];
    if (route.slowest) figures.push(['Slowest stretch', `${route.slowest.road}, ${Math.round(route.slowest.kmh)} km/h`]);
    document.querySelector('#map-route-figures').replaceChildren(...figures.map(([label, value]) => {
      const cell = document.createElement('span'), strong = document.createElement('strong');
      strong.textContent = value; cell.append(strong, label); return cell;
    }));
    const steps = (route.steps || []).slice(0, 8);
    document.querySelector('#map-route-steps').replaceChildren(...steps.map((step, index) => {
      const item = document.createElement('li');
      item.textContent = step.arrive ? `Arrive at ${step.road} · ${km(step.distance_m)}` : `${step.instruction} onto ${step.road} · in ${km(step.distance_m)}`;
      if (index === 0) item.className = 'next';
      return item;
    }));
    document.querySelector('#map-route-basis').textContent = `Planned at ${String(route.hour).padStart(2, '0')}:00 in-game. ${route.basis || ''}`;
  }
  function resize() {
    if (!map || !visible) return;
    map.invalidateSize({pan: false});
    if (fitPending) fit();
    if (followControl.checked) centrePlayer();
  }

  document.querySelector('#map-in').addEventListener('click', () => { if (map) map.zoomIn(); });
  document.querySelector('#map-out').addEventListener('click', () => { if (map) map.zoomOut(); });
  document.querySelector('#map-fit').addEventListener('click', fit);
  document.querySelector('#map-route-show').addEventListener('click', fitRoute);
  document.querySelector('#map-route-clear').addEventListener('click', () => action('route_clear'));
  document.querySelector('#map-player').addEventListener('click', () => { setFollow(false); centrePlayer(); });
  followControl.addEventListener('change', () => setFollow(followControl.checked));
  overlayControl.addEventListener('change', () => {
    if (!map) return;
    if (overlayControl.checked) roadLayer.addTo(map); else map.removeLayer(roadLayer);
  });
  document.querySelector('#map-retry').addEventListener('click', () => {
    if (visible) active.forEach(layer => { layer.failed = 0; layer.redraw(); });
    tileMessage();
    if (!loaded || !reference) requestData();
  });
  async function requestData() {
    if (dataRequested) return;
    dataRequested = true;
    const generation = loadGeneration;
    await action('map_data');
    dataRequested = false;
    if (generation === loadGeneration) status.textContent = 'Local map data unavailable. Check the Unreal connection and use Retry tiles to try again.';
  }
  addEventListener('resize', resize);
  return {
    load(data) {
      loadGeneration++;
      loaded = true;
      roads = (Array.isArray(data.roads) ? data.roads : []).filter(road => Array.isArray(road.path));
      try {
        reference = cityMapCoordinates.reference(data);
        status.textContent = `${roads.length.toLocaleString()} exported road surfaces · teal = local modeled drivability, not all geographic streets. Destination safety is checked by Unreal.`;
      } catch (error) {
        reference = null;
        status.textContent = `${error.message} Local overlay and destination picking are unavailable; geographic basemaps still work.`;
      }
      renderRoads();
      routeDrawn = null;
      renderRoute();
      resize();
      updatePlayer();
    },
    show() {
      visible = true;
      init();
      resize();
      applyMode();
      if (!loaded) requestData();
    },
    hide() {
      visible = false;
      clearTimeout(tileTimer);
      if (map) { map.stop(); active.forEach(layer => map.removeLayer(layer)); }
    },
    settings(value) {
      if (value.mode === mode) return;
      mode = value.mode;
      applyMode();
    },
    zoomTo(zoom) { if (map && visible) map.setZoom(zoom, {animate: false}); },
    position(value) { player = finitePoint(value) ? value : null; updatePlayer(); },
    route(value, isPlaying) { playing = !!isPlaying; route = value && typeof value === 'object' ? value : null; renderRoute(); },
    fitRoute,
    resize
  };
})();
