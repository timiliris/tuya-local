const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../desktop/ui/launcher.js'),'utf8');
function fixture(invoke=async()=>({running:false})){
 const nodes=new Map(),saved=new Map(),buttons=[{disabled:false},{disabled:false}];
 const document={documentElement:{},querySelector:id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',disabled:false});return nodes.get(id);},querySelectorAll:selector=>selector==='button'?buttons:[]};
 const calls=[],context=vm.createContext({document,navigator:{language:'fr'},localStorage:{getItem:key=>saved.get(key),setItem:(key,value)=>saved.set(key,value)},window:{__TAURI__:{core:{invoke:async(name,args)=>{calls.push({name,args});return invoke(name,args);}}}},setInterval(){}});
 vm.runInContext(source+'\nthis.catalogs=catalogs;this.runAction=action;',context);return {context,nodes,saved,calls,buttons};
}
test('desktop startup only checks server status and never starts a camera server',async()=>{const f=fixture();await new Promise(setImmediate);assert.deepEqual(f.calls.map(c=>c.name),['server_status']);});
test('desktop languages cover the same launcher controls',()=>{const f=fixture();for(const catalog of Object.values(f.context.catalogs))assert.deepEqual(Object.keys(catalog).sort(),Object.keys(f.context.catalogs.fr).sort());});
test('successful connection stores the server address after native verification',async()=>{const f=fixture();f.nodes.get('#address').value='http://127.0.0.1:18788/';f.nodes.get('#connect').onsubmit({preventDefault(){}});await new Promise(setImmediate);assert.equal(f.saved.get('tuya-local-server-address'),'http://127.0.0.1:18788/');assert.ok(f.calls.some(c=>c.name==='connect_server'));});
test('failed connection preserves the previous address and releases controls',async()=>{const f=fixture(async name=>{if(name==='connect_server')throw new Error('offline');return {running:false};});f.nodes.get('#connect').onsubmit({preventDefault(){}});await new Promise(setImmediate);assert.equal(f.saved.has('tuya-local-server-address'),false);assert.equal(f.nodes.get('#status').textContent,'Error: offline');assert.ok(f.buttons.every(b=>!b.disabled));});
