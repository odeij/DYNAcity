// Run with node --test tests/viewer_usability.test.cjs.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`, 'utf8');
function fixture() {
  const fields = Object.fromEntries(['q','scenario-name','library-message','progress','saved-inputs','json-export','json-export-text','t-library','t-buildings','building-query','save-input','export-input','import-input','scenario-file'].map(id => [id,{value:'',textContent:'',innerHTML:'',focus(){this.focused=true;}}]));
  const ui = {mode:'scenario',busy:false,hour:8,scenario:{forecast:{old:true}}};
  const data = {snapshot:{id:'survey',data_version:'v1'},forecast:{model_version:'model1'}};
  let store = null, failWrite = false;
  const context = vm.createContext({DATA:data,ui,Date,JSON,Number,Object,Array,String,Error,
    $: id=>fields[id], esc:s=>String(s),setMode:m=>{ui.mode=m;},
    Blob, URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},setTimeout:()=>{},
    document:{addEventListener:()=>{},body:{append:()=>{}},createElement:()=>({click(){},remove(){}})},
    localStorage:{getItem:()=>store,setItem:(key,v)=>{if(failWrite)throw new Error('quota exceeded');store=v;}}});
  vm.runInContext(html.slice(html.indexOf('// Scenario input files'),html.indexOf('function wire()')),context);
  return {fields,ui,data,run:s=>vm.runInContext(s,context),setStore:v=>{store=v;},fail:()=>{failWrite=true;}};
}
const valid = () => ({format:'dynacity-scenario-input',schema_version:1,name:'Renovation',saved_at:'2026-10-08T09:00:00.000Z',
  context:{snapshot_id:'survey',data_version:'v1',model_version:'model1'},input:{text:'Renovate stalled buildings',hour:8}});
test('portable inputs round-trip and reject unsupported, oversized and malformed schemas',()=>{
  const f=fixture();
  for(const mutate of [v=>v.schema_version=2,v=>v.forecast={},v=>v.input.hour=24,v=>v.input.text=' ',v=>v.input.text='a'.repeat(4001),v=>v.context.data_version=9,v=>v.context.extra=true,v=>v.saved_at='nonsense']) {
    const v=valid(); mutate(v);assert.throws(()=>f.run(`validateScenarioInput(${JSON.stringify(v)})`),/Invalid scenario/);
  }
  assert.equal(f.run(`validateScenarioInput(${JSON.stringify(valid())}).input.text`),valid().input.text);
});
test('loading restores only input and warns about version drift without replacing results or changing traffic',()=>{
  const f=fixture();const v=valid();v.context.data_version='older';v.input.hour=20;
  f.run(`loadScenarioInput(${JSON.stringify(v)})`);
  assert.equal(f.fields.q.value,v.input.text);
  assert.equal(f.ui.hour,8);
  assert.deepEqual(f.ui.scenario,{forecast:{old:true}});
  assert.match(f.fields['library-message'].textContent,/Version context differs \(data_version\)/);
  assert.match(f.fields.progress.textContent,/no saved results restored/);
  assert(f.fields.q.focused);
});
test('unknown model versions are never presented as reproducible',()=>{
  const f=fixture();f.data.forecast=null;const v=valid();v.context.model_version=null;
  assert.match(f.run(`inputCompatibility(${JSON.stringify(v)}).join(',')`),/unknown model/);
});
test('load refuses to change an in-flight request',()=>{
  const f=fixture();f.ui.busy=true;
  assert.throws(()=>f.run(`loadScenarioInput(${JSON.stringify(valid())})`),/Wait/);
  assert.equal(f.fields.q.value,'');
});
test('saved library survives reload; storage failures never report a successful write',()=>{
  const f=fixture();f.fields.q.value='Renovate';f.run('writeSavedInputs([makeScenarioInput()]);savedInputs=[];readSavedInputs()');
  assert.equal(f.run('savedInputs.length'),1);
  f.fail();assert.throws(()=>f.run('writeSavedInputs([])'),/quota/);
  assert.equal(f.run('savedInputs.length'),1);
});
test('malformed saved libraries cannot inject rendered content',()=>{
  const f=fixture();f.setStore(JSON.stringify([{name:'<script>alert(1)</script>'}]));f.run('readSavedInputs()');
  assert.equal(f.run('savedInputs.length'),0);
  assert.match(f.fields['library-message'].textContent,/could not be read/);
  assert.throws(()=>f.run('writeSavedInputs([])'),/preserved/);
});

test('export remains portable when a browser blocks the download',()=>{
  const f=fixture();f.run(`downloadScenarioInput(${JSON.stringify(valid())})`);
  const exported=JSON.parse(f.fields['json-export-text'].value);
  assert.deepEqual(exported,valid());
  assert.equal(f.fields['json-export'].hidden,false);
  assert.equal(f.fields['json-export'].open,true);
  assert.equal(f.run(`validateScenarioInput(${JSON.stringify(exported)}).format`),'dynacity-scenario-input');
});

test('file import rejects malformed or oversized files without changing the prompt',async()=>{
  const f=fixture();f.fields.q.value='Keep current input';f.run('wireUsability()');
  for (const file of [{size:32001,text:async()=>JSON.stringify(valid())},{size:5,text:async()=>'{bad'}]) {
    const target={files:[file],value:'selected.json'};
    await f.fields['scenario-file'].onchange({target});
    assert.equal(f.fields.q.value,'Keep current input');
    assert.match(f.fields['library-message'].textContent,/Could not import/);
    assert.equal(target.value,'');
  }
  const target={files:[{size:1000,text:async()=>JSON.stringify(valid())}],value:'input.json'};
  await f.fields['scenario-file'].onchange({target});
  assert.equal(f.fields.q.value,valid().input.text);
});
