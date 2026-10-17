const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const read=name=>fs.readFileSync(path.join(__dirname,'../web/',name),'utf8');
function storage(){const items=new Map();return {getItem:key=>items.get(key)||null,setItem:(key,value)=>items.set(key,value),removeItem:key=>items.delete(key)};}
function classes(){const values=new Set();return {add:value=>values.add(value),remove:value=>values.delete(value),toggle:(value,enabled)=>enabled?values.add(value):values.delete(value),contains:value=>values.has(value)};}
function displayFixture(wakeRequest=async()=>({released:false,release:async()=>{}})) {
 const localStorage=storage(),sessionStorage=storage(),events=new Map(),elements=new Map(),timers=new Map();let timerId=0,starts=[],stops=[];
 const element=()=>({classList:classes(),style:{setProperty(){}},value:'',children:[],hidden:false,append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},close(){this.open=false;},showModal(){this.open=true;}});
 const document={documentElement:{dataset:{},requestFullscreen:async()=>{document.fullscreenElement=document.documentElement;}},body:{classList:classes()},visibilityState:'visible',fullscreenElement:null,
  addEventListener:(name,fn)=>events.set(name,fn),querySelector:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);},createElement:()=>element(),exitFullscreen:async()=>{document.fullscreenElement=null;}};
 const media={matches:false,addEventListener:(_,fn)=>media.changed=fn};
 const context=vm.createContext({window:{matchMedia:()=>media},document,localStorage,sessionStorage,navigator:{wakeLock:{request:wakeRequest}},I18N:{language:'fr'},Date,
  setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),setInterval:fn=>{timers.set(++timerId,fn);return timerId;},clearInterval:id=>timers.delete(id),changeQuality:()=>{}});
 vm.runInContext(read('display.js')+'\nthis.Theme=DisplayTheme;this.Display=SentinelDisplay;',context);
 const tiles=new Map();
 for(const id of ['a','b','c']){const select={value:'sd'},node=element();node.querySelector=()=>select;tiles.set(id,{node,camera:{deviceName:id},phase:'idle',recovery:{requested:id==='a'}});}
 const display=new context.Display({tiles,start:tile=>{starts.push(tile.camera.deviceName);tile.recovery.requested=true;},stop:async tile=>{stops.push(tile.camera.deviceName);tile.recovery.requested=false;},changeView:()=>{},changePage:()=>{},translate:(key,variables={})=>key.replace(/\{(\w+)\}/g,(_,name)=>variables[name])});
 return {display,context,document,media,tiles,localStorage,sessionStorage,starts,stops,timers,elements,events};
}
test('theme supports light, dark and system changes with persisted preference',()=>{
 const {context,document,media,localStorage}=displayFixture();
 context.Theme.apply('dark');assert.equal(document.documentElement.dataset.theme,'dark');assert.equal(localStorage.getItem('tuya-local-theme'),'dark');
 context.Theme.apply('system');media.matches=true;media.changed();assert.equal(document.documentElement.dataset.theme,'dark');
 context.Theme.apply('light');media.changed();assert.equal(document.documentElement.dataset.theme,'light');
});
test('sentinel rejects empty selection and restores only previously requested cameras on exit',async()=>{
 const f=displayFixture();assert.equal(f.display.enter({cameras:[]}),false);
 assert.equal(f.display.enter({cameras:['b','c'],mode:'grid',quality:'sd'}),true);
 assert.deepEqual(f.starts,['b','c']);assert.deepEqual(f.stops,['a']);
 assert.equal(f.document.body.classList.contains('sentinel'),true);
 assert.equal(f.tiles.get('b').node.classList.contains('sentinel-visible'),true);
 assert.equal(f.sessionStorage.getItem('tuya-local-sentinel-active'),'1');
 f.display.exit();assert.deepEqual(f.stops,['a','b','c']);assert.deepEqual(f.starts,['b','c','a']);
 assert.equal(f.document.body.classList.contains('sentinel'),false);assert.equal(f.sessionStorage.getItem('tuya-local-sentinel-active'),null);
 await new Promise(resolve=>setImmediate(resolve));
});
test('rotation changes the visible camera without restarting the video sessions',()=>{
 const f=displayFixture();f.display.enter({cameras:['b','c'],mode:'rotation',interval:10});
 assert.equal(f.tiles.get('b').node.classList.contains('sentinel-visible'),true);
 assert.equal(f.tiles.get('c').node.classList.contains('sentinel-visible'),false);
 f.display.next();assert.equal(f.tiles.get('c').node.classList.contains('sentinel-visible'),true);
 assert.deepEqual(f.starts,['b','c']);assert.equal(f.display.saved.interval,10);f.display.exit();
});
test('a wake lock acquired after exit is released instead of leaving the screen awake',async()=>{
 let resolveLock,releases=0;const lock=new Promise(resolve=>resolveLock=resolve);
 const f=displayFixture(()=>lock);f.display.enter({cameras:['b'],mode:'grid'});f.display.exit();
 resolveLock({released:false,release:async()=>{releases++;}});await new Promise(resolve=>setImmediate(resolve));assert.equal(releases,1);
});
test('tab reload resumes the saved sentinel selection without requesting fullscreen',()=>{
 const f=displayFixture();f.display.saved={cameras:['b'],mode:'grid',quality:'sd'};f.sessionStorage.setItem('tuya-local-sentinel-active','1');
 f.display.restore();assert.equal(f.display.active,true);assert.deepEqual(f.starts,['b']);assert.equal(f.document.fullscreenElement,null);f.display.exit();
});
test('recovery backs off, stops on user cancellation and resets only after stable playback',()=>{
 const callbacks=new Map();let id=0,restarts=0;const waits=[];
 const context=vm.createContext({});vm.runInContext(read('recovery.js')+'\nthis.Recovery=PlaybackRecovery;',context);
 const timers={setTimeout:fn=>{callbacks.set(++id,fn);return id;},clearTimeout:id=>callbacks.delete(id)};
 const recovery=new context.Recovery(()=>restarts++,seconds=>waits.push(seconds),timers);
 recovery.begin();for(let attempt=0;attempt<7;attempt++){recovery.failed();const fn=callbacks.get(recovery.timer);callbacks.delete(recovery.timer);fn();}
 assert.deepEqual(waits,[3,6,12,24,48,60,60]);assert.equal(restarts,7);
 recovery.failed();recovery.stop();assert.equal(callbacks.size,0);recovery.now();assert.equal(restarts,7);
 recovery.begin();recovery.failed();const fn=callbacks.get(recovery.timer);fn();recovery.playing();assert.equal(recovery.attempts,1);callbacks.get(recovery.stable)();assert.equal(recovery.attempts,0);
});
