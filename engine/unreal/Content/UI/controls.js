'use strict';
// Bootstrap Sidebars navigation adapted to the local Unreal action bridge.
let state = {};
let pageRequestId = null; // first state only records the id: a reopened panel must not replay an old page request
let pending = false;
let mapSettings = null, mapSettingsSaving = false, mapSettingsError = '', stateEpoch = 0;
const validMapSettings = value => value && value.version === 1 &&
  ['street', 'satellite', 'hybrid'].includes(value.mode) && typeof value.heading_up === 'boolean';
function renderMapSettings() {
  document.querySelectorAll('[data-map-mode]').forEach(button => {
    const selected = !!mapSettings && button.dataset.mapMode === mapSettings.mode;
    button.setAttribute('aria-pressed', String(selected));
    button.classList.toggle('active', selected);
    button.disabled = pending || !mapSettings;
  });
  const heading = document.querySelector('#map-heading');
  heading.checked = !!mapSettings && mapSettings.heading_up;
  heading.disabled = pending || !mapSettings;
  const status = document.querySelector('#map-settings-status');
  status.textContent = mapSettingsSaving ? 'Saving map settings…' : mapSettingsError ? `Error: ${mapSettingsError}` :
    mapSettings ? `Saved · ${mapSettings.mode} · native HUD ${mapSettings.heading_up ? 'heading-up' : 'north-up'}` :
      'Saved settings unavailable. Open this panel inside Unreal with the map settings bridge.';
  status.classList.toggle('text-warning', !!mapSettingsError);
}
function applyMapSettings(value) {
  if (!validMapSettings(value)) return false;
  mapSettings = {...value};
  window.beirutMap.settings(mapSettings);
  renderMapSettings();
  return true;
}
let lastResults = '';
let searchTimer, queuedQuery = null, displayedQuery = '';
const $ = selector => document.querySelector(selector);
function feedback(message, error = false) {
  const box = $('#feedback');
  box.className = `alert ${error ? 'alert-warning' : 'alert-info'}`;
  box.textContent = message;
}
window.showPage = function (page) {
  if (!['explore', 'map', 'search', 'controls', 'layers'].includes(page)) page = 'explore';
  document.querySelectorAll('[data-section]').forEach(el => el.classList.toggle('d-none', el.dataset.section !== page));
  document.querySelectorAll('nav [data-page]').forEach(el => {
    const active = el.dataset.page === page;
    el.classList.toggle('active', active); el.classList.toggle('text-white', !active);
    if (active) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
  history.replaceState(null, '', `#${page}`);
  if (page === 'search') $('#query').focus();
  else { const heading = document.querySelector(`[data-section="${page}"] h1`); if (heading) heading.focus({ preventScroll: true }); }
  if (page === 'map') window.beirutMap.show(); else window.beirutMap.hide();
};
function renderResults(results) {
  const serialized = JSON.stringify(results);
  if (serialized === lastResults) return;
  lastResults = serialized;
  const box = $('#results'); box.replaceChildren();
  results.forEach((row, index) => {
    const button = document.createElement('button'); button.type = 'button';
    button.className = 'list-group-item list-group-item-action py-3';
    const title = document.createElement('strong'); title.textContent = row.name;
    const detail = document.createElement('span'); detail.className = 'd-block small text-body-secondary'; detail.textContent = row.detail || '';
    button.append(title, detail); button.addEventListener('click', () => {
      if (!pending && $('#query').value.trim() === displayedQuery) action('go_to', { index });
    }); box.append(button);
  });
}
const MODE_LABELS = { city_view: 'City view', driving_menu: 'Driving paused · menu open', driving: 'Driving' };
const MODE_NEXT = {
  city_view: 'Editor city view, no car. Pick a tool, or start driving.',
  driving_menu: 'Car held while this menu is open. Resume driving (or press Esc or F1). Simulated traffic keeps running.',
  driving: 'Driving. F1 or Tab opens Controls and holds the car; use ← Main menu there.'
};
function render(next) {
  // Backend-driven navigation (Python request_page); showPage only accepts the known page ids.
  if (next.page_request && next.page_request.id !== pageRequestId) { const replay = pageRequestId !== null; pageRequestId = next.page_request.id; if (replay) { showPage(next.page_request.page); if (Number.isFinite(next.page_request.zoom)) window.beirutMap.zoomTo(next.page_request.zoom); } }
  state = next;
  if (typeof next.map_settings_error === 'string') mapSettingsError = next.map_settings_error;
  if (!applyMapSettings(next.map_settings)) renderMapSettings();
  window.beirutMap.position(next.map_position || null);
  const mode = next.mode || (next.playing ? 'driving' : 'city_view');
  $('#mode').textContent = MODE_LABELS[mode] || mode;
  $('#mode-title').textContent = 'Now:';
  $('#close-menu').textContent = next.playing ? 'Resume driving' : 'Close menu';
  $('#location').textContent = next.area || 'Beirut';
  $('#next-step').textContent = MODE_NEXT[mode] || '';
  $('#control-intro').textContent = next.playing ? 'Close the menu with Resume driving to control your car.' : 'Vehicle controls become active after you choose Start driving.';
  $('#drive strong').textContent = next.playing ? 'Resume driving' : 'Start driving';
  $('#end-drive').classList.toggle('d-none', !next.playing);
  $('#night').textContent = next.night ? 'Switch to day' : 'Switch to night';
  $('#ambient-clouds').textContent = next.clouds_enabled ? 'Clouds on' : 'Clouds off';
  $('#ambient-clouds').setAttribute('aria-pressed', String(!!next.clouds_enabled));
  const traffic = next.traffic || {};
  const coverage = next.coverage || {};
  $('#coverage-terrain').textContent = Number.isFinite(coverage.source_terrain_municipal_percent) ? `${coverage.source_terrain_municipal_percent.toFixed(1)}%` : '—';
  $('#coverage-roads').textContent = Number.isFinite(coverage.municipal_network_length_percent) ? `${coverage.municipal_network_length_percent.toFixed(1)}%` : '—';
  $('#coverage-segments').textContent = coverage.source_road_segments ? `${coverage.exported_road_segments.toLocaleString()} / ${coverage.source_road_segments.toLocaleString()}` : '—';
  $('#traffic-status').textContent = `${traffic.message || 'SUMO disconnected'}${traffic.render && traffic.enabled ? ` · ${traffic.render.visible_vehicles || 0} visible · ${Math.max(0, traffic.render.time_s || 0).toFixed(1)} s` : ''}`;
  document.querySelectorAll('.drive-only').forEach(el => el.classList.toggle('d-none', !next.playing));
  document.querySelectorAll('.editor-only').forEach(el => { el.disabled = !!next.playing; });
  Object.entries(next.layers || {}).forEach(([key, enabled]) => { const el = document.getElementById(key); if (el) el.checked = enabled; });
  renderResults(next.results || []);
  if (next.search_status) $('#search-status').textContent = next.search_status;
}
async function action(name, values = {}) {
  if (pending) return;
  const bridge = window.ue && window.ue.beirut;
  if (!bridge) {
    if (name === 'map_settings') { mapSettingsError = 'Unreal bridge unavailable; settings were not saved.'; renderMapSettings(); }
    feedback('Open this panel inside Unreal to use its controls.', true); return;
  }
  pending = true;
  stateEpoch++;
  if (name === 'map_settings') { mapSettingsSaving = true; mapSettingsError = ''; }
  renderMapSettings();
  document.querySelectorAll('[data-action], [data-layer], #search-form button').forEach(el => { el.disabled = true; });
  try {
    const response = JSON.parse(await bridge.execute(JSON.stringify({ action: name, ...values })));
    if (name === 'map_settings') {
      if (response.error || response.map_settings_error) throw new Error(response.error || response.map_settings_error);
      if (!applyMapSettings(response.map_settings)) throw new Error('Bridge did not confirm saved map settings.');
    }
    if (response.error) feedback(response.error, true);
    else if (response.message) feedback(response.message);
    if (name === 'map_data' && response.map) window.beirutMap.load(response.map);
    if (name === 'map_pick' && !response.error) {
      $('#query').value = displayedQuery = 'Selected map location';
      showPage('search');
    }
    if (name === 'search') displayedQuery = values.query.trim();
    await poll(true);
  } catch (error) {
    if (name === 'map_settings') mapSettingsError = error.message;
    feedback(`Could not complete the action: ${error.message}`, true);
  }
  finally {
    pending = false;
    mapSettingsSaving = false;
    renderMapSettings();
    document.querySelectorAll('[data-action], [data-layer], #search-form button').forEach(el => { el.disabled = false; });
    if (state.playing) document.querySelectorAll('.editor-only').forEach(el => { el.disabled = true; });
    if (queuedQuery !== null) { const query = queuedQuery; queuedQuery = null; action('search', { query }); }
  }
}
async function poll(afterAction = false) {
  const bridge = window.ue && window.ue.beirut;
  if (!bridge || (pending && !afterAction)) return;
  const epoch = stateEpoch;
  try {
    const next = JSON.parse(await bridge.getstate());
    if (epoch === stateEpoch) render(next);
  }
  catch (error) { feedback('The Unreal connection is unavailable. Reopen Explore Beirut from Tools.', true); }
}
document.addEventListener('click', event => {
  if (event.target.closest('[data-skip]')) { event.preventDefault(); $('#content').focus(); return; }
  const nav = event.target.closest('[data-page]');
  if (nav) { event.preventDefault(); showPage(nav.dataset.page); }
  const control = event.target.closest('[data-action]');
  if (control) action(control.dataset.action);
  const shortcut = event.target.closest('[data-query]');
  if (shortcut) { $('#query').value = shortcut.dataset.query; scheduleSearch(0); }
});
function scheduleSearch(delay = 280) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const query = $('#query').value.trim();
    if (query.length < 2) { queuedQuery = null; renderResults([]); return; }
    if (pending) queuedQuery = query; else action('search', { query });
  }, delay);
}
$('#query').addEventListener('input', () => { displayedQuery = ''; scheduleSearch(); });
$('#query').addEventListener('keydown', event => {
  if (event.key === 'ArrowDown') { const first = $('#results button'); if (first) { event.preventDefault(); first.focus(); } }
});
$('#results').addEventListener('keydown', event => {
  const buttons = [...document.querySelectorAll('#results button')], index = buttons.indexOf(document.activeElement);
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); (buttons[index + (event.key === 'ArrowDown' ? 1 : -1)] || $('#query')).focus(); }
});
$('#search-form').addEventListener('submit', event => { event.preventDefault(); scheduleSearch(0); });
document.querySelectorAll('[data-map-mode]').forEach(button => button.addEventListener('click', () => {
  if (!pending && mapSettings && button.dataset.mapMode !== mapSettings.mode) action('map_settings', {mode: button.dataset.mapMode});
}));
$('#map-heading').addEventListener('change', event => {
  const heading_up = event.target.checked;
  renderMapSettings();
  if (!pending && mapSettings) action('map_settings', {heading_up});
});
document.querySelectorAll('[data-layer]').forEach(el => el.addEventListener('change', () => action('layer', { layer: el.dataset.layer, enabled: el.checked })));
// Opened from the in-game title overlay (title.html → City map): no sidebar, and Back/Esc return to that menu.
const overlay = new URLSearchParams(location.search).has('overlay');
if (overlay) {
  document.body.classList.add('overlay');
  const back = document.createElement('button');
  back.className = 'btn btn-outline-light btn-sm'; back.id = 'overlay-back'; back.textContent = '‹ Back to menu';
  back.addEventListener('click', () => { location.href = 'title.html'; });
  $('#content header').prepend(back);
}
// Esc or F1 resumes driving. Tab stays ordinary focus navigation here; Tab/F1 in the game window opens this menu.
document.addEventListener('keydown', event => {
  if (overlay && event.key === 'Escape') { event.preventDefault(); location.href = 'title.html'; return; }
  if ((event.key === 'Escape' || event.key === 'F1') && state.playing) { event.preventDefault(); action('resume'); }
});
// When the panel window gains keyboard focus, put it back on the current page heading so Tab/Enter start from there.
window.addEventListener('focus', () => {
  if (document.activeElement && document.activeElement !== document.body) return;
  const heading = document.querySelector('[data-section]:not(.d-none) h1');
  if (heading) heading.focus({ preventScroll: true });
});
renderMapSettings(); showPage(location.hash.slice(1)); poll(); setInterval(poll, 1000);
