import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {RectAreaLightUniformsLib} from 'three/addons/lights/RectAreaLightUniformsLib.js';
import {addOutletViewer} from './outlet-viewer.js';
RectAreaLightUniformsLib.init();
const viewport=document.querySelector('#viewport'), loading=document.querySelector('#loading');
const status=document.querySelector('#status');
const toggles=Object.fromEntries(['ceiling','walls','furniture'].map(k=>[k,document.getElementById(k)]));
const buttons=[...document.querySelectorAll('[data-view]')];
const edgeToggle=document.getElementById('edges'), edgeLines=[], architecturalEdgeParents=[];
edgeToggle.addEventListener('change',()=>edgeLines.forEach(line=>line.visible=edgeToggle.checked));
let renderer;
try { renderer=new THREE.WebGLRenderer({antialias:true}); }
catch(error){loading.textContent='瀏覽器無法啟用 3D 顯示，請先開啟下方的「五張渲染圖」。';throw error;}
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;
renderer.domElement.setAttribute('aria-label','臥室 A 互動 3D；拖曳旋轉、滾輪縮放');renderer.domElement.tabIndex=0;
viewport.prepend(renderer.domElement);
const scene=new THREE.Scene(); scene.background=new THREE.Color('#d3d7d3');
const camera=new THREE.PerspectiveCamera(45,1,.025,150);
const controls=new OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.dampingFactor=.12;controls.minDistance=.1;controls.maxDistance=40;controls.maxPolarAngle=Math.PI*.94;
scene.add(new THREE.HemisphereLight(0xffffff,0x8b8f85,1.2));
const sun=new THREE.DirectionalLight(0xfff6e9,1.7);sun.position.set(4,9,5);sun.target.position.set(4,0,-1.5);
sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);sun.shadow.camera.left=-9;sun.shadow.camera.right=9;sun.shadow.camera.top=9;sun.shadow.camera.bottom=-9;
sun.shadow.normalBias=.025;sun.shadow.bias=-.00015;scene.add(sun,sun.target);
const fill=new THREE.DirectionalLight(0xe5edff,1);fill.position.set(-5,4,-6);scene.add(fill);
let model, active='overview', interiorMode=false, pointer=null;
let updateOutletViewer=()=>{};
const fromBlender=a=>new THREE.Vector3(a[0],a[2],-a[1]);
const ledToggle=document.getElementById('led'), ledMaterials=[], ledLights=[];
for(const [x,z,tx,tz] of [[3.036,2.642,2.90,2.81],[6.452,2.642,6.60,2.81]]){
 const light=new THREE.RectAreaLight(0xffd8a8,20,2.25,.025);
 light.position.copy(fromBlender([x,1.72,z]));
 light.lookAt(fromBlender([tx,1.72,tz]));
 scene.add(light);ledLights.push(light);
}
function updateLED(){
 const on=ledToggle.checked;
 for(const m of ledMaterials){m.emissiveIntensity=on?1.25:0;m.color.set(on?0xd5b788:0xb5b1a7);}
 for(const light of ledLights)light.visible=on&&toggles.ceiling.checked;
}
ledToggle.addEventListener('change',updateLED);
const presets={
 outlets:{p:[3.8,-4.6,9.5],t:[3.8,1.8,0],label:'第 64 頁插座平面位置 · 高度未標',fov:45},
 overview:{p:[-4,-7.2,8.2],t:[3.7,1.8,1.15],label:'整體配置 · 剖開檢視',fov:45},
 top:{p:[4.2,1.84,13],t:[4.2,1.85,0],label:'俯視平面 · 左為床頭，右為陽台',fov:45},
 bed:{p:[.70,1.7,1.35],t:[7.6,1.7,1.35],label:'床上朝陽台看',fov:78},
 entry:{p:[3.45,2.88,1.60],t:[.8,1.60,1.1],label:'進房後看床區',fov:74},
 balcony:{p:[6.94,1.35,1.60],t:[1.2,1.68,1.1],label:'陽台側回看床區',fov:74}
};
function visibility(){if(!model)return;model.traverse(o=>{
 const g=o.userData.viewer_group;
 if(g==='Ceiling')o.visible=toggles.ceiling.checked;
 if(g==='FrontWall'||g==='LeftWall')o.visible=toggles.walls.checked;
 if(g==='Furniture')o.visible=toggles.furniture.checked;
});
architecturalEdgeParents.forEach(o=>{
 const g=o.userData.viewer_group;
 o.visible=g==='Ceiling'?toggles.ceiling.checked:['FrontWall','LeftWall'].includes(g)?toggles.walls.checked:g==='Furniture'?toggles.furniture.checked:true;
});updateLED();}
function preset(key){
 if(key==='outlets'){document.getElementById('outlets').checked=true;document.getElementById('outlets').dispatchEvent(new Event('change'));}
 document.getElementById('quickview').value=key;active=key;interiorMode=['bed','entry','balcony'].includes(key);
 const v=presets[key];camera.fov=v.fov;camera.updateProjectionMatrix();
 const target=fromBlender(v.t),pos=fromBlender(v.p);
 if(!interiorMode){
  const direction=pos.clone().sub(target).normalize();
  const width=9.4,height=8.4,vfov=THREE.MathUtils.degToRad(camera.fov);
  const dist=Math.max(width/(2*Math.tan(vfov/2)*camera.aspect),height/(2*Math.tan(vfov/2)))*1.08;
  pos.copy(target).addScaledVector(direction,dist);
 }
 controls.enabled=!interiorMode;camera.position.copy(pos);controls.target.copy(target);camera.lookAt(target);controls.update();
 toggles.ceiling.checked=interiorMode;toggles.walls.checked=interiorMode;visibility();
 buttons.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===key)));status.textContent=v.label;
}
buttons.forEach(b=>b.addEventListener('click',()=>preset(b.dataset.view)));
 document.getElementById('quickview').addEventListener('change',e=>preset(e.target.value));
Object.values(toggles).forEach(el=>el.addEventListener('change',visibility));
controls.addEventListener('start',()=>{active=null;buttons.forEach(b=>b.setAttribute('aria-pressed','false'));status.textContent='自由視角 · 點上方按鈕可回到預設位置';});
// Interior controls rotate around the eye instead of orbiting the far wall.
renderer.domElement.addEventListener('pointerdown',e=>{if(!interiorMode)return;pointer={x:e.clientX,y:e.clientY,button:e.button};renderer.domElement.setPointerCapture(e.pointerId);});
renderer.domElement.addEventListener('pointermove',e=>{
 if(!interiorMode||!pointer)return;
 const dx=e.clientX-pointer.x,dy=e.clientY-pointer.y;pointer.x=e.clientX;pointer.y=e.clientY;
 if(pointer.button===2){const right=new THREE.Vector3(1,0,0).applyQuaternion(camera.quaternion);camera.position.addScaledVector(right,-dx*.006);camera.position.y+=dy*.006;}
 else{const angles=new THREE.Euler().setFromQuaternion(camera.quaternion,'YXZ');angles.y-=dx*.004;angles.x=THREE.MathUtils.clamp(angles.x-dy*.004,-1.5,1.5);camera.quaternion.setFromEuler(angles);}
 status.textContent='室內自由視角 · 拖曳轉頭，滾輪移動';
});
renderer.domElement.addEventListener('pointerup',()=>{pointer=null;});
renderer.domElement.addEventListener('pointercancel',()=>{pointer=null;});
renderer.domElement.addEventListener('wheel',e=>{if(!interiorMode)return;e.preventDefault();const dir=new THREE.Vector3();camera.getWorldDirection(dir);camera.position.addScaledVector(dir,-Math.sign(e.deltaY)*.18);},{passive:false});
renderer.domElement.addEventListener('contextmenu',e=>e.preventDefault());
document.querySelector('#fullscreen').addEventListener('click',async()=>{
 try{if(document.fullscreenElement)await document.exitFullscreen();else await document.querySelector('#viewer').requestFullscreen();}
 catch{status.textContent='也可以拉寬瀏覽器面板，放大檢視。';}
});
document.addEventListener('fullscreenchange',()=>{document.querySelector('#fullscreen').textContent=document.fullscreenElement?'退出放大':'放大檢視';});
new ResizeObserver(()=>{const w=viewport.clientWidth,h=viewport.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();if(active&&!interiorMode)preset(active);}).observe(viewport);
new GLTFLoader().load('./bedroom-a-v1.glb?v=p69-traced-profile-2',async gltf=>{
 model=gltf.scene;
 model.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;const mats=Array.isArray(o.material)?o.material:[o.material];mats.forEach(m=>{if(m.transmission>0){m.transmission=0;m.transparent=true;m.opacity=.14;m.depthWrite=false;o.castShadow=false;}});}});
 scene.add(model);
 model.traverse(o=>{
  if(o.isMesh&&o.name.startsWith('LED_diffuser')){
   const m=new THREE.MeshStandardMaterial({color:0xd5b788,emissive:0xffd7a0,emissiveIntensity:1.25,roughness:.5,toneMapped:true});
   o.material=m;o.castShadow=false;ledMaterials.push(m);
  }
 });
 // Draw actual sharp edges, omitting coplanar triangulation and transparent glass.
 // Children inherit each mesh's transform and ceiling/wall visibility.
 let architecturalEdges=[];
 try{
  const response=await fetch('./architectural-edges.json?v=union-2');
  if(!response.ok)throw new Error('Architectural outlines unavailable');
  architecturalEdges=await response.json();
 }catch(error){console.error(error);}
 const mergedGroups=new Set(architecturalEdges.map(entry=>entry.group));
 const meshes=[];model.traverse(o=>{if(o.isMesh)meshes.push(o);});
 for(const mesh of meshes){
  const materials=Array.isArray(mesh.material)?mesh.material:[mesh.material];
  if(mesh.name.startsWith('LED_diffuser')||materials.some(m=>m.transparent&&m.opacity<.5))continue;
  materials.forEach(m=>{m.polygonOffset=true;m.polygonOffsetFactor=1;m.polygonOffsetUnits=1;});
  if(mergedGroups.has(mesh.userData.viewer_group)&&materials.some(m=>m.name==='01 chalk walls'))continue;
  const furniture=mesh.userData.viewer_group==='Furniture';
  const geometry=new THREE.EdgesGeometry(mesh.geometry,35);
  const material=new THREE.LineBasicMaterial({color:0x343b38,transparent:true,opacity:furniture?.32:.68,depthTest:true,depthWrite:false,toneMapped:false});
  const lines=new THREE.LineSegments(geometry,material);
  lines.name='Viewer crease edges';lines.visible=edgeToggle.checked;lines.renderOrder=2;
  mesh.add(lines);edgeLines.push(lines);
 }
 // These edges come from a solid union, so adjoining coplanar pieces have no seams.
 for(const entry of architecturalEdges){
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(entry.positions,3));
  const lines=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:0x343b38,transparent:true,opacity:.68,depthTest:true,depthWrite:false,toneMapped:false}));
  const parent=new THREE.Group();parent.userData.viewer_group=entry.group;
  lines.visible=edgeToggle.checked;lines.renderOrder=2;
  parent.add(lines);scene.add(parent);edgeLines.push(lines);architecturalEdgeParents.push(parent);
 }
 try{updateOutletViewer=await addOutletViewer({scene,camera,renderer,fromBlender});}catch(error){console.error(error);document.getElementById('outlet-detail').textContent='點位資料未載入，請開啟完整平面對照。';}
 preset(new URLSearchParams(location.search).get('view')==='outlets'?'outlets':'bed');loading.hidden=true;
},undefined,error=>{console.error(error);loading.textContent='模型載入失敗。請重新整理，或開啟下方「五張渲染圖」。';});
renderer.setAnimationLoop(()=>{if(!interiorMode)controls.update();updateOutletViewer();renderer.render(scene,camera);});








