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
  // A steady slope needs no added points: they would sit on the straight line between the ends.
  assert.equal(street.path.length, 2);
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
  assert.ok(walks.length >= 2 && curbs.length === walks.length);
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

test('streets keep added points only where the relief bends', () => {
  const c = scene();
  // A ridge: up 1 m per column to column 20, then down again.
  const grid = new Int16Array(COLS * ROWS);
  for (let r = 0; r < ROWS; r++) for (let col = 0; col < COLS; col++) grid[r * COLS + col] = (20 - Math.abs(col - 20)) * 100;
  c.setGrid(grid);
  c.DATA.traffic.roads = [{u: 1, v: 2, path: [[lonAt(2), LAT], [lonAt(38), LAT]]}];
  run(c, 'elevateRoads()');
  const path = c.DATA.traffic.roads[0].path;
  assert.ok(path.length >= 3 && path.length < 10, `${path.length} points`);
  for (const p of path) assert.ok(Math.abs(p[2] - c.groundZ(p[0], p[1])) < .15 + 1e-9);
  assert.ok(Math.max(...path.map((p) => p[2])) > 19);
});

test('a street spans a dip steeper than MAX_ROAD_GRADE, and a junction on it stays up for the next road', () => {
  const c = scene();
  // Flat at 10 m with a 10 m deep, one-column-wide trench (a river under an untagged viaduct) at column 20.
  const grid = new Int16Array(COLS * ROWS).fill(1000);
  for (let r = 0; r < ROWS; r++) for (const col of [19, 20, 21]) grid[r * COLS + col] = col === 20 ? 0 : 500;
  c.setGrid(grid);
  // The viaduct is split at a junction (node 2) right over the trench.
  c.DATA.traffic.roads = [{u: 1, v: 2, path: [[lonAt(5), LAT], [lonAt(20), LAT]]}, {u: 2, v: 3, path: [[lonAt(20), LAT], [lonAt(35), LAT]]}];
  run(c, 'elevateRoads()');
  const [a, b] = c.DATA.traffic.roads;
  const grade = (p, q) => Math.abs(q[2] - p[2]) / metresBetween(p, q);
  for (const road of [a, b]) {
    for (let i = 1; i < road.path.length; i++) assert.ok(grade(road.path[i - 1], road.path[i]) <= run(c, 'MAX_ROAD_GRADE') + 1e-9);
    for (const p of road.path) assert.ok(p[2] >= c.groundZ(p[0], p[1]) - 1e-9 && Math.abs(p[3] - (p[2] - c.groundZ(p[0], p[1]))) < 1e-9);
    assert.equal(road.lifted, true);
  }
  // Both roads meet at one level over the trench, well above its floor.
  assert.ok(Math.abs(a.path.at(-1)[2] - b.path[0][2]) < 1e-9);
  assert.ok(a.path.at(-1)[3] > 3);
  // Cars follow the lifted road, not the trench floor.
  assert.ok(Math.abs(c.roadHeightAt(1, lonAt(20), LAT) - b.path[0][2]) < 1e-6);
});

test('on terrain a bridge ramps from the level at its ends, not from the ground under a valley', () => {
  const c = scene();
  // Banks at 20 m, a 16-column valley floor at 0 m between them.
  const grid = new Int16Array(COLS * ROWS).fill(2000);
  for (let r = 0; r < ROWS; r++) for (let col = 12; col <= 28; col++) grid[r * COLS + col] = 0;
  c.setGrid(grid);
  c.DATA.traffic.roads = [{u: 1, v: 2, path: [[lonAt(2), LAT], [lonAt(8), LAT]]}, {u: 2, v: 3, lvl: 1, deck: 22, path: [[lonAt(8), LAT], [lonAt(32), LAT]]},
    {u: 3, v: 4, path: [[lonAt(32), LAT], [lonAt(38), LAT]]}];
  run(c, 'elevateRoads()');
  const bridge = c.DATA.traffic.roads[1].path;
  assert.ok(Math.abs(bridge[0][2] - 20) < 1e-9 && Math.abs(bridge.at(-1)[2] - 20) < 1e-9);
  for (let i = 1; i < bridge.length; i++) assert.ok(Math.abs(bridge[i][2] - bridge[i - 1][2]) / metresBetween(bridge[i - 1], bridge[i]) <= run(c, 'BRIDGE_TERRAIN_GRADE') + 1e-9);
  // Over the valley floor it stands high above the ground, so piers are drawn.
  assert.ok(Math.max(...bridge.map((p) => p[3])) > 18);
});
