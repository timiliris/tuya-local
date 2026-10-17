 'use strict';
const form=document.querySelector('#login'),error=document.querySelector('#login-error'),language=document.querySelector('#language');
I18N.ready.then(()=>{language.value=I18N.language;});
language.onchange=()=>I18N.setLanguage(language.value);
form.onsubmit=async event=>{
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;error.textContent='';
  try{
    const response=await fetch('/api/access/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:document.querySelector('#password').value}),signal:AbortSignal.timeout(10000)});
    if(!response.ok){error.textContent=I18N.t(response.status===429?'Trop de tentatives. Attendez une minute.':'Mot de passe incorrect.');return;}
    document.querySelector('#password').value='';location.replace('/');
  }catch{error.textContent=I18N.t('Le serveur ne répond pas.');}finally{button.disabled=false;}
};
