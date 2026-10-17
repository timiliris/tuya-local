const test=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');const source=fs.readFileSync(require('node:path').join(__dirname,'../web/ai.js'),'utf8');
function fixture(api=async()=>({objects:[]})){
 const elements=new Map(),tiles=new Map();let captures=0;
 const element=()=>({value:'',hidden:false,children:[],textContent:'',style:{},classList:{},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];},setAttribute(){},getBoundingClientRect(){return {width:640,height:360};}});
 const document={hidden:false,addEventListener(){},querySelector:id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);},querySelectorAll:()=>[],createElement:type=>type==='canvas'?{getContext:()=>({drawImage(){captures++;}}),toDataURL:()=> 'data:image/jpeg;base64,frame'}:element()};
 const context=vm.createContext({document,window:{addEventListener(){}},Date,ResizeObserver:class{observe(){}},I18N:{language:'en'},setInterval:()=>1,clearInterval(){}});
 vm.runInContext(source+'\nthis.AI=LocalAIConsole;this.rect=aiVideoRect;this.group=groupAIEvents;',context);
 const ai=new context.AI({api,tiles,translate:key=>key,cameras:()=>[]});
 const video={videoWidth:640,videoHeight:360,readyState:2},stage=element();
 const tile={camera:{deviceId:'camera'},generation:1,token:'live-token',phase:'playing',node:{querySelector:selector=>selector==='video'?video:stage}};tiles.set('camera',tile);
 ai.config={cameras:{camera:{enabled:false,interval:2,zone:[0,0,1,1]}},classes:[{id:'person',label:'Personne'}]};
 return {ai,tile,document,context,captures:()=>captures};
}
test('browser only polls results and never captures or uploads images',async()=>{
 let calls=[];const f=fixture(async(path,body)=>{calls.push({path,body});return {cameras:{},results:{},workers:{}};});
 await f.ai.tick();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/ai/state');assert.equal(calls[0].body,undefined);assert.equal(f.captures(),0);
 f.document.hidden=true;await f.ai.tick();assert.equal(calls.length,1);
});
test('fresh server results draw on a live video and stale results disappear',async()=>{
 const state={cameras:{camera:{enabled:true,zone:[0,0,1,1]}},classes:[],results:{camera:{objects:[{class:'person',score:.9,box:[.1,.1,.5,.9]}],time:Date.now()/1000,elapsedMs:30}},workers:{}};
 const f=fixture(async()=>state);await f.ai.tick();assert.equal(f.tile.aiObjects.length,1);assert.equal(f.tile.aiMs,30);assert.equal(f.captures(),0);
 state.results.camera.time-=30;await f.ai.tick();assert.equal(f.tile.aiObjects.length,0);
});
test('stopping a player while polling discards the overlay',async()=>{
 let resolve;const f=fixture(()=>new Promise(r=>resolve=r));
 const running=f.ai.tick();f.tile.phase='idle';resolve({cameras:{},workers:{},results:{camera:{objects:[{}],time:Date.now()/1000}}});await running;
 assert.equal(f.tile.aiObjects.length,0);assert.equal(f.ai.busy,false);
});
test('overlay accounts for letterboxing instead of stretching coordinates',()=>{
 const f=fixture();let rect=f.context.rect(640,360,360,640);
 assert.equal(rect.y,0);assert.equal(rect.height,360);assert.ok(rect.x>200);
 rect=f.context.rect(640,480,1920,1080);assert.equal(rect.x,0);assert.equal(rect.y,60);assert.equal(rect.width,640);assert.equal(rect.height,360);
});
test('inference failures release the browser scheduler for other cameras',async()=>{
 const f=fixture(async()=>{throw new Error('offline');});f.ai.config.cameras.camera.enabled=true;
 await f.ai.tick();assert.equal(f.ai.busy,false);assert.deepEqual([...f.tile.aiObjects],[]);
});

test('history groups nearby observations per camera and class, with peak evidence',()=>{
 const f=fixture(),base={deviceId:'a',class:'person',box:[0,0,1,1],snapshot:true};
 const groups=f.context.group([{...base,id:'new',time:100,score:.6},{...base,id:'peak',time:70,score:.9},{...base,id:'old',time:1,score:.7}],{a:{cooldown:30}});
 assert.equal(groups.length,2);assert.equal(groups[0].observations,2);assert.equal(groups[0].start,70);assert.equal(groups[0].time,100);assert.equal(groups[0].score,.9);assert.equal(groups[0].id,'peak');
});
test('history never merges different cameras or object classes',()=>{
 const f=fixture();const groups=f.context.group([{deviceId:'a',class:'person',time:100,score:.9},{deviceId:'b',class:'person',time:99,score:.8},{deviceId:'a',class:'dog',time:98,score:.8}]);assert.equal(groups.length,3);
});
