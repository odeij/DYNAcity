'use strict';
// DynaCity title / pause menu. Talks to Unreal through the same allowlisted bridge as the control panel
// (window.ue.beirut.execute / getstate); opened outside Unreal it runs as a static preview.
const $ = selector => document.querySelector(selector);
const bridge = () => window.ue && window.ue.beirut;
let state = { playing: false, menu: 'title', preview: true };
let view = 'menu', pending = false, focusIndex = 1, toastTimer, lastResults = '', searchTimer, displayedQuery = '';

// ---------- Background: a real Beirut street photo, wiped left to right into the same view of the model ----------
// Each render was taken from the photo's recorded KartaView position and heading (pitch/yaw refined by overlay),
// then cropped to the phone's field of view, so roads and building edges line up across the wipe.
// The cycle ends by pulling back to the whole city before starting again.
const PAIRS = [
  ['title/match-1-real.jpg', 'title/match-1-model.jpg', 'Mar Mikhael · KartaView 2018'],
  ['title/match-2-real.jpg', 'title/match-2-model.jpg', 'Mar Mikhael · KartaView 2018'],
  ['title/match-3-real.jpg', 'title/match-3-model.jpg', 'Mar Mikhael · KartaView 2018'],
  ['title/match-4-real.jpg', 'title/match-4-model.jpg', 'Mar Mikhael · KartaView 2018'],
  ['title/match-4-model.jpg', 'title/backdrop-1.jpg', null]
];
(function wipes() {
  const stack = $('#bg-stack'), real = $('#bg-real'), model = $('#bg-model'), seam = $('#bg-seam'), tag = $('#bg-tag');
  let index = 0;
  const play = () => {
    const [photo, render, source] = PAIRS[index];
    real.style.backgroundImage = `url("${photo}")`;
    model.style.backgroundImage = `url("${render}")`;
    [model, seam, stack].forEach(el => { el.classList.remove('wipe', 'drift'); void el.offsetWidth; });
    stack.classList.add('drift');
    tag.textContent = source ? `Real Beirut · ${source}` : 'DynaCity model';
    setTimeout(() => {
      [model, seam].forEach(el => el.classList.add('wipe'));
      setTimeout(() => { tag.textContent = source ? 'DynaCity model · same viewpoint' : 'DynaCity · whole city'; }, 1300);
    }, source ? 3200 : 1500);
    index = (index + 1) % PAIRS.length;
  };
  play();
  setInterval(play, 9000);
})();

// ---------- Tiles ----------
const tiles = () => [...document.querySelectorAll('[data-tile]')].filter(el => !el.hidden);
const menuKind = () => state.preview ? 'title' : !state.playing ? 'editor' : state.menu === 'pause' ? 'pause' : 'title';
function setFocus(index) {
  const all = tiles(); if (!all.length) return;
  focusIndex = (index + all.length) % all.length;
  all.forEach((el, i) => el.classList.toggle('focused', i === focusIndex));
  if (view === 'menu') all[focusIndex].focus({ preventScroll: true });
}
function choose(el) {
  if (el.dataset.open) return showView(el.dataset.open);
  if (el.dataset.map) { location.href = 'index.html?overlay=1#map'; return; }
  if (el.dataset.act) action(el.dataset.act);
}
function labelTiles() {
  const kind = menuKind();
  const drive = $('[data-tile="drive"]'), exit = $('[data-tile="exit"]'), fly = $('[data-tile="fly"]');
  drive.dataset.act = kind === 'editor' ? 'drive' : 'resume';
  $('#drive-label').textContent = kind === 'pause' ? 'resume' : 'start driving';
  fly.hidden = kind === 'editor';
  exit.hidden = kind === 'editor';
  $('#exit-label').textContent = kind === 'pause' ? 'End session' : 'Exit to editor';
}

function showView(name) {
  if (!['menu', 'search', 'settings', 'controls', 'about'].includes(name)) name = 'menu';
  view = name;
  document.querySelectorAll('.view').forEach(el => { el.hidden = el.id !== `view-${name}`; });
  if (name === 'menu') setFocus(focusIndex);
  else if (name === 'search') setTimeout(() => $('#query').focus(), 50);
  else { const back = $(`#view-${name} .back`); if (back) back.focus({ preventScroll: true }); }
}
// Called by native code when the overlay is reopened (title, pause, search, settings, controls).
window.showMenu = function (page) {
  if (page === 'title' || page === 'pause') page = 'menu';
  showView(page);
  poll();
};

// ---------- Bridge ----------
function toast(message, error = false) {
  const el = $('#toast'); el.textContent = message; el.classList.toggle('error', error); el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 4200);
}
async function action(name, values = {}) {
  const b = bridge();
  if (!b) { toast('Preview only: open DynaCity in Unreal to use this.', true); return; }
  if (pending) return;
  pending = true;
  try {
    const response = JSON.parse(await b.execute(JSON.stringify({ action: name, ...values })));
    if (response.error) toast(response.error, true);
    else if (response.message) toast(response.message);
    if (name === 'search') displayedQuery = values.query.trim();
    await poll(true);
  } catch (error) { toast(`Could not complete the action: ${error.message}`, true); }
  finally { pending = false; }
}
async function poll(force = false) {
  const b = bridge();
  if (!b || (pending && !force)) return;
  try { render(JSON.parse(await b.getstate())); }
  catch (error) { $('#status-text').textContent = 'Simulation connection unavailable'; }
}

const MODE_TEXT = { city_view: 'Editor view', driving_menu: 'Car held', driving: 'Driving' };
function render(next) {
  state = { ...next, preview: false };
  labelTiles();
  const traffic = next.traffic || {};
  $('#status-dot').classList.toggle('live', !!next.playing);
  $('#status-text').textContent = MODE_TEXT[next.mode] || 'Ready';
  $('#area').textContent = next.area || 'Beirut';
  $('#drive-sub').textContent = `${next.area || 'Beirut'}${traffic.enabled ? ' · traffic running' : ''}`;
  const hour = Number.isFinite(next.time_of_day) ? next.time_of_day : 14;
  $('#clock').textContent = `${String(Math.floor(hour)).padStart(2, '0')}:${String(Math.round((hour % 1) * 60)).padStart(2, '0')}`;
  // Settings
  document.querySelectorAll('[data-tod]').forEach(el => el.classList.toggle('active', Math.abs(parseFloat(el.dataset.tod) - hour) < .3));
  $('#set-night').checked = !!next.night;
  $('#set-clouds').checked = next.clouds_enabled !== false;
  ['#set-night', '#set-clouds', '[data-tod]', '[data-action^="sumo"]'].forEach(sel =>
    document.querySelectorAll(sel).forEach(el => { el.disabled = !next.playing; }));
  Object.entries(next.layers || {}).forEach(([key, on]) => { const el = $(`[data-layer="${key}"]`); if (el) el.checked = on; });
  const map = next.map_settings;
  document.querySelectorAll('[data-map-mode]').forEach(el => el.classList.toggle('active', !!map && el.dataset.mapMode === map.mode));
  $('#set-heading').checked = !!map && map.heading_up;
  $('#traffic-status').textContent = `${traffic.message || 'SUMO disconnected'}${traffic.render && traffic.enabled ? ` · ${traffic.render.visible_vehicles || 0} vehicles` : ''}`;
  // About
  const cov = next.coverage || {};
  $('#cov-roads').textContent = Number.isFinite(cov.municipal_network_length_percent) ? `${cov.municipal_network_length_percent.toFixed(1)}%` : '—';
  $('#cov-terrain').textContent = Number.isFinite(cov.source_terrain_municipal_percent) ? `${cov.source_terrain_municipal_percent.toFixed(1)}%` : '—';
  $('#cov-segments').textContent = cov.exported_road_segments ? cov.exported_road_segments.toLocaleString() : '—';
  // Search
  renderResults(next.results || []);
  if (next.search_status) $('#search-status').textContent = next.search_status;
}

function renderResults(results) {
  const serialized = JSON.stringify(results);
  if (serialized === lastResults) return;
  lastResults = serialized;
  const box = $('#results'); box.replaceChildren();
  results.forEach((row, index) => {
    const button = document.createElement('button'); button.type = 'button';
    const title = document.createElement('strong'); title.textContent = row.name;
    const detail = document.createElement('span'); detail.textContent = row.detail || '';
    button.append(title, detail);
    button.addEventListener('click', () => { if (!pending && $('#query').value.trim() === displayedQuery) action('go_to', { index }); });
    box.append(button);
  });
}
function scheduleSearch(delay = 280) {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    const query = $('#query').value.trim();
    if (query.length < 2) { renderResults([]); return; }
    action('search', { query });
  }, delay);
}

// ---------- Input ----------
document.addEventListener('click', event => {
  const tile = event.target.closest('[data-tile]');
  if (tile) { choose(tile); return; }
  if (event.target.closest('[data-back]')) { showView('menu'); return; }
  const open = event.target.closest('[data-open]');
  if (open) { showView(open.dataset.open); return; }
  const control = event.target.closest('[data-action]');
  if (control && !control.disabled) action(control.dataset.action);
  const chip = event.target.closest('[data-query]');
  if (chip) { $('#query').value = chip.dataset.query; displayedQuery = ''; scheduleSearch(0); }
  const mode = event.target.closest('[data-map-mode]');
  if (mode) action('map_settings', { mode: mode.dataset.mapMode });
});
document.querySelectorAll('[data-tile]').forEach(el =>
  el.addEventListener('mouseenter', () => setFocus(tiles().indexOf(el))));
$('#set-night').addEventListener('change', () => action('night'));
$('#set-clouds').addEventListener('change', () => action('clouds'));
$('#set-heading').addEventListener('change', event => action('map_settings', { heading_up: event.target.checked }));
document.querySelectorAll('[data-layer]').forEach(el => el.addEventListener('change', () => action('layer', { layer: el.dataset.layer, enabled: el.checked })));
$('#query').addEventListener('input', () => { displayedQuery = ''; scheduleSearch(); });
$('#search-form').addEventListener('submit', event => { event.preventDefault(); scheduleSearch(0); });
$('#query').addEventListener('keydown', event => {
  if (event.key === 'ArrowDown') { const first = $('#results button'); if (first) { event.preventDefault(); first.focus(); } }
});
$('#results').addEventListener('keydown', event => {
  const buttons = [...document.querySelectorAll('#results button')], index = buttons.indexOf(document.activeElement);
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); (buttons[index + (event.key === 'ArrowDown' ? 1 : -1)] || $('#query')).focus(); }
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' || event.key === 'F1') {
    event.preventDefault();
    if (view !== 'menu') showView('menu');
    else if (menuKind() === 'pause') action('resume');
    return;
  }
  if (view !== 'menu') return;
  if (['ArrowRight', 'ArrowDown', 'd', 'D', 's', 'S'].includes(event.key)) { event.preventDefault(); setFocus(focusIndex + 1); }
  else if (['ArrowLeft', 'ArrowUp', 'a', 'A', 'w', 'W'].includes(event.key)) { event.preventDefault(); setFocus(focusIndex - 1); }
  else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(tiles()[focusIndex]); }
});

const initial = location.hash.slice(1);
labelTiles();
showView(['search', 'settings', 'controls', 'about'].includes(initial) ? initial : 'menu');
poll(); setInterval(poll, 1000);
