// Component/browser verification. Native Unreal integration is checked separately.
// --live-tiles exercises EOX over the network; default uses deterministic tile responses.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
(async()=>{
  const output=path.resolve(process.argv[2]);fs.mkdirSync(output,{recursive:true});
  const live=process.argv.includes('--live-tiles');
  const browser=await chromium.launch({headless:true,executablePath:process.env.DYNACITY_CHROMIUM||undefined});
  try {
    const page=await browser.newPage({viewport:{width:1100,height:900}});
    const errors=[],securityErrors=[],requests=[];
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.text().includes('Content Security Policy'))securityErrors.push(m.text());});
    page.on('request',r=>{if(r.url().startsWith('https:'))requests.push(r.url());});
    const tilePattern='https://tiles.maps.eox.at/**';
    const fixture=route=>route.fulfill({contentType:'image/svg+xml',headers:{'cache-control':'no-store'},body:'<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#ced9cc"/><path d="M0 80H256M70 0V256" stroke="#fff" stroke-width="8"/></svg>'});
    if(!live)await page.route(tilePattern,fixture);
    await page.addInitScript(()=>{
      window.actions=[];
      window.testState={playing:false,results:[],map_settings_error:'',map_settings:JSON.parse(sessionStorage.getItem('map-settings')||'{"version":1,"mode":"street","heading_up":false}'),map_position:[300,250,0],coverage:{source_terrain_municipal_percent:97.8,
        municipal_network_length_percent:87.2,exported_road_segments:5697,source_road_segments:10817}};
      window.mapPayload={origin_utm_m:[731167.7004159444,3753882.2119486625,0],map_reference:{origin_lonlat:[35.5,33.9],crs:'EPSG:32636'},
        roads:[{id:1,path:[[-900,-400,0],[1600,600,0]]},{id:2,path:[[300,-1000,0],[300,1300,0]]}]};
      window.ue={beirut:{getstate:async()=>{
              const snapshot=JSON.stringify(window.testState);
              await new Promise(r=>setTimeout(r,window.stateDelay||0));
              return snapshot;
            },execute:async request=>{
        const d=JSON.parse(request);window.actions.push(d);
        if(d.action==='map_settings') {
          await new Promise(r=>setTimeout(r,250));
          if(window.failMapSave){
            window.testState.map_settings_error='Preference file is not writable';
            return JSON.stringify({error:window.testState.map_settings_error});
          }
          const next={...window.testState.map_settings};
          if(['street','satellite','hybrid'].includes(d.mode))next.mode=d.mode;
          if(typeof d.heading_up==='boolean')next.heading_up=d.heading_up;
          window.testState.map_settings=next;
          window.testState.map_settings_error='';
          sessionStorage.setItem('map-settings',JSON.stringify(next));
          return JSON.stringify({map_settings:next,map_settings_error:'',message:'Map settings saved'});
        }
        if(d.action==='map_data')return JSON.stringify({map:window.mapPayload});
        if(d.action==='map_pick')window.testState.results=[{name:'Selected map location',detail:'Map pin'}];
        if(d.action==='search') {
          await new Promise(r=>setTimeout(r,window.searchDelay||120));
          window.testState.results=[{name:d.query+' Avenue',detail:'Street · local OSM'}];
        }
        if(d.action==='drive')window.testState.playing=true;
        if(d.action==='night')window.testState.night=!window.testState.night;
        return JSON.stringify({message:'Completed'});
      }}};
    });
    await page.goto(pathToFileURL(path.resolve(__dirname,'../engine/unreal/Content/UI/index.html')).href);
    await page.getByText('97.8%',{exact:true}).waitFor({state:'attached'});
    await page.evaluate(()=>{const create=L.map;L.map=(...args)=>(window.atlas=create(...args));});
    const saved=async mode=>{
      await page.waitForFunction(mode=>document.querySelector('#map-settings-status').textContent.startsWith('Saved · '+mode)&&
        !document.querySelector('[data-map-mode="'+mode+'"]').disabled,mode);
    };
    await saved('street');
    // Backend fallback settings remain usable, but must never be presented as saved.
    const initialFeedback=await page.locator('#feedback').textContent();
    for(const message of ['Could not read map preferences','Map preference file is corrupt','Native HUD could not save map preferences']){
      await page.evaluate(async message=>{
        window.testState.map_settings={version:1,mode:'street',heading_up:true};
        window.testState.map_settings_error=message;await poll();await poll();
      },message);
      assert.equal(await page.locator('#map-settings-status').textContent(),'Error: '+message);
      assert.equal(await page.locator('#map-settings-status').evaluate(el=>el.classList.contains('text-warning')),true);
      assert.equal(await page.locator('[data-map-mode="street"]').getAttribute('aria-pressed'),'true');
      assert.equal(await page.locator('#map-heading').isChecked(),true);
      assert.equal(await page.locator('#feedback').textContent(),initialFeedback,'Polling errors do not repeat global feedback');
    }
    assert.equal(await page.evaluate(()=>window.actions.length),0,'Rendering backend errors sends no action');
    await page.evaluate(async()=>{window.testState.map_settings_error='';await poll();});
    await saved('street');
    assert.equal(await page.locator('#map-settings-status').evaluate(el=>el.classList.contains('text-warning')),false,'Empty authoritative error clears warning');
    await page.evaluate(async()=>{window.testState.map_settings_error='Native HUD could not save map preferences';await poll();});
    await page.evaluate(()=>action('map_settings',{heading_up:false}));await saved('street');
    assert.equal(await page.locator('#map-settings-status').evaluate(el=>el.classList.contains('text-warning')),false,'Successful save clears backend error');
    assert.equal(requests.length,0,'No map prefetch before opening atlas');
    await page.getByRole('link',{name:'Find a place',exact:true}).click();
    await page.locator('#query').fill('Charles Malek');
    await page.locator('#results button').filter({hasText:'Charles Malek Avenue'}).waitFor();
    await page.locator('#query').press('ArrowDown');await page.keyboard.press('Enter');
    await page.waitForFunction(()=>window.actions.some(a=>a.action==='go_to'));
    await page.locator('[data-query="Hamra"]').click();
    await page.locator('#results button').filter({hasText:'Hamra Avenue'}).waitFor();
    await page.screenshot({path:path.join(output,'smart-search.png')});
    const openMap=async()=>{
      await page.getByRole('link',{name:'City map',exact:true}).click();
      await page.getByText('2 exported road surfaces',{exact:false}).waitFor();
    };
    const settled=async()=>{
      await page.waitForFunction(()=>{
        const tiles=[...document.querySelectorAll('#city-map .leaflet-tile')];
        return tiles.length>0&&tiles.every(img=>img.complete&&img.naturalWidth>0)&&!document.querySelector('#map-tile-status').classList.contains('text-warning');
      },null,{timeout:30000});
      await page.waitForTimeout(350); // Leaflet's tile fade must finish before visual capture.
    };
    await openMap();await settled();
    assert.ok(requests.every(url=>url.startsWith('https://tiles.maps.eox.at/')),'Only EOX tile host');
    assert.ok(requests.some(url=>url.includes('/osm_3857/')));
    assert.ok(!requests.some(url=>url.includes('/s2cloudless_3857/')),'No imagery prefetch');
    await page.getByRole('button',{name:'Zoom in',exact:true}).click();
    await settled();
    await page.screenshot({path:path.join(output,'city-atlas.png')});
    assert.equal(await page.evaluate(()=>L.version),'1.9.4');
    // Street detail is crisp; switching to imagery clamps the actual view, not just tile URLs.
    assert.equal(await page.locator('#map-roads').isChecked(),false);
    await page.evaluate(()=>atlas.setZoom(16,{animate:false}));await settled();
    assert.equal(await page.evaluate(()=>atlas.getMaxZoom()),16);
    assert.equal(await page.evaluate(()=>window.testState.playing),false,'Preferences save outside PIE/driving');
    await page.getByRole('button',{name:'Satellite',exact:true}).click();
    await page.getByText('Saving map settings…',{exact:true}).waitFor();
    assert.equal(await page.locator('[data-map-mode="street"]').getAttribute('aria-pressed'),'true','No optimistic mode change');
    for(const selector of ['[data-map-mode="street"]','[data-map-mode="satellite"]','[data-map-mode="hybrid"]','#map-heading'])
      assert.equal(await page.locator(selector).isDisabled(),true,'Settings locked during action');
    await saved('satellite');await settled();
    assert.equal(await page.locator('[data-map-mode="satellite"]').getAttribute('aria-pressed'),'true');
    assert.equal(await page.evaluate(()=>atlas.getZoom()),14);
    assert.equal(await page.evaluate(()=>atlas.getMaxZoom()),14);
    await page.getByRole('button',{name:'Zoom in',exact:true}).click();
    assert.equal(await page.evaluate(()=>atlas.getZoom()),14);
    await page.getByText('Sharper street-level satellite requires a licensed high-resolution provider.',{exact:false}).waitFor();
    await page.getByText('Sentinel-2 cloudless 2016',{exact:false}).waitFor();
    await page.locator('#city-map').screenshot({path:path.join(output,'city-satellite.png')});
    await page.locator('.leaflet-control-attribution').getByRole('link',{name:'CC BY 4.0',exact:true}).waitFor();
    await page.getByRole('button',{name:'Hybrid',exact:true}).click();await saved('hybrid');await settled();
    assert.equal(await page.evaluate(()=>atlas.getMaxZoom()),14);
    assert.equal(await page.locator('#map-roads').isChecked(),false,'Basemap never enables modeled roads');
    assert.ok(requests.some(url=>url.includes('/overlay_bright_3857/')));
    assert.ok(requests.some(url=>url.includes('/streets_3857/')));
    // Satellite/context layers must never request nonexistent native high-zoom tiles.
    for(const url of requests){
      const match=url.match(/\/(s2cloudless_3857|overlay_bright_3857)\/default\/g\/(\d+)\//);
      if(match)assert.ok(Number(match[2])<=14,url);
    }
    await page.locator('#city-map').screenshot({path:path.join(output,'city-hybrid.png')});
    const attribution=await page.locator('.leaflet-control-attribution').textContent();
    assert.ok(attribution.includes('OpenStreetMap contributors')&&attribution.includes('MapServer')&&attribution.includes('2016 & 2017'));
    await page.locator('#map-roads').check();
    await page.locator('#map-roads').uncheck();
    await page.locator('#map-roads').check();
    await page.locator('#map-heading').click();await saved('hybrid');
    assert.deepEqual(await page.evaluate(()=>window.testState.map_settings),{version:1,mode:'hybrid',heading_up:true});
    // A state read started before a save cannot later reset the confirmed heading.
    await page.evaluate(()=>{window.stateDelay=700;window.oldPoll=poll();window.stateDelay=0;});
    await page.locator('#map-heading').click();await saved('hybrid');
    await page.evaluate(()=>window.oldPoll);
    assert.equal(await page.locator('#map-heading').isChecked(),false,'Stale poll cannot undo confirmed save');
    await page.locator('#map-heading').click();await saved('hybrid');
    // Preference failure keeps the confirmed mode and survives subsequent polling.
    await page.evaluate(()=>window.failMapSave=true);
    await page.getByRole('button',{name:'Street',exact:true}).click();
    await page.getByText('Error: Preference file is not writable',{exact:true}).waitFor();
    await page.evaluate(()=>poll());
    assert.equal(await page.locator('#map-settings-status').textContent(),'Error: Preference file is not writable','State retains native save error');
    assert.equal(await page.locator('[data-map-mode="hybrid"]').getAttribute('aria-pressed'),'true');
    assert.equal(await page.evaluate(()=>atlas.getMaxZoom()),14);
    await page.locator('#map-heading').click();
    await page.getByText('Error: Preference file is not writable',{exact:true}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('#map-heading').disabled);
    assert.equal(await page.locator('#map-heading').isChecked(),true,'Failed heading save restores confirmed value');
    await page.evaluate(()=>window.failMapSave=false);
    await page.getByRole('button',{name:'Street',exact:true}).click();await saved('street');
    assert.equal(await page.evaluate(()=>atlas.getMaxZoom()),16);
    assert.equal(await page.locator('#map-roads').isChecked(),true,'Local roads remain independent');
    // Native/HUD-originated state is authoritative and applying it sends no action or tile reload.
    const settingsCount=await page.evaluate(()=>window.actions.filter(a=>a.action==='map_settings').length);
    await page.evaluate(async()=>{window.testState.map_settings={version:1,mode:'satellite',heading_up:false};
      sessionStorage.setItem('map-settings',JSON.stringify(window.testState.map_settings));await poll();});
    await saved('satellite');await settled();
    const stableRequests=requests.length;
    await page.evaluate(async()=>{await poll();await poll();});
    assert.equal(requests.length,stableRequests,'Unchanged settings do not reload tiles');
    assert.equal(await page.locator('#map-heading').isChecked(),false);
    assert.equal(await page.evaluate(()=>window.actions.filter(a=>a.action==='map_settings').length),settingsCount,'Applying state must not save it again');
    await page.locator('#map-heading').click();await saved('satellite');
    // Reload simulates a newly opened panel; persistence belongs to the mock bridge, not the UI.
    const settingsActions=await page.evaluate(()=>window.actions.filter(a=>a.action==='map_settings'));
    await page.getByRole('link',{name:'Main menu',exact:true}).click();
    await page.reload();await saved('satellite');
    await page.evaluate(()=>{const create=L.map;L.map=(...args)=>(window.atlas=create(...args));});
    await openMap();await settled();
    assert.equal(await page.locator('[data-map-mode="satellite"]').getAttribute('aria-pressed'),'true');
    assert.equal(await page.evaluate(()=>atlas.getMaxZoom()),14);
    assert.equal(await page.locator('#map-roads').isChecked(),false);
    assert.equal(await page.locator('#map-heading').isChecked(),true,'Stored heading restored without rotating atlas');
    assert.equal(await page.evaluate(()=>window.actions.filter(a=>a.action==='map_settings').length),0,'Reload only reads settings');
    // Other actions lock map settings too, rather than silently rejecting a map click.
    await page.evaluate(()=>{window.searchDelay=500;window.searchInFlight=action('search',{query:'pending'});});
    assert.equal(await page.locator('[data-map-mode="street"]').isDisabled(),true);
    assert.equal(await page.locator('#map-heading').isDisabled(),true);
    await page.evaluate(()=>window.searchInFlight);
    await saved('satellite');
    await page.locator('#map-follow').check();
    await page.waitForTimeout(400);
    await page.evaluate(()=>{window.testState.map_position=[1400,1200,0];beirutMap.position(window.testState.map_position);});
    await page.waitForTimeout(400);
    // Enter selects the followed geographic centre, converted back into local metres.
    await page.locator('#city-map').focus();await page.keyboard.press('Enter');
    await page.locator('#results button').filter({hasText:'Selected map location'}).waitFor();
    const pick=await page.evaluate(()=>window.actions.filter(a=>a.action==='map_pick').at(-1));
    // Leaflet pans to integer screen pixels; at atlas zoom a pixel spans several metres.
    // Exact projection precision is asserted independently in verify_city_map.cjs.
    assert.ok(Math.abs(pick.x-1400)<15&&Math.abs(pick.y-1200)<15,JSON.stringify(pick));
    await page.locator('#results button').click();
    await page.waitForFunction(()=>window.actions.filter(a=>a.action==='go_to').length===1);
    await openMap();
    await page.locator('#city-map').focus();await page.keyboard.press('ArrowLeft');
    assert.equal(await page.locator('#map-follow').isChecked(),false);
    // Real pointer drag must pan, not send a destination.
    const count=await page.evaluate(()=>window.actions.filter(a=>a.action==='map_pick').length);
    const rect=await page.locator('#city-map').boundingBox();
    await page.mouse.move(rect.x+200,rect.y+150);await page.mouse.down();
    await page.mouse.move(rect.x+300,rect.y+180,{steps:10});await page.mouse.up();
    assert.equal(await page.evaluate(()=>window.actions.filter(a=>a.action==='map_pick').length),count);
    // A click (unlike a drag) sends finite local coordinates.
    await page.waitForTimeout(400);
    await page.locator('#city-map').click({position:{x:250,y:170}});
    await page.waitForFunction(n=>window.actions.filter(a=>a.action==='map_pick').length>n,count);
    assert.ok(await page.evaluate(()=>{const a=window.actions.filter(a=>a.action==='map_pick').at(-1);return Number.isFinite(a.x)&&Number.isFinite(a.y);}));
    await openMap();
    // Missing/unsupported reference must not silently assume a Beirut origin.
    await page.evaluate(()=>beirutMap.load({roads:window.mapPayload.roads,map_reference:{crs:'EPSG:3857'}}));
    await page.getByText('Local overlay and destination picking are unavailable',{exact:false}).waitFor();
    const invalidCount=await page.evaluate(()=>window.actions.filter(a=>a.action==='map_pick').length);
    await page.locator('#city-map').focus();await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(()=>window.actions.filter(a=>a.action==='map_pick').length),invalidCount);
    await page.evaluate(()=>beirutMap.load(window.mapPayload));
    // Failed provider requests get a persistent, actionable status; no retry loop.
    // Fixture tiles are no-store, so a redraw must hit the network (and be aborted) instead of reusing cached images.
    let abortedTiles=0;
    await page.route(tilePattern,route=>{abortedTiles++;return route.abort();});
    await page.locator('#map-retry').click();
    await page.getByText('Some map tiles could not load',{exact:false}).waitFor();
    assert.ok(abortedTiles>0,'Retry must request tiles again so the injected failure is exercised');
    await page.screenshot({path:path.join(output,'city-map-network-error.png')});
    await page.unroute(tilePattern);
    if(!live)await page.route(tilePattern,fixture);
    await page.locator('#map-retry').click();await settled();
    await page.setViewportSize({width:780,height:560});
    await page.getByRole('button',{name:'Whole network',exact:true}).click();
    await page.screenshot({path:path.join(output,'city-atlas-compact.png')});
    if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Horizontal clipping');
    await page.getByRole('link',{name:'Main menu',exact:true}).click();
    const hiddenCount=requests.length;
    await page.evaluate(()=>{window.testState.map_position=[2000,2000,0];beirutMap.position(window.testState.map_position);});
    await page.waitForTimeout(400);
    assert.equal(requests.length,hiddenCount,'No tile requests while atlas is hidden');
    await page.getByRole('button',{name:/^Start driving/}).click();
    await page.getByText('Atmosphere and traffic',{exact:true}).click(); // collapsed, drive-only section
    await page.getByRole('button',{name:'Switch to night',exact:true}).click();
    await page.getByRole('button',{name:'Switch to day',exact:true}).waitFor();
    await page.getByRole('button',{name:'Connect SUMO',exact:true}).click();
    await page.screenshot({path:path.join(output,'mobility-panel.png')});
    await page.getByRole('link',{name:'Controls',exact:true}).click();
    await page.getByText('Native map heading',{exact:true}).waitFor();
    // Standalone file preview cannot claim a save or pick an unconfirmed default basemap.
    const standalone=await browser.newPage();
    let standaloneTiles=0;
    await standalone.route(tilePattern,route=>{standaloneTiles++;return fixture(route);});
    await standalone.goto(pathToFileURL(path.resolve(__dirname,'../engine/unreal/Content/UI/index.html')).href+'#map');
    await standalone.getByText('Saved settings unavailable.',{exact:false}).waitFor();
    assert.equal(await standalone.locator('#map-heading').isDisabled(),true);
    assert.equal(await standalone.locator('[data-map-mode][aria-pressed="true"]').count(),0);
    assert.equal(standaloneTiles,0,'No speculative basemap requests before authoritative settings');
    await standalone.evaluate(()=>action('map_settings',{mode:'hybrid'}));
    await standalone.getByText('Error: Unreal bridge unavailable; settings were not saved.',{exact:true}).waitFor();
    await standalone.close();
    assert.deepEqual(errors,[]);assert.deepEqual(securityErrors,[]);
    const result={passed:true,errors,securityErrors,tileMode:live?'live EOX':'mock tiles',externalRequests:requests.length,
      settingsActions,restoredSettings:await page.evaluate(()=>window.testState.map_settings),
      actions:await page.evaluate(()=>window.actions),bridge:'mock; native verification separate'};
    fs.writeFileSync(path.join(output,'panel-check.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
