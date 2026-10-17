const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function fixture(status){
 const nodes=new Map(),calls=[],button={disabled:false};
 const document={querySelector(selector){if(!nodes.has(selector))nodes.set(selector,{value:'',textContent:'',querySelector:()=>button});return nodes.get(selector);}};
 const context=vm.createContext({document,I18N:{ready:Promise.resolve(),language:'en',t:key=>key,setLanguage(){}},AbortSignal:{timeout(){}},fetch:async(path,options)=>{calls.push({path,options});if(status==='offline')throw Error('offline');return {ok:status===200,status};},location:{replace:path=>calls.push({redirect:path})}});
 document.querySelector('#password');
 vm.runInContext(fs.readFileSync('web/login.js','utf8'),context);
 return {nodes,calls,button,submit:()=>nodes.get('#login').onsubmit({preventDefault(){}})};
}
test('login sends password only in a same-origin POST, clears it and redirects after success',async()=>{const f=fixture(200);f.nodes.get('#password').value='synthetic input';await f.submit();assert.equal(f.calls[0].path,'/api/access/login');assert.equal(f.calls[0].options.method,'POST');assert.equal(f.calls[0].options.headers['Content-Type'],'application/json');assert.equal(f.nodes.get('#password').value,'');assert.deepEqual(f.calls[1],{redirect:'/'});assert.equal(f.button.disabled,false);});
test('invalid passwords and throttling keep the form open with actionable errors',async()=>{for(const [status,message] of [[401,'Mot de passe incorrect.'],[429,'Trop de tentatives. Attendez une minute.'],['offline','Le serveur ne répond pas.']]){const f=fixture(status);await f.submit();assert.equal(f.nodes.get('#login-error').textContent,message);assert.equal(f.button.disabled,false);assert.equal(f.calls.some(c=>c.redirect),false);}});
