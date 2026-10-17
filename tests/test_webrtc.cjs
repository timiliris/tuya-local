const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../web/webrtc.js'),'utf8');
function fixture({frames=true}={}) {
  const timers=new Map(); let timerId=0,configuration;
  class Peer extends EventTarget {
    constructor(config){super();configuration=config;this.iceGatheringState='complete';this.connectionState='new';}
    addTransceiver(kind,options){assert.equal(kind,'video');assert.equal(options.direction,'recvonly');}
    async createOffer(){return {type:'offer',sdp:'local-offer'};}
    async setLocalDescription(offer){this.localDescription=offer;}
    async setRemoteDescription(answer){assert.equal(answer.sdp,'local-answer');if(frames)queueMicrotask(()=>this.ontrack({track:{kind:'video'}}));}
    async getStats(){return new Map([['video',{type:'inbound-rtp',kind:'video',framesDecoded:10}]]);}
    close(){this.connectionState='closed';this.onconnectionstatechange?.();}
  }
  const context=vm.createContext({RTCPeerConnection:Peer,MediaStream:class{constructor(tracks){this.tracks=tracks;}},DOMException,Date,
    setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),
    setInterval:fn=>{timers.set(++timerId,fn);return timerId;},clearInterval:id=>timers.delete(id)});
  vm.runInContext(source+'\nthis.Player=LocalWebRTC;',context);
  const player=new context.Player();
  const video={videoWidth:640,play:async()=>{video.onplaying?.();}};
  return {player,video,timers,configuration};
}
test('local WebRTC negotiates video without STUN/TURN and waits for an actual image',async()=>{
 const {player,video,timers,configuration}=fixture();
 assert.equal(configuration.iceServers.length,0);
 let exchanged=false;
 const codec=await player.play(video,async sdp=>{assert.equal(sdp,'local-offer');exchanged=true;return {sdp:'local-answer',codec:'video/H264'};},new AbortController().signal,()=>assert.fail('unexpected fallback'));
 assert.equal(codec,'video/H264');assert.ok(exchanged);assert.equal(video.srcObject.tracks[0].kind,'video');
 player.close();assert.equal(timers.size,0);
});
test('no decoded image triggers fallback and cleanup',async()=>{
 const {player,video,timers}=fixture({frames:false});let failures=0;
 const result=player.play(video,async()=>({sdp:'local-answer'}),new AbortController().signal,()=>failures++);
 await new Promise(resolve=>setImmediate(resolve));
 for(const callback of [...timers.values()]) callback();
 await assert.rejects(result,/No decoded WebRTC video/);
 assert.equal(failures,1);assert.equal(player.closed,true);assert.equal(timers.size,0);
});
test('stopping during signaling closes the peer without starting fallback',async()=>{
 const {player,video,timers}=fixture();const controller=new AbortController();let answer;
 const exchange=new Promise(resolve=>answer=resolve);
 const result=player.play(video,()=>exchange,controller.signal,()=>assert.fail('cancel should not trigger fallback'));
 await new Promise(resolve=>setImmediate(resolve));controller.abort();answer({sdp:'local-answer'});
 await assert.rejects(result,{name:'AbortError'});assert.equal(player.closed,true);assert.equal(timers.size,0);
});
test('failed connection after playback requests the fallback',async()=>{
 const {player,video}=fixture();let failures=0;
 await player.play(video,async()=>({sdp:'local-answer'}),new AbortController().signal,()=>failures++);
 player.peer.connectionState='failed';player.peer.onconnectionstatechange();
 assert.equal(failures,1);player.close();
});
test('the integrated player restarts in compatibility mode when WebRTC is rejected',async()=>{
 const app=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
 const startFunction=app.slice(app.indexOf('async function start('),app.indexOf('\nfunction capture('));
 const requests=[];const select={value:'hd'};const tile={generation:0,camera:{deviceId:'camera'},node:{}};
 class FallbackMedia {static isTypeSupported(){return true;} addEventListener(){}}
 const context=vm.createContext({LocalWebRTC:class{static supported(){return true;}async play(){throw new Error('unsupported HEVC');}},
  stop:async tile=>{tile.generation++;},status:()=>{},t:x=>x,toast:()=>{},$:selector=>selector==='video'?{}:select,
  api:async (url,body)=>{requests.push({url,body});return {token:'token'};},
  AbortController,setInterval:()=>1,clearTimeout:()=>{},setTimeout:()=>1,
  window:{MediaSource:FallbackMedia},MediaSource:FallbackMedia,URL:{createObjectURL:()=>'/local-fallback'}});
 vm.runInContext(startFunction+'\nthis.start=start;',context);
 await context.start(tile);await new Promise(resolve=>setImmediate(resolve));
 assert.equal(tile.transport,'mp4');assert.equal(requests[0].body.transport,'webrtc');
 assert.equal(requests[1].url,'/api/player/start');assert.equal(requests[1].body.quality,'hd');assert.equal(requests[1].body.transport,undefined);
});
test('quality changes let the old camera session close and respect cancellation',async()=>{
 const app=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
 const code=app.slice(app.indexOf('async function changeQuality('),app.indexOf('\nfunction capture('));
 const callbacks=[];let starts=0;
 const context=vm.createContext({stop:async tile=>{tile.generation++;},status:()=>{},t:x=>x,setTimeout:(fn,delay)=>{assert.equal(delay,5500);callbacks.push(fn);return 1;},start:()=>starts++});
 vm.runInContext(code+'\nthis.changeQuality=changeQuality;',context);
 const tile={phase:'playing',generation:1};
 await context.changeQuality(tile);assert.equal(starts,0);callbacks.pop()();assert.equal(starts,1);
 await context.changeQuality(tile);tile.generation++;callbacks.pop()();assert.equal(starts,1);
});
test('a brief WebRTC disconnect has a grace period and does not restart a recovered connection',async()=>{
 const {player,video,timers}=fixture();let failures=0;
 await player.play(video,async()=>({sdp:'local-answer'}),new AbortController().signal,()=>failures++);
 player.peer.connectionState='disconnected';player.peer.onconnectionstatechange();
 const grace=player.disconnectTimer;assert.ok(timers.has(grace));assert.equal(failures,0);
 player.peer.connectionState='connected';player.peer.onconnectionstatechange();assert.equal(timers.has(grace),false);
 player.close();
});
