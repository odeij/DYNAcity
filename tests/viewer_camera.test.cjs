require('./viewer_ground_helper.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_camera.js`,'utf8');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
function scene() {
 const state={zoom:19,pitch:35,bearing:0,centre:[35.5,33.9]},c={state,DATA:{buildings:[]},beirutOverlay:{active:false},
 map:{getZoom:()=>state.zoom,getPitch:()=>state.pitch,getBearing:()=>state.bearing,getCenter:()=>({toArray:()=>state.centre}),getCanvas:()=>({clientHeight:600})},
 osmContextEntries:()=>[],performance:{now:()=>0},requestAnimationFrame:f=>{c.queued=f;return 1;},layers:()=>[state.zoom],baseLayers:[],drawFx:()=>{c.draws=(c.draws??0)+1;},
 elevation:b=>b.h,ringBox:r=>[Math.min(...r.map(p=>p[0])),Math.min(...r.map(p=>p[1])),Math.max(...r.map(p=>p[0])),Math.max(...r.map(p=>p[1]))],cellKeys:()=>['cell']};
 vm.createContext(c);vm.runInContext(source,c);
 vm.runInContext(html.slice(html.indexOf('function inRing('),html.indexOf('// deck.gl hands MVT')),c);
 return c;
}
test('camera movement refreshes zoom-dependent geometry without rebuilding on every animation frame',()=>{
 const c=scene();c.refreshCameraGeometry(0);assert.equal(c.baseLayers[0],19);
 c.state.zoom=14;c.refreshCameraGeometry(20);assert.equal(c.baseLayers[0],19,'bounded geometry work during a gesture');
 c.refreshCameraGeometry(100);assert.equal(c.baseLayers[0],14);
 const same=c.baseLayers;c.refreshCameraGeometry(200);assert.equal(c.baseLayers,same);
 c.state.zoom=18;c.refreshCameraGeometry(205,true);assert.equal(c.baseLayers[0],18,'move-end applies the final view immediately');
 c.cameraChanged();c.cameraChanged();assert.equal(c.draws,undefined);c.queued();assert.equal(c.draws,1);
});
test('a building surrounding the camera fades without changing its mapped height',()=>{
 const c=scene();c.state.zoom=20;c.state.pitch=75;
 const eye=c.estimatedCameraEye(c.currentSceneCamera()),ring=[[-.0001,-.0001],[.0001,-.0001],[.0001,.0001],[-.0001,.0001],[-.0001,-.0001]].map(p=>[eye[0]+p[0],eye[1]+p[1]]);
 c.beirutOverlay={active:true,polygons:[{beirutModel:0,rings:[ring]}],data:{buildings:[{height_m:100}]}};
 const inside=c.cameraOcclusion();assert.equal(inside.model.has(0),true);
 assert.deepEqual(Array.from(c.cameraSafeColour([210,211,212,255],'model',0,inside)),[210,211,212,35]);
 assert.equal(c.beirutOverlay.data.buildings[0].height_m,100);
 c.state.pitch=0;assert.equal(c.cameraOcclusion().model.size,0,'camera above the roof preserves full opacity');
 c.state.zoom=16;assert.equal(c.cameraOcclusion().model.size,0,'overview never fades city geometry');
});
test('courtyards do not count as being inside a building',()=>{
 const c=scene();c.state.zoom=20;c.state.pitch=75;const eye=c.estimatedCameraEye(c.currentSceneCamera());
 const ring=r=>[[-r,-r],[r,-r],[r,r],[-r,r],[-r,-r]].map(p=>[eye[0]+p[0],eye[1]+p[1]]);
 c.beirutOverlay={active:true,polygons:[{beirutModel:0,rings:[ring(.003),ring(.002)]}],data:{buildings:[{height_m:100}]}};
 assert.equal(c.cameraOcclusion().model.size,0);
});
test('tall foreground structures fade when they block the camera ray, low roofs stay visible',()=>{
 const c=scene(),ring=[[35.4999,33.9003],[35.5001,33.9003],[35.5001,33.9006],[35.4999,33.9006],[35.4999,33.9003]];
 assert.equal(c.buildingBlocksCamera([ring],40,[35.5,33.9,30],[35.5,33.901]),true);
 assert.equal(c.buildingBlocksCamera([ring.slice(0,-1)],40,[35.5,33.9,30],[35.5,33.901]),true,'source model rings omit the repeated closing vertex');
 assert.equal(c.buildingBlocksCamera([ring],2,[35.5,33.9,30],[35.5,33.901]),false);
});
