const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../web/settings.js'),'utf8')+'\nthis.settings=CameraSettings;',context);
const {displayValue,inputValue}=context.settings;
const t = (key,variables={})=>Object.entries(variables).reduce((s,[k,v])=>s.replace(`{${k}}`,v),key);
test('unknown values and enum choices never invent meanings',()=>{
  assert.equal(displayValue({value:null},t),'Non communiqué');
  assert.equal(displayValue({type:'enum',value:'3',options:[{value:'3',label:null}]},t),'Option 3');
  assert.equal(displayValue({type:'value',value:235,scale:1,unit:'%'},t),'23.5 %');
  assert.equal(displayValue({type:'value',value:1,valueLabel:'Normale'},t),'Normale');
});
test('numeric controls preserve scale and validate bounds and steps',()=>{
  const field={type:'value',scale:1,min:10,max:50,step:5};
  const input=value=>({value,checkValidity:()=>true});
  assert.equal(inputValue(field,input('2.5')),25);
  for(const value of ['', 'nan', '0', '6', '2.6','2.51'])assert.throws(()=>inputValue(field,input(value)));
});
test('boolean and enum types survive the input boundary',()=>{
  assert.equal(inputValue({type:'bool'},{checked:false}),false);
  assert.equal(inputValue({type:'bool'},{checked:true}),true);
  assert.equal(inputValue({type:'enum'},{value:'0'}),'0');
});
function fixture(api) {
  const elements=new Map();
  function element(){return {value:'',disabled:false,children:[],textContent:'',open:false,hidden:false,dataset:{},classList:{toggle(){}},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];},setAttribute(){},setCustomValidity(){},reportValidity(){},querySelectorAll(){return [];},addEventListener(){},showModal(){this.open=true;},close(){this.open=false;}};}
  const document={querySelector:selector=>{if(!elements.has(selector))elements.set(selector,element());return elements.get(selector);},createElement:()=>element()};
  const ctx=vm.createContext({document,Date});vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../web/settings.js'),'utf8')+'\nthis.Panel=CameraSettings.Panel;',ctx);
  return {panel:new ctx.Panel({api,cameras:()=>[],translate:t,delay:async()=>{}}),elements};
}
test('read failures leave controls usable and do not publish a setting',async()=>{
  const calls=[];const {panel}=fixture(async path=>{calls.push(path);throw new Error('offline');});
  await panel.load();assert.equal(panel.busy,false);assert.equal(panel.message.textContent,'offline');assert.deepEqual(calls,['/api/settings/read']);
});
test('one batch job tracks wake and confirmation, pending never means success',async()=>{
  const calls=[];let poll=0;const {panel}=fixture(async(path,body)=>{
    calls.push({path,body});if(path.endsWith('/start'))return {job:'job'};
    return ++poll===1?{done:false,stage:'waking'}:{done:true,stage:'pending',result:{settings:[],writeStatus:'pending'}};
  });
  panel.select.value='owned';panel.draft.set('basic_osd',{code:'basic_osd',value:true,expectedValue:false});
  await panel.saveDraft();
  assert.equal(calls[0].path,'/api/settings/start');assert.equal(calls[0].body.changes[0].expectedValue,false);
  assert.equal(calls[0].body.changes[0].value,true);assert.equal(poll,2);
  assert.match(panel.message.textContent,/confirmation en attente/);assert.equal(panel.busy,false);assert.equal(panel.draft.size,1);
});
test('confirmed batch clears drafts and enables the panel again',async()=>{
  const {panel}=fixture(async path=>path.endsWith('/start')?{job:'job'}:{done:true,result:{settings:[],writeStatus:'reported'}});
  panel.draft.set('basic_osd',{code:'basic_osd',value:true,expectedValue:false});
  await panel.saveDraft();assert.equal(panel.draft.size,0);assert.equal(panel.message.dataset.state,'success');assert.equal(panel.busy,false);
});
test('a closed panel ignores late values and never starts a write',async()=>{
  let resolve;const {panel}=fixture(()=>new Promise(r=>{resolve=r;}));const reading=panel.load();panel.generation++;
  resolve({settings:[]});await reading;assert.equal(panel.result,null);assert.equal(panel.busy,false);
});
test('switch drafts keep original value and reverting removes the change',()=>{
  const {panel}=fixture(async()=>{});const field={type:'bool',code:'basic_osd',value:false};
  const input={checked:true,setCustomValidity(){},reportValidity(){}};const row={classList:{toggle(){}}};
  panel.updateDraft(field,input,row);assert.equal(panel.draft.get(field.code).expectedValue,false);
  input.checked=false;panel.updateDraft(field,input,row);assert.equal(panel.draft.size,0);
});
test('refresh removes a pending draft once its value has been reported',async()=>{
  const {panel}=fixture(async()=>({settings:[{code:'basic_osd',value:true,type:'bool',group:'Image',label:'Date',writable:true}]}));
  panel.select.value='owned';const draft=new Map([['basic_osd',{value:true,expectedValue:false}]]);panel.drafts.set('owned',draft);
  await panel.load();assert.equal(panel.draft.size,0);
});
test('wake errors preserve drafts and display a recoverable error',async()=>{
  const {panel}=fixture(async path=>path.endsWith('/start')?{job:'job'}:{done:true,error:'wake timeout'});
  panel.draft.set('basic_osd',{code:'basic_osd',value:true,expectedValue:false});
  await panel.saveDraft();assert.equal(panel.draft.size,1);assert.equal(panel.busy,false);assert.equal(panel.message.dataset.state,'error');
});
