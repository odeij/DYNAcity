import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {MeshBVH, acceleratedRaycast} from 'three-mesh-bvh';
THREE.Mesh.prototype.raycast = acceleratedRaycast;
const $=id=>document.getElementById(id);
const safe=v=>String(v??'Not recorded').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={OBJECTID:'BBED object ID',BULBuildingID:'BUL building ID',ParcelID:'Parcel ID',Sector:'Sector',Building_Use:'Building use',NoofFloor:'Recorded floor count',Building_Hight_m:'Recorded height (m)',YearCompleted:'Year completed',PermitYear:'Permit year',Status2018:'Status · 2018',Status2022:'Status · 2022',F5__Current_status:'Status · source current field (date unverified)',Shape__Area:'Source area',Shape__Length:'Source perimeter'};
const fields=entries=>'<dl class="fields">'+entries.map(([k,v])=>`<div><dt>${safe(k)}</dt><dd>${safe(v)}</dd></div>`).join('')+'</dl>';
let data,objects,owners,geometry,mesh,selected=-1,pinned=false,hoverTimer,dragging=false,downAt;
const renderer=new THREE.WebGLRenderer({antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.setSize(innerWidth,innerHeight);renderer.setClearColor(0xe9eff2);document.body.prepend(renderer.domElement);
const scene=new THREE.Scene();const camera=new THREE.PerspectiveCamera(40,innerWidth/innerHeight,1,20000);camera.position.set(500,600,700);
const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.maxPolarAngle=Math.PI*.48;controls.minDistance=20;controls.maxDistance=14000;
scene.add(new THREE.HemisphereLight(0xffffff,0x738894,2.0));const sun=new THREE.DirectionalLight(0xffffff,2.1);sun.position.set(-1000,1800,900);scene.add(sun);
const selectedGeometry=new THREE.BufferGeometry();const highlight=new THREE.Mesh(selectedGeometry,new THREE.MeshBasicMaterial({color:0x1573c1,transparent:true,opacity:.62,depthWrite:false,polygonOffset:true,polygonOffsetFactor:-2,side:THREE.DoubleSide}));highlight.visible=false;scene.add(highlight);
let outlineMesh;const pointer=new THREE.Vector2();const raycaster=new THREE.Raycaster();raycaster.firstHitOnly=true;
function center(o){const b=o.bounds;return new THREE.Vector3((b[0]+b[3])/2,(b[2]+b[5])/2,-(b[1]+b[4])/2)}
function focus(i){const c=center(objects[i]);controls.target.copy(c);camera.position.copy(c).add(new THREE.Vector3(210,210,300));camera.lookAt(c);controls.update()}
function renderDetails(){
 if(selected<0)return;
 const o=objects[selected],link=o.link;const record=link.status==='provisional'?data.bbed[link.bbed_index]:null;const props=record?.properties;
 const which=$('source').value;
 let html=`<button id="pin" aria-label="${pinned?'Unpin building':'Pin building'}">${pinned?'Unpin':'Pin'}</button><div class="tag">${record?'PROVISIONAL SPATIAL LINK':'MODEL OBJECT · UNLINKED'}</div><h2>${props?`BBED building ${safe(props.OBJECTID)}`:'Beirut 001 building'}</h2>`;
 html+=`<div class="flag">${record?'Geometry agrees with this BBED footprint. The link is provisional; building identity has not been independently verified.':'No confident BBED link. No attributes from a nearby record are attached.'}</div>`;
 if(record)html+=`<p>Footprint overlap: ${Math.round(link.iou*100)}% · centroid gap: ${link.centroid_distance_m.toFixed(1)} m<br>Registration inferred from building patterns; not surveyed.</p>`;
 if(which!=='model'){
  html+='<h3>BBED source record</h3>';
  html+=record?fields(Object.entries(props).map(([k,v])=>[labels[k]||k,v===null?'Not recorded':v])):`<p class="empty">${safe(link.reason)}. This object has no BBED record attached.</p>`;
  html+='<p class="hint">Fields are shown as supplied by BBED. Nulls remain missing; the current-status field is not assumed to mean today.</p>';
 }
 if(which!=='bbed'){
  const b=o.bounds;
  html+='<h3>Beirut 001 source geometry</h3>'+fields([['Model object UUID',o.id],['Source object name',o.name],['Layer',o.layer],['Geometry type',o.kind],['Source release',o.source_date],['XY extent (model metres)',`${(b[3]-b[0]).toFixed(1)} × ${(b[4]-b[1]).toFixed(1)}`],['Vertical extent (model metres)',(b[5]-b[2]).toFixed(1)],['Model Z range (datum unknown)',`${b[2].toFixed(1)} to ${b[5].toFixed(1)}`],['Rendered triangles',o.triangles.toLocaleString()]]);
  html+='<p class="hint">These are model properties, not survey attributes. Vertical extent includes the model base and is not a verified building height.</p>';
 }
 html+=`<div class="notice">${pinned?'Pinned — use Unpin or Escape to follow hover.':'Hover updates this card. Click a building to pin it.'}</div>`;
 $('details').innerHTML=html;$('pin').onclick=()=>{pinned=!pinned;renderDetails()};
}
function select(i,pin=false){if(i==null||i<0)return;if(i===selected&&pin===pinned)return;selected=i;pinned=pin;const o=objects[i];selectedGeometry.setDrawRange(o.index_start,o.index_count);highlight.visible=true;renderDetails()}
function pick(event){if(!mesh||dragging)return null;const r=renderer.domElement.getBoundingClientRect();pointer.set((event.clientX-r.left)/r.width*2-1,-(event.clientY-r.top)/r.height*2+1);raycaster.setFromCamera(pointer,camera);const hits=raycaster.intersectObject(mesh,false);return hits.length?owners[hits[0].faceIndex]:null}
renderer.domElement.addEventListener('pointerdown',e=>{downAt=[e.clientX,e.clientY];dragging=false;clearTimeout(hoverTimer)});
renderer.domElement.addEventListener('pointermove',e=>{if(downAt&&Math.hypot(e.clientX-downAt[0],e.clientY-downAt[1])>4)dragging=true;if(pinned||downAt)return;clearTimeout(hoverTimer);hoverTimer=setTimeout(()=>{const i=pick(e);renderer.domElement.style.cursor=i==null?'grab':'pointer';if(i!=null)select(i)},70)});
renderer.domElement.addEventListener('pointerup',e=>{if(!dragging){const i=pick(e);if(i!=null)select(i,true)}downAt=null;dragging=false});
renderer.domElement.addEventListener('pointerleave',()=>{clearTimeout(hoverTimer);downAt=null;dragging=false});
window.addEventListener('keydown',e=>{if(e.key==='Escape'){pinned=false;renderDetails()}});
$('source').onchange=renderDetails;$('outlines').onchange=()=>{if(outlineMesh)outlineMesh.visible=$('outlines').checked};
$('example').onclick=()=>{focus(data.focus);select(data.focus,true)};
$('unmatched').onclick=()=>{const origin=center(objects[data.focus]);let best=-1,d=Infinity;objects.forEach((o,i)=>{if(o.link.status==='provisional')return;const n=center(o).distanceTo(origin);if(n<d){best=i;d=n}});focus(best);select(best,true)};
$('city').onclick=()=>{controls.target.set(-400,25,0);camera.position.set(2400,4800,6200);controls.update()};
function find(){if(!data)return;const q=$('search').value.trim().toLowerCase();if(!q)return;const i=objects.findIndex(o=>o.id.toLowerCase()===q||(o.link.status==='provisional'&&String(data.bbed[o.link.bbed_index].properties.OBJECTID).toLowerCase()===q));if(i<0){$('search-note').textContent='No linked model object found for that ID.';return}$('search-note').textContent='';focus(i);select(i,true)}
$('find').onclick=find;$('search').onkeydown=e=>{if(e.key==='Enter')find()};
window.addEventListener('resize',()=>{renderer.setSize(innerWidth,innerHeight);camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix()});
async function boot(){
 const response=await fetch('./data.json');if(!response.ok)throw new Error('Building metadata could not load');data=await response.json();objects=data.objects;
 const names=['positions','indices','owners','outlines'];const buffers=await Promise.all(names.map(async name=>{const r=await fetch(`./${name}.bin`);if(!r.ok)throw new Error(`${name} failed to load`);return r.arrayBuffer()}));
 const positions=new Float32Array(buffers[0]),indices=new Uint32Array(buffers[1]);owners=new Uint32Array(buffers[2]);
 if(indices.length/3!==owners.length)throw new Error('Mesh picking metadata does not match geometry');
 geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));geometry.setIndex(new THREE.BufferAttribute(indices,1));geometry.computeVertexNormals();
 const colors=new Float32Array(positions.length);colors.fill(.73);for(const o of objects){const rgb=o.link.status==='provisional'?[.24,.59,.54]:[.69,.72,.70];for(let j=o.index_start;j<o.index_start+o.index_count;j++){const index=indices[j]*3;colors[index]=rgb[0];colors[index+1]=rgb[1];colors[index+2]=rgb[2]}}geometry.setAttribute('color',new THREE.BufferAttribute(colors,3));
 $('loading').textContent='Preparing building selection…';
 geometry.boundsTree=new MeshBVH(geometry,{indirect:true});mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({vertexColors:true,roughness:1,metalness:0,side:THREE.DoubleSide}));scene.add(mesh);
 selectedGeometry.setAttribute('position',geometry.attributes.position);selectedGeometry.setIndex(geometry.index);selectedGeometry.boundingSphere=geometry.boundingSphere;
 const lines=new THREE.BufferGeometry();lines.setAttribute('position',new THREE.BufferAttribute(new Float32Array(buffers[3]),3));outlineMesh=new THREE.LineSegments(lines,new THREE.LineBasicMaterial({color:0x164f60,transparent:true,opacity:.85}));scene.add(outlineMesh);
 $('attribution').textContent=data.attribution;
 $('status').innerHTML=`<span class="key"></span>${data.matched.toLocaleString()} provisional BBED links<br><span class="key gray"></span>${data.unlinked.toLocaleString()} model objects without a confident link<br><span class="hint">${data.excluded_invalid_bbed_objectids.length} invalid BBED footprints excluded from matching.<br>BBED outlines projected above linked model roofs.</span>`;
 focus(data.focus);select(data.focus,false);$('loading').hidden=true;
}
boot().catch(e=>{$('loading').textContent='Could not load the overlay: '+e.message;console.error(e)});
renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,camera)});
