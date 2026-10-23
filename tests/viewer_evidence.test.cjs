// Run with node --test tests/viewer_evidence.test.cjs.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`, 'utf8');
function load(start, end, context) {
  vm.runInNewContext(html.slice(html.indexOf(start), html.indexOf(end)), context);
}
test('lens switch refreshes the evidence label and illustrative overlay warning', () => {
  const status = {}, ui = {lens:'observed', scenario:null};
  const context = {ui, DATA:{forecast:{evidence_level:'empirical_bau'}}, $:()=>status,
    hasHazards:()=>!!ui.scenario?.forecast.hazard_impacts.length};
  load('function evidenceLabel(', 'function counts(', context);
  context.renderStatus(); assert.match(status.innerHTML, /Observed records/);
  Object.assign(context, {URLSearchParams, location:{hash:''}, history:{replaceState(){}}, document:{querySelectorAll:()=>[]},
    renderLegend(){}, renderDock(){}, render(){}});
  load('function setLens(', 'function togglePanel(', context);
  context.setLens('forecast'); assert.match(status.innerHTML, /Model prediction · BAU/);
  ui.scenario={forecast:{evidence_level:'assumption_based_scenario',hazard_impacts:[{}]}};
  context.renderStatus(); assert.match(status.innerHTML, /Assumption-based prediction · illustrative effects/);
  assert.match(context.bandNote({}), /90% simulation range/);
  assert.doesNotMatch(context.bandNote({}), /draws/);
});
test('hazard counts are labelled illustrative beside the outcome', () => {
  const context={roadCard:()=>'', hazardIcon:()=>'',esc:x=>x,hazardLabel:x=>x,
    metres:x=>x, waterNote:()=>''};
  load('function hazardCards(', 'function renderScenario(', context);
  const band={p05:1,p50:2,p95:3};
  const result=context.hazardCards({forecast:{hazard_impacts:[{hazard_id:'a',kind:'earthquake',
    magnitude:6,magnitude_unit:'Mw',destroyed:band,damaged:band,exposed_buildings:10,
    year_offset:2,effect_source:'illustrative',confidence_grade:'low'}]}});
  assert.match(result,/Illustrative estimate · 2 destroyed/);
  assert.match(result,/declared confidence low/);
});
test('traffic impact counts carry the uncalibrated qualifier', () => {
  const context={ui:{traffic:false},REASON:{},hourLabel:x=>x,plural:(n,s)=>`${n} ${s}`};
  load('function roadSummary(', '// Peak-hour counts', context);
  const result=context.roadSummary({closed_by:{},closed_roads:0,affected:0,trips:100,hour:8});
  assert.match(result,/Illustrative traffic impact · uncalibrated rules/);
  assert.match(result,/100 sampled trips/);
});
