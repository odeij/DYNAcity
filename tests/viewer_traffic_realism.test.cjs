const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_template.html`,'utf8');
const latitude=33.9,origin=[35.5,latitude],sx=111320*Math.cos(latitude*Math.PI/180);
const point=(x,z=0)=>[origin[0]+x/sx,latitude,z];
function scene() {
 const context=vm.createContext({trafficNetwork:{vehicle_types:{car:{length_m:4.6}}},DATA:{traffic:{roads:[
  {path:[point(-100),point(-50)]},
  {lvl:1,path:[point(0),point(100,10)]},
  {path:[point(100),point(200)]},
 ]}}});
 vm.runInContext(html.slice(html.indexOf('// Height of road `i`'),html.indexOf('function simulationSummary()')),context);
 vm.runInContext(html.slice(html.indexOf('function vehicleMeshPosition('),html.indexOf('function setMixedTraffic(')),context);
 return context;
}
const car=(i,x,heading=0)=>({i,pose:{position:point(x,.04),orientation:[0,heading,0]}});
test('opposing vehicles align to the same bridge surface with opposite pitch',()=>{
 const s=scene(),uphill=car(1,40),downhill=car(1,40,180);
 s.carPose(uphill);s.carPose(downhill);
 const expected=Math.atan(.1)*180/Math.PI;
 assert.ok(Math.abs(uphill.pose.orientation[0]+expected)<1e-8);
 assert.ok(Math.abs(downhill.pose.orientation[0]-expected)<1e-8);
 assert.ok(Math.abs(uphill.pose.position[2]-4.04)<1e-8);
 assert.equal(uphill.pose.position[0],point(40)[0]);
 assert.equal(uphill.pose.position[1],latitude);
});
test('road grade and braking pitch do not accumulate across repeated draws',()=>{
 const s=scene(),c=car(1,40);c.bodyPitch=1.5;
 s.carPose(c);const first=c.pose.orientation[0];
 for(let i=0;i<100;i++)s.carPose(c);
 assert.equal(c.pose.orientation[0],first);
 c.bodyPitch=.25;s.carPose(c);
 assert.ok(Math.abs(c.pose.orientation[0]-(first-1.25))<1e-10);
});
test('junctions retain grade only until the next ground road',()=>{
 const s=scene(),c=car(1,40);s.carPose(c);const grade=c.pose.orientation[0];
 c.i=0;c.pose.position[0]=point(101)[0];s.carPose(c);
 assert.equal(c.pose.orientation[0],grade);
 c.i=2;s.carPose(c);
 assert.equal(c.pose.orientation[0],0);
 assert.equal(c.pose.position[2],.04);
});
test('grade projection rejects distant roads and ignores cross-slope headings',()=>{
 const s=scene(),c=car(1,40,90);s.carPose(c);
 assert.ok(Math.abs(c.pose.orientation[0])<1e-10);
 c.pose.position[1]+=.001;
 assert.equal(s.vehicleRoadPitch(c),null);
});
test('mesh centre follows the road plane behind the authoritative front bumper',()=>{
 const s=scene(),c=car(1,40);c.type='car';c.bodyPitch=1.5;s.carPose(c);
 const front=Array.from(c.pose.position),centre=s.vehicleMeshPosition(c);
 assert.ok(Math.abs((front[0]-centre[0])*sx-2.3)<1e-8);
 assert.ok(Math.abs(centre[2]-(front[2]-.23))<1e-8);
 assert.deepEqual(Array.from(c.pose.position),front,'rendering never mutates recorded front position');
 c.bodyPitch=-2;s.carPose(c);
 assert.equal(s.vehicleMeshPosition(c)[2],centre[2],'suspension pitch does not lift the mesh centre');
 c.pose.orientation[1]=180;s.carPose(c);
 const downhill=s.vehicleMeshPosition(c);
 assert.ok(Math.abs(downhill[2]-(front[2]+.23))<1e-8);
});
