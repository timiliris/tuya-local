'use strict';
class ServerSettings {
  constructor({api,translate,onSaving=()=>{},onSaved=()=>{},beforeSave=async()=>{}}) {
    this.api=api;this.t=translate;this.onSaving=onSaving;this.onSaved=onSaved;this.beforeSave=beforeSave;
    this.dialog=document.querySelector('#server-settings-dialog');this.form=document.querySelector('#server-access-form');
    this.status=document.querySelector('#server-access-status');this.summary=document.querySelector('#server-access-summary');
    this.current=document.querySelector('#server-current-password');this.password=document.querySelector('#server-new-password');this.confirmation=document.querySelector('#server-confirm-password');
    this.close=document.querySelector('#server-settings-close');this.submit=document.querySelector('#server-access-save');this.enabled=false;this.saving=false;
    document.querySelector('#server-settings-open').onclick=()=>this.open();
    this.close.onclick=()=>this.dialog.close();this.form.onsubmit=event=>{event.preventDefault();this.save();};
    this.dialog.addEventListener('cancel',event=>{if(this.saving)event.preventDefault();});
    this.dialog.addEventListener('close',()=>this.clear());
  }
  clear(){this.current.value='';this.password.value='';this.confirmation.value='';}
  render(){
    document.querySelector('#server-current-password-field').hidden=!this.enabled;this.current.required=this.enabled;
    this.summary.textContent=this.t(this.enabled?'Un mot de passe protège l’interface et les commandes de ce serveur.':'Aucun mot de passe ne protège actuellement ce serveur.');
    this.submit.textContent=this.t(this.saving?'Enregistrement…':this.enabled?'Modifier le mot de passe':'Définir le mot de passe');
  }
  async open(){
    this.clear();this.status.textContent='';this.form.hidden=true;this.dialog.showModal();
    try{const access=await this.api('/api/access');if(!this.dialog.open)return;this.enabled=access.enabled;this.render();this.form.hidden=false;}
    catch(error){if(this.dialog.open)this.status.textContent=error.message;}
  }
  async save(){
    if(this.saving)return;
    if(this.password.value.length<12 || this.password.value.length>1024){this.status.textContent=this.t('Utilisez un mot de passe de 12 à 1024 caractères.');return;}
    if(this.password.value!==this.confirmation.value){this.status.textContent=this.t('Les mots de passe ne correspondent pas.');return;}
    this.saving=true;this.onSaving(true);this.status.textContent='';this.close.disabled=true;this.submit.disabled=true;this.render();
    for(const field of [this.current,this.password,this.confirmation])field.readOnly=true;
    try{
      await this.beforeSave();
      const result=await this.api('/api/access/password',{currentPassword:this.current.value,password:this.password.value,confirmation:this.confirmation.value});
      this.enabled=result.enabled;this.clear();this.status.textContent=this.t('Mot de passe enregistré. Les autres sessions ont été déconnectées.');this.onSaved(result);
    }catch(error){this.status.textContent=error.message;}
    finally{this.saving=false;this.onSaving(false);this.close.disabled=false;this.submit.disabled=false;for(const field of [this.current,this.password,this.confirmation])field.readOnly=false;this.render();}
  }
}
