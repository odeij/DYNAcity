// Run with node --test tests/viewer_terrain.test.cjs.
// The page's ground sampler and the layers that stand on it, on a synthetic 1 m-per-cell eastward slope.
require('./viewer_ground_helper.cjs');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`, 'utf8');
const edges = fs.readFileSync(`${__dirname}/../src/dynacity/viewer_street_edges.js`, 'utf8');
const mPerLon = (lat) => 111320 * Math.cos((lat * Math.PI) / 180);
const metresBetween = (a, b) => Math.hypot((b[0] - a[0]) * mPerLon(a[1]), (b[1] - a[1]) * 111320);
const slice = (start, end) => html.slice(html.indexOf(start), html.indexOf(end));
const WEST = 35.5, NORTH = 33.91, CELL = 1e-4, COLS = 40, ROWS = 40, LAT = 33.9;
// Height in metres = column index: 1 m per 1e-4 degrees of longitude, so about 0.108 m per metre eastwards.
const terrain = () => {
  const grid = new Int16Array(COLS * ROWS);
  for (let r = 0; r < ROWS; r++) for (let c = 0; c < COLS; c++) grid[r * COLS + c] = c * 100;
  return grid;
};
function scene(extra = {}) {
  const context = {DATA: {terrain: {west: WEST, north: NORTH, cell: CELL, cols: COLS, rows: ROWS, unit_m: 0.01}, traffic: {roads: []}}, metresBetween, ...extra};
  vm.createContext(context);
  vm.runInContext('ground.grid = null', context);
  vm.runInContext(slice('// ---- bridges', 'function roadMarkings('), context);
  vm.runInContext(slice('// Height of road `i`', 'function simulationSummary('), context);
  context.setGrid = (grid) => { context.__grid = grid; vm.runInContext('ground.grid = __grid', context); };
  context.setGrid(terrain());
  return context;
}
const lonAt = (col) => WEST + col * CELL;
const run = (context, code) => vm.runInContext(code, context);

test('groundZ is bilinear on the grid and holds the nearest edge value beyond it', () => {
  const c = scene();
  assert.ok(Math.abs(c.groundZ(lonAt(10), LAT) - 10) < 1e-9);
  assert.ok(Math.abs(c.groundZ(lonAt(10.25), LAT) - 10.25) < 1e-9);
  assert.equal(c.groundZ(lonAt(-50), LAT), 0);
  assert.equal(c.groundZ(lonAt(500), LAT), COLS - 1);
  assert.equal(c.groundZ(lonAt(10), NORTH + 1), c.groundZ(lonAt(10), NORTH));
});

test('without a grid the city stays on the flat plane', () => {
  const c = scene();
  c.setGrid(null);
  assert.equal(c.groundZ(lonAt(10), LAT), 0);
  assert.deepEqual(Array.from(c.groundPath([[WEST, LAT], [lonAt(30), LAT]], .1)).map((p) => p.join()), ['35.5,33.9,0.1', '35.503,33.9,0.1']);
  assert.deepEqual(Array.from(c.groundedPolygon([[[WEST, LAT], [lonAt(2), LAT], [lonAt(2), LAT + .0001]]], 0)[0][0]), [WEST, LAT, 0]);
});

test('groundPath adds a point at least every step metres, each on the ground plus the lift', () => {
  const c = scene();
  const path = c.groundPath([[lonAt(2), LAT], [lonAt(30), LAT]], .05, 8);
  const length = metresBetween(path[0], path.at(-1));
  assert.ok(path.length >= Math.ceil(length / 8) + 1);
  for (let i = 1; i < path.length; i++) assert.ok(metresBetween(path[i - 1], path[i]) <= 8 + 1e-6);
  for (const p of path) assert.ok(Math.abs(p[2] - (c.groundZ(p[0], p[1]) + .05)) < 1e-9);
});

test('a footprint stands on its lowest corner: every vertex at one level, holes included', () => {
  const c = scene();
  const rings = [[[lonAt(4), LAT], [lonAt(8), LAT], [lonAt(8), LAT + 1e-4], [lonAt(4), LAT + 1e-4]], [[lonAt(5), LAT], [lonAt(6), LAT], [lonAt(6), LAT + 1e-5]]];
  const polygon = c.groundedPolygon(rings, .02);
  for (const ring of polygon) for (const p of ring) assert.ok(Math.abs(p[2] - 4.02) < 1e-9);
});

test('streets on the ground follow the relief and bridges keep their deck height above it', () => {
  const c = scene();
  const at = (col) => [lonAt(col), LAT];
  c.DATA.traffic.roads = [{u: 1, v: 2, path: [at(2), at(30)]}, {u: 2, v: 3, lvl: 1, deck: 6, path: [at(30), at(36)]}, {u: 3, v: 4, path: [at(36), at(38)]}];
  run(c, 'elevateRoads()');
  const [street, bridge] = c.DATA.traffic.roads;
  assert.ok(street.path.length > 30);
  for (const p of street.path) {
    assert.equal(p.length, 4);
    assert.ok(Math.abs(p[2] - c.groundZ(p[0], p[1])) < 1e-9);
    assert.equal(p[3], 0);
  }
  for (const p of bridge.path) assert.ok(Math.abs(p[2] - c.groundZ(p[0], p[1]) - p[3]) < 1e-9);
  assert.ok(Math.max(...bridge.path.map((p) => p[3])) <= 6 + 1e-9);
});

test('cars sit .04 m above the ground, keep their height above it through a junction, and pitch with the grade', () => {
  const c = scene();
  c.DATA.traffic.roads = [{u: 1, v: 2, path: [[lonAt(2), LAT], [lonAt(30), LAT]]}, {u: 2, v: 3, path: [[lonAt(30), LAT], [lonAt(38), LAT]]}];
  run(c, 'elevateRoads()');
  const car = {i: 1, pose: {position: [lonAt(15), LAT], orientation: [0, 0, 0]}};
  c.carPose(car);
  assert.ok(Math.abs(car.pose.position[2] - (c.groundZ(lonAt(15), LAT) + .04)) < 1e-9);
  // Heading east (0 deg) goes uphill here: the nose rises, which is negative deck.gl pitch.
  const grade = 1 / (CELL * mPerLon(LAT));
  assert.ok(Math.abs(car.pose.orientation[0] + Math.atan(grade) * 180 / Math.PI) < 0.05);
  // A junction (road 0, away from its path) keeps the last height above the ground.
  car.i = 0;
  car.pose.position[0] = lonAt(20);
  c.carPose(car);
  assert.ok(Math.abs(car.pose.position[2] - (c.groundZ(lonAt(20), LAT) + .04)) < 1e-9);
  assert.equal(c.roadHeightAt(1, lonAt(15), LAT), c.groundZ(lonAt(15), LAT));
});

test('sidewalk and kerb strips follow the road level and are skipped on bridges by their height above ground', () => {
  const c = scene({roadWidth: () => 6.4, pavementWidth: () => 1.6, shade: () => [0, 0, 0, 0], isDark: () => false, nightLevel: () => 0});
  vm.runInContext(edges, c);
  const roads = [{u: 1, v: 2, cls: 'residential', path: [[lonAt(2), LAT], [lonAt(30), LAT]]},
    {u: 3, v: 4, cls: 'residential', lvl: 1, path: [[lonAt(2), LAT + 3e-4], [lonAt(30), LAT + 3e-4]]}];
  c.DATA.traffic.roads = roads;
  run(c, 'elevateRoads()');
  const {walks, curbs} = c.streetEdgeGeometry(roads, [lonAt(15), LAT]);
  assert.ok(walks.length > 20 && curbs.length === walks.length);
  assert.ok(walks.every((w) => w.roadIndex === 0));
  for (const w of walks) {
    const [p, q, q2, p2] = w.polygon;
    assert.equal(q[2], q2[2]);
    assert.equal(p[2], p2[2]);
    // Each end sits at the road's own level (centre line), not at the ground under the slab's outer edge.
    assert.ok(Math.abs(p[2] - .015 - c.groundZ(p[0], LAT)) < .4);
    assert.ok(q[2] > p[2]);
  }
});
