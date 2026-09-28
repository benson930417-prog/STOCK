import * as THREE from 'three';

export async function addOutletViewer({scene,camera,renderer,fromBlender}){
 const response=await fetch('./outlets.json');if(!response.ok)throw new Error('插座點位資料載入失敗');
 const data=await response.json();
 const toggle=document.getElementById('outlets'), info=document.getElementById('outlet-info');
 const select=document.getElementById('outlet-select'), detail=document.getElementById('outlet-detail');
 const group=new THREE.Group();group.name='P64 plan projections, mounting height unknown';scene.add(group);
 const sprites=[];
 for(const item of data.items){
  const canvas=document.createElement('canvas');canvas.width=128;canvas.height=128;const ctx=canvas.getContext('2d');
  ctx.beginPath();ctx.arc(64,64,53,0,Math.PI*2);ctx.fillStyle=item.kind==='power'?'#00767e':'#a76512';ctx.fill();ctx.lineWidth=7;ctx.strokeStyle='#ffffff';ctx.stroke();
  ctx.fillStyle='#ffffff';ctx.font='bold 46px system-ui';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(item.kind==='power'?item.id.slice(1):'?',64,66);
  const material=new THREE.SpriteMaterial({map:new THREE.CanvasTexture(canvas),depthTest:false,depthWrite:false,toneMapped:false});
  const sprite=new THREE.Sprite(material);sprite.position.copy(fromBlender([item.position[0],item.position[1],.12]));sprite.renderOrder=20;sprite.userData.outlet=item;
  group.add(sprite);sprites.push(sprite);
  const option=document.createElement('option');option.value=item.id;option.textContent=`${item.id} · ${item.label}`;select.append(option);
 }
 const show=()=>{group.visible=toggle.checked;info.hidden=!toggle.checked;};toggle.addEventListener('change',show);show();
 function choose(id){
  const item=data.items.find(i=>i.id===id);select.value=id||'';
  detail.textContent=item?`${item.id}｜${item.symbolCount?item.symbolCount+' 個插座符號':'用途未確認'}${item.extraSymbol?' ＋ C':''}。${item.note} 高度未標。`:'圓點是第 64 頁的平面投影，不是落地插座；高度未標，未隨新家具移位。';
  sprites.forEach(s=>s.userData.selected=s.userData.outlet.id===id);
 }
 select.addEventListener('change',()=>choose(select.value));choose('');
 let down;renderer.domElement.addEventListener('pointerdown',e=>down=[e.clientX,e.clientY]);
 renderer.domElement.addEventListener('pointerup',e=>{
  if(!group.visible||!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>5)return;
  const rect=renderer.domElement.getBoundingClientRect();
  const ray=new THREE.Raycaster();ray.setFromCamera(new THREE.Vector2((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1),camera);
  const hit=ray.intersectObjects(sprites,false)[0];if(hit)choose(hit.object.userData.outlet.id);
 });
 return ()=>{
  if(!group.visible)return;
  const height=renderer.domElement.clientHeight;
  for(const s of sprites){const size=camera.position.distanceTo(s.position)*2*Math.tan(THREE.MathUtils.degToRad(camera.fov)/2)*(s.userData.selected?40:29)/height;s.scale.set(size,size,1);}
 };
}
