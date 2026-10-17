'use strict';
function aiVideoRect(width,height,videoWidth,videoHeight){const scale=Math.min(width/videoWidth,height/videoHeight);return {x:(width-videoWidth*scale)/2,y:(height-videoHeight*scale)/2,width:videoWidth*scale,height:videoHeight*scale};}
function groupAIEvents(observations,configs={}){
    const events=[],latest=new Map();
    for(const event of observations){
      const key=event.deviceId+':'+event.class,group=latest.get(key),gap=configs[event.deviceId]?.cooldown||30;
      if(group&&group.start-event.time<=gap*2){group.start=event.time;group.observations++;if(event.score>group.score){group.score=event.score;group.box=event.box;if(event.snapshot){group.id=event.id;group.snapshot=true;}}}
      else {const entry={...event,start:event.time,observations:1};events.push(entry);latest.set(key,entry);}
    }
  return events;
}
class LocalAIConsole {
  constructor({api,tiles,translate,cameras}){
    this.api=api;this.tiles=tiles;this.t=translate;this.cameras=cameras;this.config=null;this.events=[];this.last=new Map();this.drafts=new Map();this.busy=false;
    this.form=document.querySelector('#ai-form');this.camera=document.querySelector('#ai-camera');
    this.camera.onchange=()=>this.fill();
    this.form.onchange=event=>{if(event.target===this.camera)return;try{this.drafts.set(this.camera.value,this.readForm());document.querySelector('#ai-status').textContent=this.t('Enregistre les changements pour les appliquer.');}catch{}};
    this.form.onsubmit=event=>{event.preventDefault();this.save();};
    document.querySelector('#ai-refresh-events').onclick=()=>this.refreshEvents();
    for(const id of ['#ai-event-camera','#ai-event-class'])document.querySelector(id).onchange=()=>this.renderEvents();
    document.querySelector('#ai-snapshot-close').onclick=()=>document.querySelector('#ai-snapshot-dialog').close();
    document.querySelector('#ai-clear-events').onclick=async()=>{if(!confirm(this.t('Effacer les événements et leurs vignettes du serveur ?')))return;try{await this.api('/api/ai/events/clear',{});await this.refreshEvents();}catch(error){document.querySelector('#ai-status').textContent=error.message;}};
    document.addEventListener('visibilitychange',()=>{if(document.hidden)for(const tile of this.tiles.values())this.clear(tile);});
  }
  async initialize(){
    await this.load();
    this.timer=setInterval(()=>this.tick(),2000);
    this.poll=setInterval(()=>{if(!document.querySelector('#ai-page').hidden)this.refreshEvents();},15000);
    window.addEventListener('pagehide',()=>{clearInterval(this.timer);clearInterval(this.poll);});
  }
  async load(){try{this.config=await this.api('/api/ai/state');this.renderStatus();}catch(error){document.querySelector('#ai-status').textContent=error.message;}}
  async open(){await this.load();if(!this.config)return;this.populate();this.fill();await this.refreshEvents();}
  option(value,label){const option=document.createElement('option');option.value=value;option.textContent=label;return option;}
  populate(){
    const previous=this.camera.value;
    this.camera.replaceChildren(...this.cameras().map(camera=>this.option(camera.deviceId,camera.deviceName)));
    if(this.cameras().some(camera=>camera.deviceId===previous))this.camera.value=previous;
    document.querySelector('#ai-model').replaceChildren(...this.config.models.map(model=>this.option(model.id,`${this.t(model.name)} · ${(model.bytes/1000000).toFixed(1)} MB`)));
    const classes=document.querySelector('#ai-classes');classes.replaceChildren();const legend=document.createElement('legend');legend.textContent=this.t('Objets à détecter');classes.append(legend);
    for(const item of this.config.classes){const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');input.type='checkbox';input.value=item.id;text.textContent=this.t(item.label);label.append(input,text);classes.append(label);}
    for(const [id,items,any] of [['#ai-event-camera',this.cameras().map(camera=>[camera.deviceId,camera.deviceName]),'Toutes les caméras'],['#ai-event-class',this.config.classes.map(item=>[item.id,this.t(item.label)]),'Tous les objets']]){
      const select=document.querySelector(id),value=select.value;select.replaceChildren(this.option('',this.t(any)),...items.map(([key,label])=>this.option(key,label)));select.value=value;
    }
  }
  fill(){
    const config=this.drafts.get(this.camera.value)||this.config?.cameras[this.camera.value];if(!config)return;
    for(const [id,key] of [['ai-enabled','enabled'],['ai-snapshots','snapshots']])document.getElementById(id).checked=config[key];
    for(const [id,key,factor] of [['ai-source','sourceMode',1],['ai-model','model',1],['ai-confidence','confidence',100],['ai-min-area','minArea',100],['ai-interval','interval',1],['ai-confirmations','confirmations',1],['ai-cooldown','cooldown',1]])document.getElementById(id).value=typeof config[key]==='number'?Math.round(config[key]*factor*100)/100:config[key];
    for(const input of document.querySelectorAll('#ai-classes input'))input.checked=config.classes.includes(input.value);
    ['left','top','right','bottom'].forEach((name,index)=>document.getElementById('ai-zone-'+name).value=Math.round(config.zone[index]*100));
    document.querySelector('#ai-status').textContent=this.t(this.drafts.has(this.camera.value)?'Enregistre les changements pour les appliquer.':config.enabled?'Analyse gérée par le serveur, même sans navigateur.':'Analyse désactivée pour cette caméra.');
  }
  readForm(){
    const value=id=>Number(document.getElementById(id).value);
    return {sourceMode:document.querySelector('#ai-source').value,enabled:document.querySelector('#ai-enabled').checked,model:document.querySelector('#ai-model').value,
      classes:[...document.querySelectorAll('#ai-classes input:checked')].map(input=>input.value),confidence:value('ai-confidence')/100,
      minArea:value('ai-min-area')/100,interval:value('ai-interval'),confirmations:value('ai-confirmations'),cooldown:value('ai-cooldown'),
      zone:['left','top','right','bottom'].map(name=>value('ai-zone-'+name)/100),snapshots:document.querySelector('#ai-snapshots').checked};
  }
  async save(){
    if(!this.camera.value)return;const button=document.querySelector('#ai-save');button.disabled=true;
    try{const device=this.camera.value,config=await this.api('/api/ai/config',{deviceId:device,config:this.readForm()});this.config.cameras[device]=config;this.drafts.delete(device);this.fill();for(const tile of this.tiles.values())if(tile.camera.deviceId===device)this.clear(tile);}
    catch(error){document.querySelector('#ai-status').textContent=error.message;}finally{button.disabled=false;}
  }
  classLabel(name){return this.t(this.config?.classes.find(item=>item.id===name)?.label || name);}
  clear(tile){tile.aiObjects=[];tile.aiLayer?.replaceChildren();if(tile.aiBadge)tile.aiBadge.hidden=true;}
  draw(tile){
    const video=tile.node.querySelector('video'),stage=tile.node.querySelector('.video-stage'),config=this.config?.cameras[tile.camera.deviceId];
    if(tile.phase!=='playing'||!config?.enabled||!video.videoWidth)return this.clear(tile);
    if(!tile.aiLayer){tile.aiLayer=document.createElement('div');tile.aiLayer.className='ai-overlay';tile.aiLayer.setAttribute('aria-hidden','true');stage.append(tile.aiLayer);tile.aiBadge=document.createElement('span');tile.aiBadge.className='ai-video-badge';stage.append(tile.aiBadge);tile.aiResize=new ResizeObserver(()=>this.draw(tile));tile.aiResize.observe(stage);}
    const bounds=stage.getBoundingClientRect(),rect=aiVideoRect(bounds.width,bounds.height,video.videoWidth,video.videoHeight);tile.aiLayer.replaceChildren();
    const place=(box,className,label)=>{const node=document.createElement('div');node.className=className;node.style.left=`${rect.x+box[0]*rect.width}px`;node.style.top=`${rect.y+box[1]*rect.height}px`;node.style.width=`${(box[2]-box[0])*rect.width}px`;node.style.height=`${(box[3]-box[1])*rect.height}px`;const text=document.createElement('span');text.textContent=label;node.append(text);tile.aiLayer.append(node);};
    if(config.zone.some((value,index)=>value!==[0,0,1,1][index]))place(config.zone,'ai-zone-overlay',this.t('Zone d’analyse'));
    for(const object of tile.aiObjects || [])place(object.box,'ai-object',`${this.classLabel(object.class)} ${Math.round(object.score*100)} % · ${this.t((tile.aiConfirmed||[]).includes(object.class)?'Confirmé':'À confirmer')}`);
    tile.aiBadge.hidden=false;tile.aiBadge.textContent=this.t('IA locale')+(tile.aiMs!==undefined?` · ${tile.aiMs} ms`:'');
  }
  async tick(){
    if(this.busy||document.hidden)return;
    this.busy=true;
    try{
      await this.load();
      for(const tile of this.tiles.values()){
        const result=this.config?.results?.[tile.camera.deviceId];
        if(tile.phase!=='playing'||!result||Date.now()-result.time*1000>8000){this.clear(tile);continue;}
        tile.aiObjects=result.objects||[];tile.aiConfirmed=result.confirmed||[];tile.aiMs=result.elapsedMs;this.draw(tile);
      }
    }finally{this.busy=false;}
  }
  renderStatus(){
    const root=document.querySelector('#ai-server-status');if(!root||!this.config)return;root.replaceChildren();
    const labels={disabled:'Analyse désactivée pour cette caméra.',waiting:'En attente d’un lecteur',connecting:'Connexion au flux local',analyzing:'Analyse en cours',retrying:'Reconnexion automatique',capacity:'Capacité du serveur atteinte'};
    for(const camera of this.cameras()){
      const worker=this.config.workers?.[camera.deviceId]||{phase:'disabled'},result=this.config.results?.[camera.deviceId];
      const card=document.createElement('article');card.className='ai-server-card';card.dataset.phase=worker.phase;
      const title=document.createElement('strong'),status=document.createElement('span'),detail=document.createElement('small');
      title.textContent=camera.deviceName;status.textContent=this.t(labels[worker.phase]||labels.retrying);
      if(result&&Date.now()-result.time*1000<8000){
        const counts=new Map();for(const object of result.objects||[])counts.set(object.class,(counts.get(object.class)||0)+1);
        detail.textContent=[...counts].map(([name,count])=>`${count} × ${this.classLabel(name)}`).join(' · ')||this.t('Aucun objet retenu');
        detail.textContent+=` · ${result.elapsedMs} ms`;
      }else detail.textContent=this.config.cameras[camera.deviceId]?.enabled?this.t('Les résultats apparaîtront ici dès qu’un flux sera analysé.') : '';
      card.append(title,status,detail);root.append(card);
    }
  }
  async refreshEvents(){try{this.events=await this.api('/api/ai/events');this.renderEvents();}catch(error){document.querySelector('#ai-events').textContent=error.message;}}
  renderEvents(){
    const root=document.querySelector('#ai-events'),device=document.querySelector('#ai-event-camera').value,name=document.querySelector('#ai-event-class').value;root.replaceChildren();
    const filtered=this.events.filter(event=>(!device||device===event.deviceId)&&(!name||name===event.class));
    const events=groupAIEvents(filtered,this.config?.cameras||{});
    const summary=document.querySelector('#ai-event-summary');if(summary)summary.textContent=`${events.length} ${this.t('séquences')} · ${filtered.length} ${this.t('observations')}`;
    if(!events.length){const empty=document.createElement('p');empty.className='ai-empty';empty.textContent=this.t('Aucun événement pour ces filtres.');root.append(empty);return;}
    for(const event of events){const row=document.createElement('article');row.className='ai-event';const title=document.createElement('strong'),camera=document.createElement('span'),date=document.createElement('time');
      title.textContent=`${this.classLabel(event.class)} · ${Math.round(event.score*100)} %`;camera.textContent=this.cameras().find(c=>c.deviceId===event.deviceId)?.deviceName || this.t('Caméra');date.dateTime=new Date(event.time*1000).toISOString();date.textContent=new Date(event.time*1000).toLocaleString(I18N.language);row.append(title,camera,date);
      const detail=document.createElement('small');detail.textContent=`${event.observations} ${this.t('observations')} · ${Math.round(event.time-event.start)} s · ${this.t('Confiance maximale')}`;row.append(detail);
      if(event.snapshot){const button=document.createElement('button');button.type='button';button.className='button secondary';button.textContent=this.t('Voir la vignette');button.onclick=()=>{document.querySelector('#ai-snapshot-image').src=`/api/ai/snapshot/${encodeURIComponent(event.id)}.jpg`;document.querySelector('#ai-snapshot-dialog').showModal();};row.append(button);}
      root.append(row);
    }
  }
  retranslate(){if(this.config && !document.querySelector('#ai-page').hidden){this.populate();this.fill();this.renderEvents();this.renderStatus();}for(const tile of this.tiles.values())if(tile.aiLayer)this.draw(tile);}
}
