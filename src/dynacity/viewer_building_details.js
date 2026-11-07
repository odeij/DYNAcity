/* Decorative facade detail follows source footprints/heights. Window layout and
 * roof coping are illustrative, never a survey of openings or roof equipment. */
let buildingDetailCache = null;
let modelBuildingDetailSource = null, modelBuildingDetailItems = [];
function visibleBuildingDetailItems() {
  if(!beirutOverlay.active)return buildingsData();
  if(modelBuildingDetailSource!==beirutOverlay.polygons) {
    modelBuildingDetailSource=beirutOverlay.polygons;
    modelBuildingDetailItems=(beirutOverlay.polygons??[]).flatMap(p=>{
      const model=beirutOverlay.data?.buildings[p.beirutModel];
      if(!model||!Number.isFinite(model.height_m))return [];
      const record=model.objectid==null?null:beirutOverlay.snapshotById?.get(String(model.objectid));
      return [{b:{id:`beirut-model-${p.beirutModel}`,h:model.height_m,state:record?.state??'model'},ring:p.rings[0]}];
    });
  }
  return modelBuildingDetailItems;
}
function buildingDetailGeometry(items, centre, radius = 250) {
  const windows=[],coping=[],sx=111320*Math.cos(centre[1]*Math.PI/180),sy=111320;
  const metric=p=>[(p[0]-centre[0])*sx,(p[1]-centre[1])*sy];
  const ll=(x,y,z)=>[centre[0]+x/sx,centre[1]+y/sy,z];
  // Spend the finite detail budget nearest the camera, rather than in source
  // file order, which can leave foreground buildings bare.
  const nearby=items.filter(item=>item.ring?.length).map(item=>{
    const points=item.ring.map(metric),xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    const distance=Math.hypot(Math.max(0,Math.min(...xs),-Math.max(...xs)),Math.max(0,Math.min(...ys),-Math.max(...ys)));
    return {...item,points,distance};
  }).filter(item=>Number.isFinite(item.distance)&&item.distance<=radius).sort((a,b)=>a.distance-b.distance);
  let count=0;
  for(const {b,ring,points} of nearby) {
    if(count>=120||windows.length>=6000)break;
    if(!ring?.length||['empty_or_parking','demolished','destroyed'].includes(b.state))continue;
    const h=elevation(b);
    if(!Number.isFinite(h)||h<4.6||!points.every(p=>p.every(Number.isFinite)))continue;
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    if(Math.min(...xs)>radius||Math.max(...xs)<-radius||Math.min(...ys)>radius||Math.max(...ys)<-radius)continue;
    const area=points.reduce((s,p,i)=>{const q=points[(i+1)%points.length];return s+p[0]*q[1]-q[0]*p[1];},0);
    if(Math.abs(area)<2)continue;
    count++;
    for(let i=0;i<points.length;i++) {
      const a=points[i],c=points[(i+1)%points.length],dx=c[0]-a[0],dy=c[1]-a[1],length=Math.hypot(dx,dy);
      if(length<.4)continue;
      const nx=dy/length*Math.sign(area),ny=-dx/length*Math.sign(area),yaw=Math.atan2(dy,dx)*180/Math.PI;
      // Small continuous coping follows every actual edge, including concave roofs.
      coping.push({position:ll((a[0]+c[0])/2,(a[1]+c[1])/2,h+.015),scale:[length,.18,.18],orientation:[0,yaw,0],illustrative:true,buildingId:b.id});
      if(length<3.5)continue;
      const cols=Math.min(40,Math.floor((length-1.4)/3.2)),rows=Math.min(25,Math.floor((h-1.2)/3.2));
      for(let row=0;row<rows;row++)for(let col=0;col<cols;col++) {
        if(windows.length>=6000)break;
        const d=length*(col+1)/(cols+1),z=1.2+row*3.2;
        if(z+1.45>h-.45)continue;
        windows.push({position:ll(a[0]+dx*d/length+nx*.045,a[1]+dy*d/length+ny*.045,z),
          scale:[Math.min(1.15,length/(cols+1)*.55),.045,1.45],orientation:[0,yaw,0],illustrative:true,buildingId:b.id});
      }
    }
  }
  return {windows,coping};
}
function buildingDetailLayers() {
  if(ui.lens!=='observed'||ui.satellite||ui.selected||ui.highlight||ui.answerIds||ui.scenario)return [];
  const vp=!map&&deckgl?.getViewports()[0],zoom=map?map.getZoom():vp?.zoom??0;
  const centre=map?map.getCenter().toArray():vp&&[vp.longitude,vp.latitude];
  if(zoom<18.5||!centre)return [];
  const source=visibleBuildingDetailItems();
  if(!buildingDetailCache||buildingDetailCache.source!==source||metresBetween(centre,buildingDetailCache.centre)>40)
    buildingDetailCache={source,centre:centre.slice(),...buildingDetailGeometry(source,centre)};
  const occlusion=cameraOcclusion(),occlusionKey=[...occlusion.model].sort().join(',')+'|'+[...occlusion.bbed].sort().join(',');
  if(buildingDetailCache.occlusionKey!==occlusionKey) {
    const visible=d=>d.buildingId.startsWith('beirut-model-')?!occlusion.model.has(Number(d.buildingId.slice(13))):!occlusion.bbed.has(d.buildingId);
    buildingDetailCache.visibleWindows=occlusion.model.size||occlusion.bbed.size?buildingDetailCache.windows.filter(visible):buildingDetailCache.windows;
    buildingDetailCache.visibleCoping=occlusion.model.size||occlusion.bbed.size?buildingDetailCache.coping.filter(visible):buildingDetailCache.coping;
    buildingDetailCache.occlusionKey=occlusionKey;
  }
  const night=nightLevel(),common={mesh:mesh('building-detail-box',blockMesh),getPosition:d=>d.position,
    getScale:d=>d.scale,getOrientation:d=>d.orientation,pickable:false,material:{ambient:.6,diffuse:.6,shininess:8,specularColor:[25,30,35]}};
  // Once the night windows are on (viewer_night.js), the dark illustrative glass boxes would only cover them.
  const windows=night>=.5?[]:[new deck.SimpleMeshLayer({...common,id:'illustrative-facade-windows',data:buildingDetailCache.visibleWindows,
    getColor:shade(isDark()?[64,77,85]:[89,108,115],night,.65),updateTriggers:{getColor:[night,isDark()]}})];
  return [...windows,new deck.SimpleMeshLayer({...common,id:'illustrative-roof-coping',data:buildingDetailCache.visibleCoping,
    getColor:shade(isDark()?[114,119,123]:[192,190,179],night,.5),updateTriggers:{getColor:[night,isDark()]}})];
}
