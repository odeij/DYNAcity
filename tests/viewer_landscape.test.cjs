require('./viewer_ground_helper.cjs');
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
const osm=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_osm_context.js`,'utf8');
// landscapeLayers culls trees with the shared view-cell helper.
const viewCells=osm.slice(osm.indexOf('const VIEW_CELL_DEG'),osm.indexOf('let osmContextCache'));
test('landscape renders mapped surfaces and only mapped tree points, with satellite suppression',()=>{
 class Layer {constructor(props){Object.assign(this,props);}}
 const tree={geometry:{type:'Point',coordinates:[35.5,33.9]},properties:{kind:'tree',height_m:12}};
 const park={geometry:{type:'Polygon',coordinates:[[[35.5,33.9],[35.51,33.9],[35.5,33.91],[35.5,33.9]]]},properties:{kind:'park'}};
 const context=vm.createContext({DATA:{landscape:{features:[tree,park]}},ui:{satellite:false},beirutOverlay:{active:false},
  map:{getZoom:()=>18,getCenter:()=>({toArray:()=>[35.5,33.9]})},nightLevel:()=>0,shade:x=>x,
  metresBetween:()=>0,mesh:()=>({}),mappedTreeMesh:()=>{},MATERIAL:{},SOFT:{depthWriteEnabled:false},deck:{SimpleMeshLayer:Layer,SolidPolygonLayer:Layer,PathLayer:Layer}});
 vm.runInContext(viewCells,context);vm.runInContext(html.slice(html.indexOf('function landscapeLayers()'),html.indexOf('function mobilityLayers()')),context);
 const layers=()=>vm.runInContext('landscapeLayers()',context);
 assert.equal(layers()[0].data.length,1);
 assert.equal(layers()[0].parameters.depthWriteEnabled,false,'overlapping mapped land-use surfaces do not fight for depth');
 context.beirutOverlay.active=true;assert.equal(layers()[0].data.length,1,'default source-building mode preserves mapped landscape');
 const trees=layers().find(l=>l.id==='mapped-tree-canopies');
 assert.equal(trees.data.length,1);
 assert.deepEqual(Array.from(trees.getScale(tree)),[2,2,2]);
 assert.equal(layers().find(l=>l.id==='mapped-surface-boundaries').pickable,false);
 const tagged={...park,properties:{kind:'park',surface:'asphalt'}};
 assert.deepEqual(Array.from(layers()[0].getFillColor(tagged)),[126,131,130,195]);
 context.DATA.landscape.features=[park];
 assert.equal(layers().filter(l=>l.id==='mapped-tree-canopies').length,0); // A park polygon does not create tree locations.
 context.ui.satellite=true;assert.equal(layers().length,0);
});

test('all mapped trees in view survive dense areas and remain visible at overview zoom',()=>{
 class Layer {constructor(props){Object.assign(this,props);}}
 const features=Array.from({length:700},(_,i)=>({geometry:{type:'Point',coordinates:[35.5,33.9]},properties:{kind:'tree',id:`node/${i}`,height_m:6}}));
 const context=vm.createContext({DATA:{landscape:{features}},ui:{satellite:false},map:{getZoom:()=>18,getCenter:()=>({toArray:()=>[35.5,33.9]}),getBounds:()=>({toArray:()=>[[35.49,33.89],[35.51,33.91]]})},nightLevel:()=>0,shade:x=>x,metresBetween:()=>0,mesh:()=>({}),mappedTreeMesh:()=>{},SOFT:{depthWriteEnabled:false},deck:{SimpleMeshLayer:Layer,SolidPolygonLayer:Layer,PathLayer:Layer,ScatterplotLayer:Layer}});
 vm.runInContext(viewCells,context);vm.runInContext(html.slice(html.indexOf('function landscapeLayers()'),html.indexOf('function mobilityLayers()')),context);
 assert.equal(context.landscapeLayers().find(l=>l.id==='mapped-tree-canopies').data.length,700);
 context.map.getZoom=()=>14;
 assert.equal(context.landscapeLayers().find(l=>l.id==='mapped-tree-overview').data.length,700);
});

test('generic tree crown has round trunk, multiple lobes, exact fallback height and finite normals',()=>{
 const context=vm.createContext({Float32Array,Math});
 vm.runInContext(html.slice(html.indexOf('function meshOf('),html.indexOf('// A square-section taper')),context);
 vm.runInContext(html.slice(html.indexOf('function mappedTreeMesh()'),html.indexOf('function signalHeadMesh(')),context);
 const shape=vm.runInContext('mappedTreeMesh()',context),positions=Array.from(shape.positions.value),normals=Array.from(shape.normals.value);
 const zs=positions.filter((_,i)=>i%3===2);
 assert.equal(Math.min(...zs),0);assert.equal(Math.max(...zs),6);
 assert.ok(positions.every(Number.isFinite));assert.ok(normals.every(Number.isFinite));
 for(let i=0;i<normals.length;i+=3)assert.ok(Math.abs(Math.hypot(...normals.slice(i,i+3))-1)<1e-5);
 assert.ok(shape.positions.value.length/3<1000,'one small instanced mesh bounds GPU cost');
});
