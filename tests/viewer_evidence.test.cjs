// Run with node --test tests/viewer_evidence.test.cjs.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`, 'utf8');
function load(start, end, context) {
  vm.runInNewContext(html.slice(html.indexOf(start), html.indexOf(end)), context);
}
test('lens switch refreshes the evidence label', () => {
  const status = {}, ui = {lens:'observed', scenario:null};
  const context = {ui, DATA:{forecast:{evidence_level:'empirical_bau'}}, $:()=>status};
  load('function evidenceLabel(', 'function counts(', context);
  context.renderStatus(); assert.match(status.innerHTML, /Observed records/);
  Object.assign(context, {URLSearchParams, location:{hash:''}, history:{replaceState(){}}, document:{querySelectorAll:()=>[]},
    renderLegend(){}, renderDock(){}, render(){}});
  load('function setLens(', 'function togglePanel(', context);
  context.setLens('forecast'); assert.match(status.innerHTML, /Model prediction · BAU/);
  ui.scenario={forecast:{evidence_level:'assumption_based_scenario'}};
  context.renderStatus(); assert.match(status.innerHTML, /Assumption-based prediction/);
  assert.match(context.bandNote({}), /90% simulation range/);
  assert.doesNotMatch(context.bandNote({}), /draws/);
});
test('traffic impact counts carry the uncalibrated qualifier', () => {
  const context={ui:{traffic:false},REASON:{},hourLabel:x=>x,plural:(n,s)=>`${n} ${s}`};
  load('function roadSummary(', '// Peak-hour counts', context);
  const result=context.roadSummary({closed_by:{},closed_roads:0,affected:0,trips:100,hour:8});
  assert.match(result,/Illustrative traffic impact · uncalibrated rules/);
  assert.match(result,/100 sampled trips/);
});
