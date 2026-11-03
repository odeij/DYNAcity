// Projection regression fixtures independently generated with PROJ / pyproj EPSG:32636.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const geo=require('../engine/unreal/Content/UI/city-map-coordinates.js');
const close=(a,b,tolerance=1e-6)=>assert.ok(Math.abs(a-b)<tolerance,`${a} != ${b}`);
const fixtures=[
  [[33,0],[500000,0]],
  [[35.5,33.9],[731167.7004159444,3753882.2119486625]],
  [[35.513,33.895],[732383.6397323518,3753356.9840075364]],
  [[30,40],[243900.35202972143,4432069.056898517]],
  [[36,80],[558132.215132799,8883084.955948303]]
];
test('UTM36N forward/inverse agree with PROJ to sub-millimetre precision',()=>{
  for(const [lonlat,utm] of fixtures){
    geo.forward(lonlat).forEach((n,i)=>close(n,utm[i],.0001));
    geo.inverse(utm).forEach((n,i)=>close(n,lonlat[i],1e-9));
  }
});
test('local east/north conversion preserves axes, nonzero origin and ignores height',()=>{
  const origin=fixtures[1][1];
  const ref=geo.reference({origin_utm_m:[...origin,123],map_reference:{crs:'EPSG:32636',origin_lonlat:[35.5,33.9]}});
  for(const point of [[0,0,0],[-5000,3500,25],[1000,-900,100]]){
    const [lat,lng]=ref.localToLatLng(point), local=ref.latLngToLocal({lat,lng});
    close(local.x,point[0],.0001);close(local.y,point[1],.0001);
  }
  assert.ok(ref.localToLatLng([1000,0])[1]>35.5);
  assert.ok(ref.localToLatLng([0,1000])[0]>33.9);
});
test('reference fallback is projected rather than degrees-per-metre approximation',()=>{
  const ref=geo.reference({map_reference:{crs:'EPSG:32636',origin_lonlat:[35.5,33.9]}});
  const [lat,lng]=ref.localToLatLng([0,0]);close(lat,33.9,1e-9);close(lng,35.5,1e-9);
});
test('invalid references and out-of-zone destinations fail safely',()=>{
  for(const data of [{},{map_reference:{crs:'EPSG:3857'}},{map_reference:{crs:'EPSG:32636',origin_lonlat:[null,33]}}])assert.throws(()=>geo.reference(data));
  assert.throws(()=>geo.forward([37,34]));assert.throws(()=>geo.forward([35,-1]));
  assert.throws(()=>geo.inverse([NaN,0]));
});
test('vendored map assets match pinned download hashes',()=>{
  const root=path.resolve(__dirname,'../engine/unreal/Content/UI/vendor');
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'map-sources.json')));
  assert.equal(manifest.leaflet_version,'1.9.4');
  for(const file of manifest.files)assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(root,file.file))).digest('hex'),file.sha256,file.file);
});
