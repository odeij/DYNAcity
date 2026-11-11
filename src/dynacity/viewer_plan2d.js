/* 3D city ⇄ 2D street planner (/planner), from wherever the camera is.
   Going down: the camera turns north-up and looks straight down at the same
   centre, the buildings sink into the ground and lifted details fade, then the
   planner (loaded in a frame at the same centre and zoom, so the same scale)
   fades in over the flattened city and lays its paper, desk and panel on top.
   Coming back runs it in reverse and the city rises at the planner's spot. */
const plan2d={flat:0,open:false,busy:false,hidden:false,frame:null,loaded:false,waiters:[],back:null,raf:0,gen:0};
const PLAN2D_ZOOM=[11,19];
const plan2dMs=(ms)=>(typeof calm!=='undefined'&&calm.matches)?Math.min(ms,180):ms;
const plan2dWait=(ms)=>new Promise(r=>setTimeout(r,ms));
const plan2dEase=(t)=>t<.5?4*t*t*t:1-(-2*t+2)**3/2;
function plan2dAvailable(){return !!(DATA.api&&DATA.traffic&&/^https?:$/.test(location.protocol));}

// Applied to the finished layer list each frame while plan2d.flat > 0.
function plan2dFlatten(list){
  const f=plan2d.flat;
  if(f<=0)return list;
  const rise=Math.max(.0005,1-f),fade=Math.max(0,1-f*2.4);
  return list.map((l)=>{
    if(!l)return l;
    const p=l.props;
    // Walls shrink to their footprints (deck scales extrusion, not position).
    if(p.extruded||l.id==='context')return l.clone({elevationScale:(p.elevationScale??1)*rise});
    // Roofs, meshes (cars, trees, bridges, signals) and points would float: fade them early.
    if(/roof|coping|canop|tree|bridge|pier|signal|car-|walker|furniture|cloud|granite|facade/i.test(l.id)
      ||(deck.SimpleMeshLayer&&l instanceof deck.SimpleMeshLayer)||(deck.PointCloudLayer&&l instanceof deck.PointCloudLayer))
      return fade>0?l.clone({opacity:(p.opacity??1)*fade}):l.clone({visible:false});
    return l;
  });
}

function plan2dTween(to,ms,delay=0){
  cancelAnimationFrame(plan2d.raf);
  const from=plan2d.flat;
  const gen=++plan2d.gen;
  return new Promise((done)=>{
    const start=performance.now()+delay;
    let finished=false;
    // A newer tween owns plan2d.flat: an older one only settles its promise.
    const finish=()=>{if(finished)return;finished=true;if(gen===plan2d.gen){cancelAnimationFrame(plan2d.raf);plan2d.flat=to;drawFx();}done();};
    const step=(now)=>{
      if(finished||gen!==plan2d.gen)return;
      const t=Math.min(1,Math.max(0,(now-start)/ms));
      plan2d.flat=from+(to-from)*plan2dEase(t);drawFx();
      if(t<1)plan2d.raf=requestAnimationFrame(step);else finish();
    };
    plan2d.raf=requestAnimationFrame(step);
    // A background tab gets no animation frames: land on the end state rather than hang.
    setTimeout(finish,delay+ms+700);
  });
}
function plan2dMove({centre,zoom,pitch,bearing},ms){
  return new Promise((done)=>{
    if(map){
      if(!ms){map.jumpTo({center:centre,zoom,pitch,bearing});done();return;}
      map.stop();map.easeTo({center:centre,zoom,pitch,bearing,duration:ms,easing:plan2dEase,essential:true});
      setTimeout(done,ms+40);
      return;
    }
    deckgl.setProps({initialViewState:{...home,maxPitch:75,maxZoom:20,longitude:centre[0],latitude:centre[1],zoom,pitch,bearing,
      transitionDuration:ms||0,transitionEasing:plan2dEase,
      transitionInterpolator:new deck.LinearInterpolator(['pitch','bearing','zoom','longitude','latitude'])}});
    setTimeout(done,(ms||0)+40);
  });
}
// The shortest turn to north-up, so a view facing south-west does not spin the long way.
const plan2dNorth=(b)=>((b+180)%360+360)%360-180;
function plan2dIdle(ms){
  if(!map)return plan2dWait(Math.min(ms,300));
  return Promise.race([new Promise(r=>map.once('idle',r)),plan2dWait(ms)]);
}

function plan2dPost(type,extra={}){plan2d.frame?.contentWindow?.postMessage({type,...extra},location.origin);}
function plan2dLoad(){
  if(plan2d.frame)return plan2d.frame;
  const cam=currentSceneCamera(),f=document.createElement('iframe');
  f.id='plan2d';f.title='2D street planner';f.setAttribute('aria-hidden','true');f.tabIndex=-1;
  const z=Math.min(PLAN2D_ZOOM[1],Math.max(PLAN2D_ZOOM[0],cam?.zoom??15));
  f.src=`/planner?embed=1#lon=${(cam?.centre[0]??35.5).toFixed(7)}&lat=${(cam?.centre[1]??33.89).toFixed(7)}&zoom=${z.toFixed(2)}`;
  document.body.appendChild(f);plan2d.frame=f;
  return f;
}
const plan2dWhenLoaded=()=>plan2d.loaded?Promise.resolve():new Promise(r=>plan2d.waiters.push(r));
let plan2dPrepared=null;
function plan2dPrepare(cam){
  return plan2dWhenLoaded().then(()=>new Promise((done)=>{
    plan2dPrepared=done;
    // The planner's three run hours; the nearest to the 3D clock.
    const hour=[8,13,17].reduce((a,h)=>Math.abs(h-(ui.hour??8))<Math.abs(a-(ui.hour??8))?h:a,8);
    // A planner that loads only after it is already showing keeps its paper on.
    const showing=!!plan2d.frame?.classList.contains('on');
    plan2dPost('dynacity-planner:prepare',{lon:cam.centre[0],lat:cam.centre[1],zoom:cam.zoom,hour,showing,bare:bareView()});
  }));
}

async function plan2dOpen(){
  if(plan2d.open||plan2d.busy||!deckgl)return;
  plan2d.busy=true;
  const cam=currentSceneCamera();
  plan2d.back={pitch:cam.pitch,bearing:cam.bearing};
  const zoom=Math.min(PLAN2D_ZOOM[1],Math.max(PLAN2D_ZOOM[0],cam.zoom));
  const target={centre:cam.centre,zoom,pitch:0,bearing:0};
  plan2dLoad();
  document.body.classList.add('plan2d-going');
  $('t-plan')?.setAttribute('aria-pressed','true');
  // Longer for a steep street-level view, so the tilt never feels like a snap.
  const ms=plan2dMs(900+Math.round(cam.pitch*8)+Math.round(Math.abs(plan2dNorth(cam.bearing))*2));
  const ready=plan2dPrepare(target);
  await Promise.all([plan2dMove(target,ms),plan2dTween(1,ms*1.05)]);
  await Promise.race([ready,plan2dWait(6000)]);
  const f=plan2d.frame;
  f.classList.add('on');f.removeAttribute('aria-hidden');f.tabIndex=0;
  plan2dPost('dynacity-planner:enter');
  await plan2dWait(plan2dMs(720));
  // Nothing of the 3D view shows now: stop drawing it.
  plan2d.hidden=true;$('scene').style.visibility='hidden';
  document.body.classList.add('plan2d-open');
  plan2d.open=true;plan2d.busy=false;
  try{f.contentWindow.focus();}catch(_){/* focus is a nicety */}
}

async function plan2dClose({lon,lat,zoom,bare}){
  if(!plan2d.open||plan2d.busy)return;
  plan2d.busy=true;
  if(typeof bare==='boolean')setBareView(bare);
  const centre=[lon,lat],f=plan2d.frame;
  // Under the still-opaque planner: put the flat city exactly where the planner was.
  plan2d.hidden=false;plan2d.flat=1;$('scene').style.visibility='';
  await plan2dMove({centre,zoom,pitch:0,bearing:0},0);
  render();
  await plan2dIdle(1400);
  f.classList.remove('on');f.setAttribute('aria-hidden','true');f.tabIndex=-1;
  document.body.classList.remove('plan2d-open');
  await plan2dWait(plan2dMs(380));
  document.body.classList.remove('plan2d-going');
  $('t-plan')?.setAttribute('aria-pressed','false');
  const back=plan2d.back??{pitch:homePitch(),bearing:-20};
  // From a top-down 3D view the rise would be invisible: tilt up to show it.
  const pitch=back.pitch>=20?back.pitch:55;
  if(back.pitch<20&&ui.topDown){ui.topDown=false;$('t-2d')?.setAttribute('aria-pressed','false');}
  await plan2dRise(centre,zoom,pitch,back.bearing);
  plan2d.open=false;plan2d.busy=false;
  $('t-plan')?.focus({preventScroll:true});
}
function plan2dRise(centre,zoom,pitch,bearing){
  const ms=plan2dMs(1500);
  return Promise.all([plan2dMove({centre,zoom,pitch,bearing},ms),plan2dTween(0,ms*1.1,plan2dMs(120))]);
}

addEventListener('message',(e)=>{
  if(e.origin!==location.origin||!plan2d.frame||e.source!==plan2d.frame.contentWindow)return;
  const m=e.data||{};
  if(m.type==='dynacity-planner:loaded'){plan2d.loaded=true;plan2d.waiters.splice(0).forEach(r=>r());}
  else if(m.type==='dynacity-planner:prepared'){const done=plan2dPrepared;plan2dPrepared=null;done?.();}
  else if(m.type==='dynacity-planner:leave'&&Number.isFinite(m.lon)&&Number.isFinite(m.lat)&&Number.isFinite(m.zoom))plan2dClose(m);
});

// ---- Bare view: hide every panel, keep the city, the credits line and one way back.
const bareView=()=>document.body.classList.contains('bare');
function setBareView(on){
  document.body.classList.toggle('bare',on);
  $('t-bare')?.setAttribute('aria-pressed',String(on));
  if(on)$('tool-menu')&&($('tool-menu').open=false);
}
function toggleBareView(){
  const on=!bareView();setBareView(on);
  (on?$('unbare'):$('t-bare'))?.focus({preventScroll:true});
}
function typingIn(t){return !!t&&(t.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));}
addEventListener('keydown',(e)=>{
  if((e.key==='h'||e.key==='H')&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!typingIn(e.target)&&!plan2d.open&&!plan2d.busy){e.preventDefault();toggleBareView();}
});

// Called once the scene exists.
function wirePlan2d(){
  if($('t-bare'))$('t-bare').onclick=toggleBareView;
  if($('unbare'))$('unbare').onclick=toggleBareView;
  const b=$('t-plan');
  if(!b)return;
  b.hidden=!plan2dAvailable();
  if(b.hidden)return;
  b.onclick=()=>plan2dOpen();
  // Start loading the planner as soon as the pointer heads for the button.
  for(const ev of ['pointerenter','focus','touchstart'])b.addEventListener(ev,()=>plan2dLoad(),{once:true,passive:true});
  // Arriving from the standalone planner (`#…&pitch=0&bearing=0&rise=1`): the city rises here.
  const p=new URLSearchParams(location.hash.slice(1));
  if(p.get('rise')==='1'){
    p.delete('rise');history.replaceState(null,'',`#${p}`);
    const cam=currentSceneCamera();
    if(!cam)return;
    plan2d.flat=1;drawFx();
    // `load` may already have fired; wait for the first quiet frame instead (or give up after 4 s).
    plan2dIdle(4000).then(()=>plan2dRise(cam.centre,cam.zoom,60,-20));
  }
}
