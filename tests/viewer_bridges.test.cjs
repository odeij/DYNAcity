// Run with node --test tests/viewer_bridges.test.cjs.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`, 'utf8');
function load(start, end, context) {
  vm.runInNewContext(html.slice(html.indexOf(start), html.indexOf(end)), context);
}
const LAT = 33.9, M = 1 / 111320;
const mPerLon = (lat) => 111320 * Math.cos((lat * Math.PI) / 180);
const metresBetween = (a, b) => Math.hypot((b[0] - a[0]) * mPerLon(a[1]), (b[1] - a[1]) * 111320);
// Ground road 0 → bridge 1 → bridge 2 → ground road 3 along a north-running line,
// plus a tunnel. Node 3 joins only bridges, so the deck stays up there.
const at = (metres) => [35.5, LAT + metres * M];
function roads() {
  return [
    {u: 1, v: 2, path: [at(-100), at(0)]},
    {u: 2, v: 3, lvl: 1, path: [at(0), at(200)]},
    {u: 3, v: 4, lvl: 1, path: [at(200), at(400)]},
    {u: 4, v: 5, path: [at(400), at(500)]},
    {u: 6, v: 7, lvl: -1, path: [at(600), at(700)]},
  ];
}
function scene() {
  const context = {DATA: {traffic: {roads: roads()}}, metresBetween};
  load('// ---- bridges', 'function roadMarkings(', context);
  load('// Height of road `i`', 'function simulationSummary(', context);
  return context;
}

test('bridges ramp up from the ground and stay level where only bridges meet', () => {
  const {DATA} = scene(), [ground, first, second, after, tunnel] = DATA.traffic.roads;
  const z = (road) => road.path.map((p) => p[2]);
  // Densified, so a straight two-point span can still ramp.
  assert.ok(first.path.length > 20);
  assert.equal(z(first)[0], 0);
  assert.equal(z(first).at(-1), 6);
  assert.ok(Math.abs(z(first)[5] - 6 * metresBetween(first.path[0], first.path[5]) / 40) < 1e-9);
  assert.deepEqual(new Set(z(second).slice(0, 20)), new Set([6]));
  assert.equal(z(second).at(-1), 0);
  // Ground roads and tunnels keep their flat paths.
  assert.equal(ground.path[0].length, 2);
  assert.equal(tunnel.path.length, 2);
  assert.equal(after.path[1].length, 2);
});

test('cars ride on bridges, keep their height through junctions and vanish in tunnels', () => {
  const context = scene();
  const car = (i, metres) => ({i, pose: {position: [...at(metres), .04]}});
  const onDeck = car(1, 150);
  context.carPose(onDeck);
  assert.equal(onDeck.pose.position[2], 6.04);
  // Inside a junction SUMO reports road 0; far from road 0 the height is kept.
  onDeck.i = 0;
  onDeck.pose.position[1] = at(210)[1];
  context.carPose(onDeck);
  assert.equal(onDeck.pose.position[2], 6.04);
  const street = car(3, 450);
  context.carPose(street);
  assert.equal(street.pose.position[2], .04);
  assert.equal(context.inTunnel(car(4, 650)), true);
  assert.equal(context.inTunnel(street), false);
});
