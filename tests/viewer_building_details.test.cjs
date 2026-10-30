const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const source=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_building_details.js`,'utf8');
const centre=[35.5,33.9],sy=111320,sx=sy*Math.cos(centre[1]*Math.PI/180);
const ll=(x,y)=>[centre[0]+x/sx,centre[1]+y/sy];
const ring=[ll(-10,-10),ll(10,-10),ll(10,10),ll(-10,10),ll(-10,-10)];
const building=(extra={})=>({b:{id:'one',h:12,state:'occupied',...extra},ring});
function context(extra={}) {const c={elevation:b=>b.h,cameraOcclusion:()=>({model:new Set(),bbed:new Set()}),...extra};vm.createContext(c);vm.runInContext(source,c);return c;}
test('windows stay outside source facades and below the measured roof height',()=>{
 const c=context(),g=c.buildingDetailGeometry([building()],centre);assert.ok(g.windows.length>0);assert.equal(g.coping.length,4);
 for(const w of g.windows){const x=Math.abs((w.position[0]-centre[0])*sx),y=Math.abs((w.position[1]-centre[1])*sy);assert.ok(Math.max(x,y)>10.02);assert.ok(w.position[2]+w.scale[2]<12);assert.equal(w.illustrative,true);}
 for(const r of g.coping)assert.equal(r.position[2],12.015);
});
test('reverse winding keeps openings outside and missing buildings remain undecorated',()=>{
 const c=context(),item=building();item.ring=[...ring].reverse();const g=c.buildingDetailGeometry([item],centre);
 for(const w of g.windows)assert.ok(Math.max(Math.abs((w.position[0]-centre[0])*sx),Math.abs((w.position[1]-centre[1])*sy))>10.02);
 for(const state of ['empty_or_parking','demolished','destroyed'])assert.equal(c.buildingDetailGeometry([building({state})],centre).windows.length,0);
 assert.equal(c.buildingDetailGeometry([{...building(),ring:ring.map(p=>[p[0]+.1,p[1]])}],centre).windows.length,0);
});
test('analytical states and distant views hide detail; stable views reuse geometry',()=>{
 class Layer{constructor(p){Object.assign(this,p);}}
 const items=[building()],c=context({ui:{lens:'observed'},beirutOverlay:{active:false},map:{getZoom:()=>19,getCenter:()=>({toArray:()=>centre})},buildingsData:()=>items,metresBetween:()=>0,nightLevel:()=>0,isDark:()=>false,shade:x=>x,mesh:()=>({}),blockMesh:()=>({}),deck:{SimpleMeshLayer:Layer}});
 const first=c.buildingDetailLayers();assert.equal(first.length,2);assert.equal(c.buildingDetailLayers()[0].data,first[0].data);
 for(const key of ['selected','highlight','answerIds','scenario']){c.ui[key]={};assert.equal(c.buildingDetailLayers().length,0);c.ui[key]=null;}
 c.ui.lens='forecast';assert.equal(c.buildingDetailLayers().length,0);c.ui.lens='observed';c.map.getZoom=()=>17;assert.equal(c.buildingDetailLayers().length,0);
});

test('default Beirut model view decorates its own footprints and heights',()=>{
 class Layer{constructor(p){Object.assign(this,p);}}
 const c=context({ui:{lens:'observed'},beirutOverlay:{active:true,polygons:[{beirutModel:0,rings:[ring]}],data:{buildings:[{height_m:18,objectid:42}]},snapshotById:new Map([['42',{state:'occupied'}]])},map:{getZoom:()=>19,getCenter:()=>({toArray:()=>centre})},buildingsData:()=>{throw Error('must not decorate hidden BBED footprints');},metresBetween:()=>0,nightLevel:()=>0,isDark:()=>false,shade:x=>x,mesh:()=>({}),blockMesh:()=>({}),deck:{SimpleMeshLayer:Layer}});
 const layers=c.buildingDetailLayers();assert.ok(layers[0].data.length>0);
 assert.equal(layers[1].data[0].position[2],18.015);
 assert.equal(layers[0].data,c.buildingDetailLayers()[0].data);
 c.beirutOverlay.polygons=[{beirutModel:0,rings:[ring.map(p=>[p[0]+.1,p[1]])]}];
 assert.equal(c.buildingDetailLayers()[0].data.length,0);
});

test('the detail budget decorates foreground buildings before source-file order',()=>{
 const c=context(),far=Array.from({length:130},(_,i)=>({b:{id:`far-${i}`,h:12,state:'occupied'},ring:ring.map(p=>[p[0]+150/sx,p[1]])}));
 const g=c.buildingDetailGeometry([...far,building({id:'foreground'})],centre);
 assert.ok(g.windows.some(w=>w.buildingId==='foreground'));
 assert.equal(g.windows[0].buildingId,'foreground');
});
