const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(`${__dirname}/../src/dynacity/viewer_night.js`,'utf8');
function scene(appliedNight=1){
 class LayerExtension {}
 const c={appliedNight,deck:{LayerExtension}};
 vm.createContext(c);vm.runInContext(source+';this.api={NIGHT_DARK_STATES,nightSeed,nightInfo,NIGHT_WINDOW_MODULE,NightWindows,nightWindowProps};',c);
 return c;
}
test('recorded vacant, demolished, empty-lot and stalled buildings are never lit-capable',()=>{
 const {api}=scene();
 for(const s of ['vacant_or_evicted','demolished','empty_or_parking','stalled_or_cancelled'])assert.equal(api.nightInfo(s,'b1')[1],0,s);
 for(const s of ['stable_built','renovated','active_construction','unknown'])assert.equal(api.nightInfo(s,'b1')[1],1,s);
});
test('window seed is deterministic per building id and spread over [0,1)',()=>{
 const {api}=scene();
 assert.equal(api.nightSeed('m17'),api.nightSeed('m17'));
 const seeds=Array.from({length:500},(_,i)=>api.nightSeed(`m${i}`));
 assert.ok(seeds.every(v=>v>=0&&v<1));
 assert.ok(new Set(seeds).size>490);
 assert.ok(Math.min(...seeds)<.1&&Math.max(...seeds)>.9);
});
test('extension injects wall windows through the verified deck.gl 9.4 hooks and leaves roofs alone',()=>{
 const {api}=scene(),inj=api.NIGHT_WINDOW_MODULE.inject;
 assert.deepEqual(Object.keys(inj).sort(),['fs:#decl','fs:DECKGL_FILTER_COLOR','vs:#decl','vs:#main-end']);
 assert.match(inj['vs:#main-end'],/#ifdef IS_SIDE_VERTEX/);
 assert.match(inj['fs:DECKGL_FILTER_COLOR'],/nightWin\.level > 0\.0 && vNwB\.w > 0\.5/);
 assert.match(inj['fs:DECKGL_FILTER_COLOR'],/3\.6/);assert.match(inj['fs:DECKGL_FILTER_COLOR'],/3\.0/);
 assert.equal(api.NightWindows.defaultProps.nightLevel.value,0);
});
test('layer props carry the night level and daytime level is zero',()=>{
 const night=scene(.7).api.nightWindowProps(()=>[0,1]),day=scene(0).api.nightWindowProps(()=>[0,1]);
 assert.equal(night.nightLevel,.7);assert.equal(day.nightLevel,0);assert.equal(night.extensions.length,1);
});
