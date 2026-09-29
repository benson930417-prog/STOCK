import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {RectAreaLightUniformsLib} from 'three/addons/lights/RectAreaLightUniformsLib.js';
RectAreaLightUniformsLib.init();
const $=id=>document.getElementById(id),viewport=$('viewport'),loading=$('loading');
const asset=name=>new URL(name,import.meta.url).href;
const fromB=p=>new THREE.Vector3(p[0],p[2],-p[1]);
let renderer;
try{renderer=new THREE.WebGLRenderer({antialias:true});}catch(e){$('fallback').hidden=false;loading.textContent='此瀏覽器無法開啟3D；請看靜態預覽。';throw e;}
renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1;
renderer.domElement.setAttribute('aria-label','房間整合設計，可拖曳旋轉及縮放');viewport.prepend(renderer.domElement);
const scene=new THREE.Scene();scene.background=new THREE.Color('#e3e0d7');
const camera=new THREE.PerspectiveCamera(58,1,.02,100);
const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.minDistance=.35;controls.maxDistance=22;controls.maxPolarAngle=Math.PI*.93;
const hemi=new THREE.HemisphereLight(0xfaf6ec,0xa5a09a,1.4);scene.add(hemi);
const sun=new THREE.DirectionalLight(0xfff1d8,2);sun.position.copy(fromB([3,-5,7]));sun.target.position.copy(fromB([3.5,2,.5]));sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);Object.assign(sun.shadow.camera,{left:-8,right:8,top:8,bottom:-8,near:.1,far:30});sun.shadow.normalBias=.014;sun.shadow.bias=-.0001;scene.add(sun,sun.target);
const fill=new THREE.DirectionalLight(0xe5ecf1,.65);fill.position.copy(fromB([7,2,4]));scene.add(fill);
const area=(color,intensity,width,height,p,t)=>{const l=new THREE.RectAreaLight(color,intensity,width,height);l.position.copy(fromB(p));l.lookAt(fromB(t));scene.add(l);return l;};
const cove1=area(0xffd5a0,12,2.25,.025,[3.036,1.72,2.642],[2.90,1.72,2.81]);
const cove2=area(0xffd5a0,12,2.25,.025,[6.452,1.72,2.642],[6.60,1.72,2.81]);
const shelf=area(0xffd1a0,3,1.7,.025,[2.685,1.05,.905],[2.565,1.05,.79]);
const seatGlow=area(0xffcf92,3,1.65,.025,[3.60,1.05,.079],[3.98,1.05,.015]);
const stepGlow=area(0xffd1a0,3,.9,.025,[3.30,2.3,.08],[3.5,2.5,0]);
const platformGlow=area(0xffddb3,2.2,.97,.015,[2.203,2.303,.17],[2.34,2.44,.105]);
const wallGlow=area(0xffddb3,1.3,1,.02,[3.38,.28,1.22],[3.38,.02,1.35]);
const headGlow=area(0xffddb3,1.4,1.6,.025,[.12,1.08,1.45],[.01,1.08,1.2]);
const returnGlow=area(0xffddb3,1,.85,.02,[3.34,.49,.065],[3.34,.80,.012]);
const lights=[cove1,cove2,shelf,seatGlow,stepGlow,platformGlow,wallGlow];
const headCache=new Map();let headManifest,headModel,headSequence=0;
const loader=new GLTFLoader(),cache=new Map();let architecture,currentModel,currentVariant,manifest,sequence=0,view='design';
const presets={
 wardrobe:{p:[2.7,1.50,1.65],t:[.65,2.45,1.25],fov:70,cut:false},
 bench:{p:[4.7,2.45,1.9],t:[4.65,.35,1.1],fov:90,cut:false},
 headwall:{p:[2.40,1.10,1.45],t:[0,1.075,1.2],fov:65,cut:false},
 sidewall:{p:[4.45,2.55,1.6],t:[3.9,.2,1.0],fov:85,cut:false},
 design:{p:[4.85,2.55,1.8],t:[1.7,1.65,.87],fov:65,cut:false},
 room:{p:[6.65,1.05,1.7],t:[2.15,1.7,.95],fov:70,cut:false},
 bedstorage:{p:[1.30,2.55,1.60],t:[1.30,.85,.55],fov:75,cut:false},
 stairs:{p:[3.8,1.7,1.7],t:[2.12,2.12,.27],fov:65,cut:false},
 sofa:{p:[5.6,.95,1.53],t:[2.4,1.1,.81],fov:55,cut:false},
 top:{p:[3.4,2.5,11.5],t:[3.4,2.51,0],fov:48,cut:true},
 overview:{p:[7.8,-7.0,7.3],t:[3.0,2.4,.75],fov:48,cut:true},
 bath:{p:[2.30,3.85,1.65],t:[.75,4.9,1.1],fov:75,cut:false}
};
function updateProjection(){const v=presets[view],horizontalLimit=view==='sidewall'?90:105;camera.fov=Math.min(v.fov,THREE.MathUtils.radToDeg(2*Math.atan(Math.tan(THREE.MathUtils.degToRad(horizontalLimit/2))/camera.aspect)));camera.updateProjectionMatrix();}
function setView(key){view=key;const v=presets[key];camera.position.copy(fromB(v.p));controls.target.copy(fromB(v.t));updateProjection();controls.update();$('ceiling').checked=!v.cut;$('walls').checked=!v.cut;document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===key)));visibility();}
function prepare(root){
 const meshes=[];root.traverse(o=>{if(o.isMesh)meshes.push(o);});
 for(const o of meshes){o.castShadow=true;o.receiveShadow=true;
 const mats=Array.isArray(o.material)?o.material:[o.material];
 for(const m of mats){if(m.transmission>0){m.transmission=0;m.transparent=true;m.opacity=o.userData.display_glass?.24:.14;m.depthWrite=false;o.castShadow=false;}}
 if(o.userData.led||/^LED[_ ]diffuser/.test(o.name)){o.userData.lightSurface=true;o.castShadow=false;for(const m of mats)m.emissiveIntensity=o.userData.emission_level??1.4;}
 if(o.userData.drawer_travel){o.userData.moveAxis=o.userData.drawer_axis||'x';o.userData.closedPosition=o.position[o.userData.moveAxis];}
 if(!o.userData.lightSurface&&!mats.some(m=>m.transparent)){
 const edges=new THREE.LineSegments(new THREE.EdgesGeometry(o.geometry,40),new THREE.LineBasicMaterial({color:0x4c5145,transparent:true,opacity:.22,depthWrite:false}));edges.userData.crease=true;edges.visible=false;o.add(edges);
 }
 }
}
function visibility(){
 const mode=$('lighting').value,lightOn=mode!=='off',night=mode==='night',evening=mode==='evening',factor=night?.55:1;
 for(const root of [architecture,currentModel,headModel])if(root)root.traverse(o=>{
  if(o.userData.viewer_group==='Ceiling')o.visible=$('ceiling').checked;
  if(['FrontWall','LeftWall'].includes(o.userData.viewer_group))o.visible=$('walls').checked;
  if(o.userData.crease)o.visible=$('edges').checked;
  if(o.userData.seat_option)o.visible=o.userData.seat_option===$('seat').value;
  if(o.userData.drawer_travel)o.position[o.userData.moveAxis]=o.userData.closedPosition+($('drawers').checked?o.userData.drawer_travel*(o.userData.drawer_direction??1):0);
  if(o.userData.lightSurface){const mats=Array.isArray(o.material)?o.material:[o.material];for(const m of mats)m.emissiveIntensity=lightOn?(o.userData.emission_level??1.4)*factor:0;}
 });
 hemi.intensity=night?.34:evening?.62:1.4;sun.intensity=night?.16:evening?.4:2;fill.intensity=night?.18:evening?.35:.8;renderer.toneMappingExposure=night?1.15:1;
 lights.forEach((l,i)=>{l.visible=lightOn&&(i<2?$('ceiling').checked:true)&&(i!==5||currentVariant?.id==='j'||!!currentVariant?.window_set)&&(i!==6||!!currentVariant?.window_set);l.intensity=(i<2?12:i===5?2.2:i===6?1.3:currentVariant?.style==='modern'?1.65:3)*factor;});
 const hasHead=!!currentVariant?.window_set||currentVariant?.id==='j';
 if(headModel)headModel.visible=hasHead;
 headGlow.visible=lightOn&&hasHead&&!!headModel;headGlow.intensity=1.4*factor;
 returnGlow.visible=lightOn&&!!currentVariant?.window_set;returnGlow.intensity=factor;
 if(currentVariant?.window_set)$('dimensions').textContent='訂製沙發190×85・座高45｜後檯深20・高50｜窗簾至檯上1.5 cm';
}
const textureLoader=new THREE.TextureLoader(),textures=new Map();
const palettes={ash:{wood:'ash.png',paint:'#d4d5cf',cloth:'#9ea39b',accent:'#50616a'},warm:{wood:'warm-oak.png',paint:'#c9c6be',cloth:'#a79f92',accent:'#656963'},smoke:{wood:'smoke-oak.png',paint:'#c8cac5',cloth:'#858d8d',accent:'#343d42'}};
function applyFinish(){
 if(!currentModel||!currentVariant?.window_set)return;
 const p=palettes[$('palette').value];
 if(!textures.has(p.wood)){const t=textureLoader.load(asset(p.wood));t.colorSpace=THREE.SRGBColorSpace;t.flipY=false;t.wrapS=t.wrapT=THREE.RepeatWrapping;t.anisotropy=4;textures.set(p.wood,t);}
 const materials=new Set();for(const root of [currentModel,headModel])if(root)root.traverse(o=>{if(o.isMesh)for(const m of Array.isArray(o.material)?o.material:[o.material])materials.add(m);});
 for(const m of materials){
  if(m.name.startsWith('Modern desaturated ash')){m.color.set(0xffffff);m.map=textures.get(p.wood);m.needsUpdate=true;}
  if(m.name.startsWith('Modern chalk lacquer'))m.color.set(p.paint);
  if(m.name.startsWith('Modern stone textile'))m.color.set(p.cloth);
  if(m.name.startsWith('Modern graphite textile'))m.color.set(p.accent);
 }
}
async function switchHead(id,focus=false){
 const token=++headSequence,option=headManifest.options.find(o=>o.id===id);if(!option)return;
 $('head-note').textContent='載入床頭展示…';
 try{
  let model=null;
  if(id!=='none'){
   if(!headCache.has(id))headCache.set(id,loader.loadAsync(asset('head-'+id+'.glb?v=1')).then(g=>{prepare(g.scene);return g.scene;}).catch(e=>{headCache.delete(id);throw e;}));
   model=await headCache.get(id);
  }
  if(token!==headSequence)return;
  if(headModel)scene.remove(headModel);headModel=model;if(model)scene.add(model);
  $('head-note').textContent=option.description+(id==='none'?'':' 展示區寬175、總深20 cm；不超出深20 cm齊平滑門下櫃；床尾保留屋簷。');
  const z=id==='ledge'?.91:id==='floating'?1.47:id==='enclosed'?1.49:1.3;
  headGlow.position.copy(fromB([.12,1.08,z]));headGlow.lookAt(fromB([.01,1.08,z-.22]));
  applyFinish();visibility();if(focus)setView('headwall');document.body.dataset.loadedHead=id;
 }catch(e){console.error(e);$('head-note').textContent='床頭展示載入失敗，請重新選擇。';}
}
async function switchVariant(id){
 const token=++sequence,v=manifest.variants.find(v=>v.id===id);if(!v)return;
 loading.hidden=false;loading.textContent='載入 '+v.name+'…';
 try{
  if(!cache.has(id))cache.set(id,loader.loadAsync(asset(id+'.glb?v=29')).then(g=>{prepare(g.scene);return g.scene;}));
  const model=await cache.get(id);if(token!==sequence)return;
  if(currentModel)scene.remove(currentModel);currentModel=model;currentVariant=v;scene.add(model);
  const bedFacing=v.cabinet_faces==='bed';
  shelf.width=Math.max(.6,v.cap_length/100-.04);shelf.position.copy(fromB([bedFacing?2.565:2.685,(40+v.cap_length)/200,.905]));shelf.lookAt(fromB([bedFacing?2.685:2.565,(40+v.cap_length)/200,.79]));
  seatGlow.width=Math.max(.8,v.sofa_length/100-.06);seatGlow.position.copy(fromB([3.60,(40+v.sofa_length)/200,.079]));seatGlow.lookAt(fromB([3.98,(40+v.sofa_length)/200,.015]));
  if(v.window_set){shelf.position.copy(fromB([2.565,.95,.62]));shelf.lookAt(fromB([2.685,.95,.49]));seatGlow.width=1.7;seatGlow.position.copy(fromB([4.75,.99,.07]));seatGlow.lookAt(fromB([4.75,1.23,.012]));}
  const edge=v.steps[0].edge,mid=[(edge[0][0]+edge[1][0])/200,(edge[0][1]+edge[1][1])/200,v.steps[0].height/100-.02];stepGlow.position.copy(fromB(mid));stepGlow.lookAt(fromB([mid[0]+.2,mid[1]+.2,0]));
  stepGlow.width=Math.hypot(edge[0][0]-edge[1][0],edge[0][1]-edge[1][1])/100*.9;
  const upper=v.platform_access?.top_edge||[[255,195],[185,265]],ux=(upper[0][0]+upper[1][0])/200,uy=(upper[0][1]+upper[1][1])/200;
  platformGlow.position.copy(fromB([ux,uy,.17]));platformGlow.lookAt(fromB([ux+.14,uy+.14,.105]));platformGlow.width=Math.hypot(upper[0][0]-upper[1][0],upper[0][1]-upper[1][1])/100*.9;
  $('name').textContent=v.name;$('description').textContent=v.description;$('description').classList.toggle('warning',id==='e');
  $('dimensions').textContent=`沙發 ${Math.round(v.sofa_length)} · 矮牆 ${v.cap_length} · 衣櫃 ${v.wardrobe_width} cm｜門前 ${v.bathroom_ground}`;
  $('palette').disabled=$('seat').disabled=!v.window_set;
  $('headwall').disabled=!(v.window_set||v.id==='j');
  $('head-note').hidden=$('headwall').disabled;
  $('set-note').textContent='一體式主方案已固定；床頭展示、坐墊、配色與光線可獨立調整。';
  $('fallback').src=asset(id+'-preview.png');applyFinish();visibility();loading.hidden=true;
  document.body.dataset.loadedVariant=id;
 }catch(e){console.error(e);loading.textContent='3D載入失敗，請重新整理。';$('fallback').hidden=false;}
}
$('headwall').addEventListener('change',e=>switchHead(e.target.value,true));
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
for(const id of ['ceiling','walls','lighting','seat','edges','drawers'])$(id).addEventListener('change',visibility);
$('palette').addEventListener('change',applyFinish);
$('fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('compare').requestFullscreen();}catch(e){console.warn(e);}});
new ResizeObserver(()=>{renderer.setSize(viewport.clientWidth,viewport.clientHeight);camera.aspect=viewport.clientWidth/viewport.clientHeight;updateProjection();}).observe(viewport);
try{
 manifest=await (await fetch(asset('variants.json'))).json();
 headManifest=await (await fetch(asset('headwall-options.json'))).json();
 for(const h of headManifest.options){const o=document.createElement('option');o.value=h.id;o.textContent=h.name;$('headwall').append(o);}
 $('headwall').value=headManifest.default;
 const gltf=await loader.loadAsync(asset('architecture.glb?v=11'));architecture=gltf.scene;prepare(architecture);scene.add(architecture);setView('bench');await switchVariant(manifest.default);await switchHead(headManifest.default);
}catch(e){console.error(e);loading.textContent='載入失敗，請重新整理。';}
renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,camera);});
