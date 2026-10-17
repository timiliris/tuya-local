'use strict';
const t = (key, variables) => I18N.t(key, variables);
const $ = (selector, root = document) => root.querySelector(selector);
const tiles = new Map();
let accessChanging = false;
let state = null, focused = null, view = 'grid', toastTimer, currentPage = 'cameras';
const icon = name => `<svg aria-hidden="true"><use href="#${name}"/></svg>`;
function toast(message) { const node = $('#toast'); node.textContent = message; node.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => node.hidden = true, 5000); }
async function api(path, body, options = {}) {
  const controller=new AbortController();
  const abort=()=>controller.abort();options.signal?.addEventListener('abort',abort,{once:true});
  if(options.signal?.aborted)controller.abort();
  const timeout=setTimeout(abort,path==='/api/access/password'?30000:path.startsWith('/api/settings/')?100000:path==='/api/player/offer'?115000:10000);
  try {
    const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers: {'Content-Type':'application/json'}, body: body === undefined ? undefined : JSON.stringify(body),signal:controller.signal});
    if(response.status===401){if(!accessChanging)location.replace('/');throw new Error(t('Session expirée. Reconnectez-vous.'));}
    const result = await response.json();
    if (!response.ok) throw new Error(t(result.error) || t('La demande a échoué.'));
    return result;
  } catch(error) {if(error.name==='AbortError' && !options.signal?.aborted)throw new Error(t('Le serveur ne répond pas.'));throw error;} finally {clearTimeout(timeout);options.signal?.removeEventListener('abort',abort);}
}
function changePage(page) {
  currentPage = page;
  $('#camera-page').hidden = page !== 'cameras'; $('#diagnostics-page').hidden = page !== 'diagnostics'; $('#ai-page').hidden=page!=='ai';
  $('#nav-cameras').classList.toggle('active',page === 'cameras'); $('#nav-diagnostics').classList.toggle('active',page === 'diagnostics'); $('#nav-ai').classList.toggle('active',page==='ai');
  $('#breadcrumb').textContent = page === 'cameras' ? t('Caméras') : page==='ai'?t('IA locale'):t('État du serveur');
}
function changeView(mode, id = focused) {
  view = mode; focused = id || tiles.keys().next().value;
  $('.camera-layout').classList.toggle('focus',mode === 'focus');
  $('#camera-detail').hidden = mode !== 'focus' || !focused;
  for (const [key, tile] of tiles) { tile.node.classList.toggle('focused',key === focused); tile.nav.classList.toggle('selected',mode === 'focus' && key === focused); }
  $('#grid-view').classList.toggle('selected',mode === 'grid'); $('#focus-view').classList.toggle('selected',mode === 'focus');
  $('#grid-view').setAttribute('aria-pressed',mode === 'grid'); $('#focus-view').setAttribute('aria-pressed',mode === 'focus');
  $('#detail-name').textContent = tiles.get(focused)?.camera.deviceName || t('Caméra');
  updateControls();
}
function status(tile, phase, message = '') {
  tile.phase = phase;
  if(phase!=='playing')tile.aiLayer?.replaceChildren();
  tile.message = I18N.source(message);
  message = t(tile.message);
  for (const name of ['loading','playing','error']) tile.node.classList.toggle(name,phase === name);
  const playing = phase === 'playing', busy = ['loading','retrying'].includes(phase);
  tile.node.classList.toggle('loading',busy);
  $('.stage-title', tile.node).textContent = phase === 'retrying' ? t('Reconnexion automatique…') : busy ? t('Connexion à la caméra…') : phase === 'error' ? t('La vidéo n’a pas démarré') : t('Prête à regarder');
  $('.stage-message', tile.node).textContent = message || (busy ? t('Réveil automatique en cours. Cela peut prendre jusqu’à 90 secondes.') : t('La caméra reste au repos tant que tu ne l’ouvres pas.'));
  $('.tile-state', tile.node).textContent = playing ? t(tile.transport === 'webrtc' ? 'En direct · WebRTC' : 'En direct · Compatibilité') : busy ? t('Réveil…') : phase === 'error' ? t('À relancer') : t('À la demande');
  $('.tile-dot',tile.node).className = `tile-dot dot${playing ? ' green' : busy ? ' amber' : ''}`;
  $('.video-label',tile.node).hidden = !playing;
  $('.video-label span:last-child',tile.node).textContent = t(tile.transport === 'webrtc' ? 'En direct · WebRTC' : 'En direct · Compatibilité'); $('.video-fullscreen',tile.node).hidden = !playing;
  $('.tile-toggle',tile.node).innerHTML = icon(playing || busy ? 'stop' : 'play') + `<span>${playing || busy ? t('Arrêter') : t('Regarder')}</span>`;
  $('.stage-play',tile.node).innerHTML = icon('play') + (phase === 'error' ? t('Réessayer') : t('Regarder'));
  $('.tile-capture',tile.node).disabled = !playing;
  updateControls();
}
function updateControls() {
  const active = [...tiles.values()].filter(tile => ['playing','loading','retrying'].includes(tile.phase)).length;
  $('#live-count').textContent = active ? t(active === 1 ? '{count} lecture ouverte' : '{count} lectures ouvertes', {count:active}) : t('Aucune lecture');
  $('#stop-all').disabled = !active;
  $('#detail-capture').disabled = tiles.get(focused)?.phase !== 'playing';
  $('#detail-copy').disabled = !tiles.has(focused);
  for (const button of $('#ptz-pad').querySelectorAll('button')) button.disabled = (typeof ptzBusy !== 'undefined' && ptzBusy) || tiles.get(focused)?.phase !== 'playing';
}
async function stop(tile, repaint = true, preserveIntent = false) {
  const generation = ++tile.generation;
  if(!preserveIntent)tile.recovery?.stop();
  clearInterval(tile.watchdog);clearTimeout(tile.sentinelStart);
  tile.abort?.abort(); tile.abort = null;
  clearInterval(tile.heartbeat); clearTimeout(tile.timeout);
  tile.rtc?.close(); tile.rtc = null;
  const token = tile.token; tile.token = null;
  const video = $('video',tile.node); video.onerror = null; video.onplaying = null; video.oncanplay = null; video.onclick = null; video.pause(); video.srcObject = null; video.removeAttribute('src'); video.load();
  if (tile.objectUrl) { URL.revokeObjectURL(tile.objectUrl); tile.objectUrl = null; }
  if (token) { try { await api('/api/player/stop', {token}); } catch {} }
  if (repaint && generation===tile.generation) status(tile,'idle');
}
function bufferOperation(buffer, operation, signal) {
  return new Promise((resolve, reject) => {
    const cleanup = () => {buffer.removeEventListener('updateend', done); buffer.removeEventListener('error', fail); signal.removeEventListener('abort', aborted);};
    const done = () => {cleanup(); resolve();};
    const fail = () => {cleanup(); reject(new Error(t('Le navigateur ne peut pas lire ce flux. Essaie le lien VLC.')));};
    const aborted = () => {cleanup(); reject(new DOMException('Arrêt','AbortError'));};
    if (signal.aborted) return aborted();
    buffer.addEventListener('updateend',done,{once:true}); buffer.addEventListener('error',fail,{once:true}); signal.addEventListener('abort',aborted,{once:true});
    try {operation();} catch(error) {cleanup(); reject(error);}
  });
}
async function start(tile, forceCompatibility = false, automatic = false) {
  tile.recovery?.begin(!automatic);
  const generation = tile.generation + 1;
  await stop(tile,false,true);
  if(generation!==tile.generation)return;
  forceCompatibility ||= tile.compatibility?.has($('select',tile.node).value) || false;
  if (!forceCompatibility && LocalWebRTC.supported()) {
    status(tile,'loading'); tile.transport = 'webrtc';
    tile.abort = new AbortController();
    let fallingBack = false;
    const fallback = () => {
      if (fallingBack || tile.generation !== generation || tile.abort?.signal.aborted) return;
      fallingBack = true;
      toast(t('WebRTC indisponible. Passage au lecteur de secours.'));
      tile.compatibility?.add($('select',tile.node).value);
      start(tile,true,true);
    };
    try {
      const session = await api('/api/player/start',{deviceId:tile.camera.deviceId,quality:$('select',tile.node).value,transport:'webrtc'});
      if (tile.generation !== generation) {await api('/api/player/stop',{token:session.token});return;}
      tile.token = session.token;
      tile.heartbeat = setInterval(() => api('/api/player/heartbeat',{token:session.token}).catch(() => failPlayer(tile,generation)),10000);
      const rtc = new LocalWebRTC(); tile.rtc = rtc;const rtcSignal=tile.abort.signal;
      tile.timeout = setTimeout(fallback,120000);
      await rtc.play($('video',tile.node),sdp => api('/api/player/offer',{token:session.token,sdp},{signal:rtcSignal}),rtcSignal,()=>tile.phase==='playing'?failPlayer(tile,generation):fallback());
      if (tile.generation === generation) {clearTimeout(tile.timeout);status(tile,'playing');monitorPlayer(tile,generation);}
    } catch(error) {if(error.name !== 'AbortError') fallback();}
    return;
  }
  tile.transport = 'mp4';
  if (!window.MediaSource || !MediaSource.isTypeSupported('video/mp4; codecs="avc1.42E033"')) {tile.recovery?.stop();return status(tile,'error',t('Ce navigateur ne prend pas en charge le lecteur. Utilise le lien VLC dans la vue détaillée.'));}
  status(tile,'loading');
  tile.abort = new AbortController(); const signal = tile.abort.signal;
  try {
    const quality = $('select',tile.node).value;
    const session = await api('/api/player/start',{deviceId:tile.camera.deviceId,quality});
    if (tile.generation !== generation) { await api('/api/player/stop',{token:session.token}); return; }
    tile.token = session.token;
    tile.heartbeat = setInterval(() => api('/api/player/heartbeat',{token:session.token}).catch(() => failPlayer(tile,generation)),10000);
    tile.timeout = setTimeout(() => failPlayer(tile,generation),110000);
    const media = new MediaSource(); const video = $('video',tile.node);
    tile.objectUrl = URL.createObjectURL(media); video.src = tile.objectUrl;
    await new Promise((resolve,reject) => {media.addEventListener('sourceopen',resolve,{once:true}); signal.addEventListener('abort',() => reject(new DOMException('Arrêt','AbortError')),{once:true});});
    if (signal.aborted) return;
    const buffer = media.addSourceBuffer('video/mp4; codecs="avc1.42E033"');
    const response = await fetch(session.url,{signal});
    if (!response.ok) throw new Error(t('Le lecteur a expiré. Relance la vidéo.'));
    const reader = response.body.getReader();
    video.onplaying = () => {if (generation === tile.generation) {clearTimeout(tile.timeout); status(tile,'playing');monitorPlayer(tile,generation);}};
    video.oncanplay = () => video.play().catch(() => toast(t('Clique sur la vidéo pour autoriser la lecture.')));
    video.onclick = () => video.play().catch(() => {});
    while (true) {
      const {done,value} = await reader.read();
      if (done) break;
      await bufferOperation(buffer,()=>buffer.appendBuffer(value),signal);
      if (buffer.buffered.length) {
        const end = buffer.buffered.end(buffer.buffered.length - 1);
        if (video.currentTime < end - 4) video.currentTime = Math.max(buffer.buffered.start(0),end - 1.2);
        const cutoff = video.currentTime - 15;
        if (cutoff > buffer.buffered.start(0) + 3 && !buffer.updating) {
          await bufferOperation(buffer,()=>buffer.remove(0,cutoff),signal);
        }
      }
    }
    if (!signal.aborted) throw new Error(t('La caméra a interrompu la vidéo. Tu peux relancer la lecture.'));
  } catch (error) {
    if (generation !== tile.generation || error.name === 'AbortError') return;
    await failPlayer(tile,generation);
  }
}
async function failPlayer(tile,generation) {
  if(generation!==tile.generation || tile.failing===generation)return;
  tile.failing=generation;
  await stop(tile,false,true);
  if(tile.generation!==generation+1)return;
  if(tile.recovery?.requested)tile.recovery.failed();
  else status(tile,'error',t('La caméra a interrompu la vidéo. Tu peux relancer la lecture.'));
}
function monitorPlayer(tile,generation) {
  clearInterval(tile.watchdog);tile.recovery?.playing();
  const video=$('video',tile.node);let position=video.currentTime,progress=Date.now();
  video.onerror=()=>failPlayer(tile,generation);
  tile.watchdog=setInterval(()=>{
    if(tile.generation!==generation)return;
    if(video.currentTime>position){position=video.currentTime;progress=Date.now();}
    else if(Date.now()-progress>15000)failPlayer(tile,generation);
  },3000);
}
async function changeQuality(tile) {
  if (!['loading','playing','retrying'].includes(tile.phase)) return;
  const generation = tile.generation + 1;
  await stop(tile,false,true);
  if (generation !== tile.generation) return;
  status(tile,'loading',t('Changement de qualité…'));
  // Allow the camera's previous session to close before opening another quality.
  tile.timeout = setTimeout(() => {if (tile.generation === generation) start(tile);},5500);
}
function capture(tile) {
  const video = $('video',tile.node);
  if (!video.videoWidth || video.readyState < 2) return toast(t('Attends que la première image soit affichée.'));
  const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video,0,0);
  canvas.toBlob(blob => {if (!blob) return toast(t('La capture a échoué.')); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `${tile.camera.deviceName.replace(/[^\p{L}\p{N} _-]/gu,'_')}-${new Date().toISOString().replace(/[:.]/g,'-')}.jpg`; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000); toast(t('Capture téléchargée.'));},'image/jpeg',.94);
}
function addCamera(camera) {
  const node = $('#camera-template').content.firstElementChild.cloneNode(true);
  $('.tile-settings',node).onclick=()=>settingsPanel.open(camera.deviceId);
  const nav = document.createElement('button'); nav.className = 'camera-nav-button'; nav.innerHTML = '<span class="dot"></span><span></span>';
  $('span:last-child',nav).textContent = camera.deviceName; nav.onclick = () => {changePage('cameras'); changeView('focus',camera.deviceId);};
  const tile = {camera,node,nav,phase:'idle',generation:0,compatibility:new Set()};
  tile.recovery=new PlaybackRecovery(()=>start(tile,false,true),seconds=>status(tile,'retrying',t('Nouvelle tentative dans {seconds} s.',{seconds})));
  tiles.set(camera.deviceId,tile);
  $('h2',node).textContent = camera.deviceName;
  $('.tile-focus',node).onclick = () => changeView('focus',camera.deviceId);
  $('.tile-toggle',node).onclick = () => ['loading','playing','retrying'].includes(tile.phase) ? stop(tile) : start(tile);
  $('.stage-play',node).onclick = () => start(tile);
  $('.tile-capture',node).onclick = () => capture(tile);
  $('.video-fullscreen',node).onclick = () => $('.video-stage',node).requestFullscreen().catch(() => toast(t('Le plein écran n’est pas disponible.')));
  $('select',node).onchange = () => changeQuality(tile);
  I18N.apply(node);
  $('#camera-grid').append(node); $('#camera-nav').append(nav); status(tile,'idle');
}
function render(next) {
  state = next;
  const ids = new Set(next.cameras.map(camera => camera.deviceId));
  for (const [id,tile] of tiles) if (!ids.has(id)) {stop(tile); tile.aiResize?.disconnect(); tile.node.remove(); tile.nav.remove(); tiles.delete(id);}
  for (const camera of next.cameras) if (!tiles.has(camera.deviceId)) addCamera(camera); else {const tile=tiles.get(camera.deviceId); tile.camera=camera; $('h2',tile.node).textContent=camera.deviceName; $('span:last-child',tile.nav).textContent=camera.deviceName;}
  $('#nav-count').textContent = next.cameras.length;
  $('#page-subtitle').textContent = next.cameras.length ? t(next.cameras.length === 1 ? '{count} caméra connectée au compte. Choisis celles que tu veux regarder.' : '{count} caméras connectées au compte. Choisis celles que tu veux regarder.',{count:next.cameras.length}) : t('Retrouve tes caméras en connectant ton compte Tuya.');
  $('#empty').hidden = next.cameras.length > 0; $('.camera-layout').hidden = !next.cameras.length;
  $('#account-label').textContent = next.loggedIn ? t('Compte Tuya connecté') : t('Compte non connecté');
  $('#server-label').textContent = next.engineRunning ? t('Bridge disponible') : t('Bridge arrêté');
  $('#server-dot').className = `dot ${next.engineRunning ? 'green' : 'amber'}`;
  $('#account-connected').hidden = !next.loggedIn; $('#qr-controls').hidden = next.loggedIn;
  $('#account-description').hidden = next.loggedIn;
  const options = Object.entries(next.regions || {}); const region = $('#region');
  if (options.length && (region.options.length !== options.length || region.dataset.language !== I18N.language)) { region.replaceChildren(...options.map(([value,info]) => new Option(t(({we:'Europe de l’Ouest',eu:'Europe centrale',us:'États-Unis',ue:'États-Unis',cn:'Chine',in:'Inde'})[value] || info.label || value),value))); region.value = next.region || 'we'; region.dataset.language=I18N.language; }
  if (next.hasQr) {$('#qr-area').hidden = false; const src = `/api/qr.png?id=${encodeURIComponent(next.qrId || '')}`; if ($('#qr-image').getAttribute('src') !== src) $('#qr-image').src = src;}
  if (next.loggedIn && $('#account-dialog').open && $('#qr-area').hidden === false) {$('#account-dialog').close(); toast(t('Compte connecté. Tes caméras sont disponibles.')); $('#qr-area').hidden=true;}
  const diagnostics = [[t('Compte Tuya'),next.loggedIn ? t('Connecté') : t('À connecter')],[t('Moteur vidéo'),next.engineRunning ? t('Disponible') : t('Arrêté')],[t('Caméras du compte'),String(next.cameras.length)],[t('Transport vidéo'),t('Local uniquement')],[t('Réveil automatique'),t('Jusqu’à 3 tentatives')],[t('Lecteur web'),t('WebRTC local, avec lecteur de secours')]];
  $('#diagnostics').replaceChildren(...diagnostics.map(([label,value]) => {const row=document.createElement('div');row.className='diagnostic-row';const name=document.createElement('span');name.textContent=label;const result=document.createElement('strong');result.textContent=value;row.append(name,result);return row;}));
  changeView(view,tiles.has(focused) ? focused : tiles.keys().next().value);
  if(next.cameras.length)sentinel.restore();
  if(sentinel.active)sentinel.layout();
}
let polling=false;
async function poll() {if(polling || accessChanging)return;polling=true;try {render(await api('/api/state'));} catch {$('#server-label').textContent=t('Bridge injoignable'); $('#server-dot').className='dot amber'; $('#page-subtitle').textContent=t('Impossible de joindre le bridge. Vérifie que le serveur Docker est démarré.');}finally{polling=false;} }
$('#nav-ai').onclick=()=>{changePage('ai');localAI.open();};
$('#nav-cameras').onclick = () => changePage('cameras'); $('#nav-diagnostics').onclick = () => changePage('diagnostics');
$('#grid-view').onclick = () => changeView('grid'); $('#focus-view').onclick = () => changeView('focus');
$('#stop-all').onclick = () => {for (const tile of tiles.values()) stop(tile);};
$('#detail-settings').onclick=()=>{if(focused)settingsPanel.open(focused);};
$('#detail-capture').onclick = () => {const tile=tiles.get(focused); if(tile) capture(tile);};
$('#detail-copy').onclick = async () => {const tile=tiles.get(focused); if(!tile) return; const quality=$('select',tile.node).value; try {await navigator.clipboard.writeText(quality==='hd' ? tile.camera.rtspHd : tile.camera.rtspSd); toast(t('Lien VLC copié.'));} catch {toast(t('Le navigateur n’a pas autorisé la copie.'));}};
$('#refresh').onclick = async () => {if ([...tiles.values()].some(tile => ['playing','loading','retrying'].includes(tile.phase))) return toast(t('Arrête les lectures avant de rechercher les caméras.')); const button=$('#refresh');button.disabled=true;try {render(await api('/api/cameras/refresh',{}));toast(t('Liste des caméras actualisée.'));}catch(error){toast(error.message);}finally{button.disabled=false;}};
function openAccount() {$('#account-dialog').showModal();}
$('#account-button').onclick=openAccount; $('#empty-connect').onclick=openAccount; $('#close-dialog').onclick=() => $('#account-dialog').close();
$('#create-qr').onclick=async () => {const button=$('#create-qr');button.disabled=true;try {render(await api('/api/qr/start',{region:$('#region').value}));}catch(error){toast(error.message);}finally{button.disabled=false;}};
let ptzBusy = false;
async function ptz(direction) {
  const device = focused;
  if (!device || ptzBusy) return;
  ptzBusy = true;
  $('#ptz-status').textContent = t('Commande en cours…');
  for (const button of $('#ptz-pad').querySelectorAll('button')) button.disabled = true;
  try {
    const result = await api('/api/ptz/move',{deviceId:device,direction});
    $('#ptz-status').textContent = result.response?.via === 'lan' ? t('Commande envoyée sur le réseau local.') : t('Commande acceptée par Tuya. La vidéo reste locale.');
  } catch(error) {$('#ptz-status').textContent=error.message;}
  finally {ptzBusy=false;updateControls();}
}
for (const button of $('#ptz-pad').querySelectorAll('button')) button.onclick = () => ptz(button.dataset.direction);
window.addEventListener('pagehide',() => {for(const tile of tiles.values()){tile.recovery.stop();if(tile.token) navigator.sendBeacon('/api/player/stop',new Blob([JSON.stringify({token:tile.token})],{type:'application/json'}));}sentinel.wakeLock?.release().catch(()=>{});});
$('#language').onchange = async event => {
  await I18N.setLanguage(event.target.value);
  if (state) render(state);
  for (const tile of tiles.values()) status(tile,tile.phase,tile.message || '');
  changePage(currentPage);
  if(settingsPanel.dialog.open)settingsPanel.render();
  localAI.retranslate();
  if(serverSettings.dialog.open)serverSettings.render();
  $('#ptz-status').textContent=t('Disponibilité selon le modèle. Le pilotage peut utiliser le cloud Tuya.');
};
const localAI=new LocalAIConsole({api,tiles,translate:t,cameras:()=>state?.cameras || []});
const settingsPanel=new CameraSettings.Panel({api,cameras:()=>state?.cameras || [],translate:t});
const sentinel=new SentinelDisplay({tiles,start,stop,changeView,changePage,translate:t});
$('#theme').value=DisplayTheme.preference;$('#theme').onchange=event=>DisplayTheme.apply(event.target.value);
$('#sentinel-open').onclick=()=>{ $('#sentinel-error').hidden=true;sentinel.configure();};
$('#sentinel-close').onclick=()=>$('#sentinel-dialog').close();
$('#sentinel-exit').onclick=()=>sentinel.exit();
$('#sentinel-next').onclick=()=>sentinel.next();
$('#sentinel-fullscreen').onclick=()=>sentinel.fullscreenScreen();
$('#sentinel-form').onsubmit=event=>{event.preventDefault();const cameras=[...$('#sentinel-cameras').querySelectorAll('input:checked')].map(input=>input.value);$('#sentinel-error').hidden=sentinel.enter({cameras,mode:$('#sentinel-mode').value,interval:Number($('#sentinel-interval').value),quality:$('#sentinel-quality').value});};
window.addEventListener('online',()=>{poll();for(const tile of tiles.values())tile.recovery.now();});
I18N.ready.then(() => {$('#language').value=I18N.language; localAI.initialize(); poll(); setInterval(poll,4000);});

const logoutButton=$('#access-logout');
logoutButton.onclick=async()=>{logoutButton.disabled=true;try{await Promise.all([...tiles.values()].map(tile=>stop(tile)));await api('/api/access/logout',{});location.replace('/');}catch(error){logoutButton.disabled=false;toast(error.message);}};
fetch('/api/access').then(response=>response.json()).then(access=>{logoutButton.hidden=!access.enabled;}).catch(()=>{});

const serverSettings=new ServerSettings({api,translate:t,onSaving:value=>{accessChanging=value;},beforeSave:async()=>{if(sentinel.active)sentinel.exit();await Promise.all([...tiles.values()].map(tile=>stop(tile)));},onSaved:()=>{$('#access-logout').hidden=false;}});
