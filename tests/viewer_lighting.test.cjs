const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
function scene(){
 const calls=[];
 class Light { constructor(props){Object.assign(this,props);} }
 const ctx={ui:{traffic:true,hour:8},mix:(a,b,t)=>a.map((v,i)=>v+(b[i]-v)*t),nightLevel:()=>0,isDark:()=>false,
  deck:{AmbientLight:Light,DirectionalLight:Light,LightingEffect:Light},deckgl:{setProps:p=>calls.push(p)},map:null};
 vm.runInNewContext(html.slice(html.indexOf('function daylightSettings('),html.indexOf('const MATERIAL =')),ctx);
 return {ctx,calls};
}
test('daylight moves east to west while all rays stay downward',()=>{
 const {ctx}=scene(),morning=ctx.daylightSettings(8),noon=ctx.daylightSettings(12),evening=ctx.daylightSettings(16);
 assert.ok(morning.direction[0]<0);assert.ok(evening.direction[0]>0);
 assert.ok(Math.abs(noon.direction[0])<1e-9);
 for(const s of [morning,noon,evening])assert.ok(s.direction[2]<0);
 assert.ok(noon.direction[2]<morning.direction[2]);
 assert.ok(morning.colour[2]<noon.colour[2]);
});
test('daytime hour changes refresh lighting once, animation frames reuse it',()=>{
 const {ctx,calls}=scene();ctx.applyNight();ctx.applyNight();assert.equal(calls.length,1);
 ctx.ui.hour=16;ctx.applyNight();assert.equal(calls.length,2);
 assert.ok(calls[0].effects[0].sun.direction[0]<0);assert.ok(calls[1].effects[0].sun.direction[0]>0);
});
test('night disables sun and static map uses neutral midday illumination',()=>{
 const {ctx}=scene();assert.equal(ctx.lighting(1).sun.intensity,0);
 ctx.ui.traffic=false;ctx.ui.hour=3;
 assert.ok(Math.abs(ctx.lighting().sun.direction[0])<1e-9);
});
test('sun shadows cast from buildings only onto the ground, and fade out at night',()=>{
 const {ctx}=scene();ctx.ui.shadows=true;ctx.nightLevel=()=>0;
 assert.ok(ctx.shadowsOn());assert.ok(ctx.lighting(0).sun._shadow);
 assert.equal(ctx.lighting(0).shadowColor[3],0.5);assert.equal(ctx.lighting(1).shadowColor[3],0);
 ctx.nightLevel=()=>1;assert.ok(!ctx.shadowsOn());
 ctx.nightLevel=()=>0;ctx.ui.shadows=false;assert.ok(!ctx.shadowsOn());
 for(const id of ['buildings','beirut-001-buildings','osm-gap-buildings','bridge-deck-slabs','mapped-tree-canopies'])assert.ok(vm.runInContext('SHADOW_CASTERS',ctx).test(id),id);
 assert.ok(!vm.runInContext('SHADOW_CASTERS',ctx).test('shadow-ground'));assert.ok(vm.runInContext('SHADOW_RECEIVERS',ctx).test('shadow-ground'));assert.ok(!vm.runInContext('SHADOW_RECEIVERS',ctx).test('buildings'));
});
