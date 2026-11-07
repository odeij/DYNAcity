// Run with node --test tests/viewer_heights.test.cjs.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`, 'utf8');
function load(context) {
  vm.runInNewContext(html.slice(html.indexOf('const HEIGHT_TINT'), html.indexOf('function elevation(')), context);
}
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
test('recorded heights keep their colour; estimates are lighter and more transparent', () => {
  const context = {mix}; load(context);
  const base = [100, 100, 100, 255];
  assert.deepEqual(context.heightConfidence({height_kind: 'recorded'}, base), base);
  assert.deepEqual(context.heightConfidence(undefined, base), base);
  const floors = context.heightConfidence({height_kind: 'floors'}, base), bin = context.heightConfidence({height_kind: 'size_bin'}, base);
  assert.ok(floors[0] > 100 && floors[3] < 255 && bin[0] > floors[0] && bin[3] < floors[3]);
  assert.equal(context.heightConfidence({height_kind: 'fallback'}, [1, 2, 3, 70])[3] < 70, true);
});
test('legend buckets: recorded, from floors, everything else estimated', () => {
  const context = {mix}; load(context);
  const c = context.heightKindCounts([{height_kind: 'recorded'}, {height_kind: 'floors'}, {height_kind: 'size_bin'}, {height_kind: 'fallback'}, {}]);
  assert.deepEqual({...c}, {recorded: 1, floors: 1, estimated: 3});
});
