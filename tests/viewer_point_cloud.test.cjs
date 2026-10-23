const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
test('road alignment translates only display Z once across visibility toggles',async()=>{
 const original=new Float64Array([35.51,33.89,10,35.52,33.90,12]);
 const nodes=new Map(),location={hash:'#lens=observed'};
 const context=vm.createContext({DATA:{},URLSearchParams,location,render:()=>{},attribution:()=>'',
  history:{replaceState:(_,__,hash)=>location.hash=hash},
  $:id=>{if(!nodes.has(id))nodes.set(id,{});return nodes.get(id)},
  fetch:async url=>({ok:true,arrayBuffer:async()=>url.endsWith('positions')?original.slice().buffer:new Uint8Array([1,2,3,4,5,6]).buffer})});
 vm.runInContext(html.slice(html.indexOf('const pointCloud ='),html.indexOf('function layers()')),context);
 const run=code=>vm.runInContext(code,context);
 run("pointCloud.metadata={point_count:2,display_z_offset:74,display_alignment:{translation_z_m:-7.3}}");
 await run('setPointCloudVisible(true)');
 const values=()=>JSON.parse(run('JSON.stringify(Array.from(pointCloud.data.attributes.getPosition.value))'));
 const aligned=values();
 assert.deepEqual([aligned[0],aligned[1],aligned[3],aligned[4]],[original[0],original[1],original[3],original[4]]);
 assert.ok(Math.abs(aligned[2]-2.7)<1e-12);
 assert.ok(Math.abs((aligned[5]-aligned[2])-2)<1e-12);
 await run('setPointCloudVisible(false)');await run('setPointCloudVisible(true)');
 assert.deepEqual(values(),aligned);
 assert.equal(original[2],10);
 assert.match(nodes.get('cloud-note').textContent,/Road-aligned for display/);
});
