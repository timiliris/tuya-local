'use strict';
const DisplayTheme = (()=>{
  const system=window.matchMedia('(prefers-color-scheme: dark)');
  let preference='system';
  try {preference=localStorage.getItem('tuya-local-theme') || 'system';} catch {}
  function apply(next=preference) {
    preference=['light','dark','system'].includes(next)?next:'system';
    document.documentElement.dataset.theme=preference==='system'?(system.matches?'dark':'light'):preference;
    try {localStorage.setItem('tuya-local-theme',preference);} catch {}
  }
  system.addEventListener('change',()=>{if(preference==='system')apply();});apply();
  return {apply,get preference(){return preference;}};
})();

class SentinelDisplay {
  constructor({tiles,start,stop,changeView,changePage,translate}) {
    Object.assign(this,{tiles,start,stop,changeView,changePage,translate});
    this.active=false;this.selected=new Set();this.previous=new Set();this.mode='grid';this.interval=20;this.index=0;
    try {this.saved=JSON.parse(localStorage.getItem('tuya-local-sentinel') || '{}');} catch {this.saved={};}
    this.saved ||= {};
    document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible' && this.active)this.acquireWakeLock();});
    document.addEventListener('fullscreenchange',()=>{if(this.active && this.fullscreen && !document.fullscreenElement)this.exit();});
    document.addEventListener('keydown',event=>{if(event.key==='Escape' && this.active)this.exit();});
  }
  text(key,variables) {return this.translate(key,variables);}
  configure() {
    const list=document.querySelector('#sentinel-cameras');list.replaceChildren();const legend=document.createElement('legend');legend.textContent=this.text('Caméras à afficher');list.append(legend);
    const defaults=new Set(Array.isArray(this.saved.cameras)?this.saved.cameras:[...this.tiles].filter(([,tile])=>tile.recovery.requested).map(([id])=>id));
    if(!defaults.size && this.tiles.size)defaults.add(this.tiles.keys().next().value);
    for(const [id,tile] of this.tiles) {
      const label=document.createElement('label'),input=document.createElement('input'),name=document.createElement('span');
      input.type='checkbox';input.value=id;input.checked=defaults.has(id);name.textContent=tile.camera.deviceName;label.append(input,name);list.append(label);
    }
    document.querySelector('#sentinel-mode').value=this.saved.mode==='rotation'?'rotation':'grid';
    document.querySelector('#sentinel-interval').value=[10,20,30,60].includes(this.saved.interval)?this.saved.interval:20;
    document.querySelector('#sentinel-quality').value=this.saved.quality==='hd'?'hd':'sd';
    document.querySelector('#sentinel-dialog').showModal();
  }
  enter(config,restore=false) {
    const cameras=(config.cameras || []).filter(id=>this.tiles.has(id));
    if(!cameras.length)return false;
    this.saved={cameras,mode:config.mode==='rotation'?'rotation':'grid',interval:[10,20,30,60].includes(Number(config.interval))?Number(config.interval):20,quality:config.quality==='hd'?'hd':'sd'};
    try {localStorage.setItem('tuya-local-sentinel',JSON.stringify(this.saved));sessionStorage.setItem('tuya-local-sentinel-active','1');} catch {}
    this.previous=new Set([...this.tiles].filter(([,tile])=>tile.recovery.requested).map(([id])=>id));
    this.previousQualities=new Map([...this.tiles].map(([id,tile])=>[id,tile.node.querySelector('select').value]));
    this.selected=new Set(cameras);this.mode=this.saved.mode;this.interval=this.saved.interval;this.index=0;this.active=true;
    this.previousView=typeof view==='undefined'?'grid':view;this.previousFocus=typeof focused==='undefined'?null:focused;
    this.changePage('cameras');this.changeView('grid');
    document.body.classList.add('sentinel');document.querySelector('#sentinel-bar').hidden=false;
    document.querySelector('#sentinel-dialog').close();
    if(!restore)this.fullscreenScreen();
    this.acquireWakeLock();
    for(const [id,tile] of this.tiles) {
      if(this.selected.has(id)) {
        const select=tile.node.querySelector('select');
        const changed=select.value!==this.saved.quality;select.value=this.saved.quality;
        if(changed && tile.recovery.requested) {this.stop(tile).then(()=>{if(this.active && this.selected.has(id)){tile.sentinelStart=setTimeout(()=>{if(this.active && this.selected.has(id))this.start(tile);},5500);}});}
        else if(!tile.recovery.requested)this.start(tile);
      } else if(tile.recovery.requested)this.stop(tile);
    }
    this.layout();this.tick();this.clock=setInterval(()=>this.tick(),1000);return true;
  }
  layout() {
    if(!this.active)return;
    const ids=[...this.selected].filter(id=>this.tiles.has(id));
    this.index=ids.length?this.index%ids.length:0;
    for(const [id,tile] of this.tiles)tile.node.classList.toggle('sentinel-visible',this.selected.has(id) && (this.mode==='grid' || id===ids[this.index]));
    const count=this.mode==='rotation'?1:ids.length;
    const columns=count<=1?1:count<=4?2:3,rows=Math.max(1,Math.ceil(count/columns));
    document.querySelector('#camera-grid').style.setProperty('--sentinel-columns',columns);
    document.querySelector('#camera-grid').style.setProperty('--sentinel-rows',rows);
    document.querySelector('#camera-grid').style.setProperty('--sentinel-count',count);
    document.querySelector('#sentinel-next').hidden=this.mode!=='rotation';
    document.querySelector('#sentinel-mode-label').textContent=this.text(this.mode==='rotation'?'Rotation automatique':'Mosaïque');
  }
  next() {if(!this.active)return;this.index++;this.layout();this.rotateAt=Date.now()+this.interval*1000;}
  tick() {
    if(!this.active)return;
    if(!this.rotateAt)this.rotateAt=Date.now()+this.interval*1000;
    if(this.mode==='rotation' && Date.now()>=this.rotateAt)this.next();
    document.querySelector('#sentinel-time').textContent=new Date().toLocaleTimeString(I18N.language,{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    const live=[...this.selected].filter(id=>this.tiles.get(id)?.phase==='playing').length;
    document.querySelector('#sentinel-health').textContent=this.text('{live}/{total} caméras en direct',{live,total:this.selected.size});
    document.querySelector('#sentinel-wake').textContent=this.text(this.wakeLock && !this.wakeLock.released?'Écran maintenu éveillé':'Veille gérée par le navigateur');
    this.layout();
  }
  async acquireWakeLock() {
    if(!this.active || this.acquiring || this.wakeLock && !this.wakeLock.released || !navigator.wakeLock || document.visibilityState!=='visible')return;
    this.acquiring=true;
    try {const lock=await navigator.wakeLock.request('screen');if(!this.active)await lock.release();else this.wakeLock=lock;}catch{}finally{this.acquiring=false;}
  }
  fullscreenScreen() {
    if(!document.documentElement.requestFullscreen)return;
    document.documentElement.requestFullscreen().then(()=>{if(this.active)this.fullscreen=true;else if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});}).catch(()=>{});
  }
  exit() {
    if(!this.active)return;
    this.active=false;clearInterval(this.clock);this.rotateAt=null;
    try {sessionStorage.removeItem('tuya-local-sentinel-active');} catch {}
    document.body.classList.remove('sentinel');document.querySelector('#sentinel-bar').hidden=true;
    for(const [id,tile] of this.tiles) {
      clearTimeout(tile.sentinelStart);tile.node.classList.remove('sentinel-visible');
      if(!this.previous.has(id))this.stop(tile);
      else {
        const select=tile.node.querySelector('select'),previous=this.previousQualities.get(id);
        const changed=previous && select.value!==previous; if(previous)select.value=previous;
        if(changed && tile.recovery.requested)changeQuality(tile);
        else if(!tile.recovery.requested)this.start(tile);
      }
    }
    this.changeView(this.previousView,this.previousFocus);
    this.wakeLock?.release().catch(()=>{});this.wakeLock=null;
    if(this.fullscreen && document.fullscreenElement)document.exitFullscreen().catch(()=>{});this.fullscreen=false;
  }
  restore() {
    if(this.restored)return;this.restored=true;
    try {if(sessionStorage.getItem('tuya-local-sentinel-active')==='1')this.enter(this.saved,true);}catch{}
  }
}
