'use strict';
class ServerSettings {
  constructor({api,translate,onSaving=()=>{},onSaved=()=>{},beforeSave=async()=>{}}) {
    this.api=api;this.t=translate;this.onSaving=onSaving;this.onSaved=onSaved;this.beforeSave=beforeSave;
    this.dialog=document.querySelector('#server-settings-dialog');this.form=document.querySelector('#server-access-form');
    this.status=document.querySelector('#server-access-status');this.summary=document.querySelector('#server-access-summary');
    this.current=document.querySelector('#server-current-password');this.password=document.querySelector('#server-new-password');this.confirmation=document.querySelector('#server-confirm-password');
    this.adminName=document.querySelector('#server-admin-username');this.users=[];this.user=null;this.editing=null;this.tab='users';
    this.userForm=document.querySelector('#server-user-form');this.userName=document.querySelector('#server-user-name');this.userRole=document.querySelector('#server-user-role');this.userPassword=document.querySelector('#server-user-password');this.userConfirmation=document.querySelector('#server-user-confirmation');
    document.querySelector('#server-user-add').onclick=()=>this.editUser(null,true);
    this.userForm.onsubmit=event=>{event.preventDefault();this.saveUser();};document.querySelector('#server-user-cancel').onclick=()=>this.editUser(null);
    this.close=document.querySelector('#server-settings-close');this.submit=document.querySelector('#server-access-save');this.enabled=false;this.saving=false;
    document.querySelector('#server-users-tab').onclick=()=>{this.tab='users';this.render();};document.querySelector('#server-password-tab').onclick=()=>{this.tab='password';this.render();};
    document.querySelector('#server-settings-open').onclick=()=>this.open();
    this.close.onclick=()=>this.dialog.close();this.form.onsubmit=event=>{event.preventDefault();this.save();};
    this.dialog.addEventListener('cancel',event=>{if(this.saving)event.preventDefault();});
    this.dialog.addEventListener('close',()=>this.clear());
  }
  clear(){this.current.value='';this.password.value='';this.confirmation.value='';this.userPassword.value='';this.userConfirmation.value='';}
  render(){
    document.querySelector('#server-admin-username-field').hidden=this.enabled;this.adminName.required=!this.enabled;
    document.querySelector('#server-security-tabs').hidden=!this.enabled;
    document.querySelector('#server-users-section').hidden=!this.enabled || this.tab!=='users';
    document.querySelector('#server-password-section').hidden=this.enabled && this.tab!=='password';
    document.querySelector('#server-users-tab').setAttribute?.('aria-pressed',this.tab==='users');document.querySelector('#server-password-tab').setAttribute?.('aria-pressed',this.tab==='password');
    document.querySelector('#server-current-password-field').hidden=!this.enabled;this.current.required=this.enabled;
    this.summary.textContent=this.t(this.enabled?'Vous gérez le mot de passe de votre compte administrateur.':'Créez le premier compte administrateur pour protéger ce serveur.');
    if(this.enabled)this.renderUsers();
    this.submit.textContent=this.t(this.saving?'Enregistrement…':this.enabled?'Modifier le mot de passe':'Définir le mot de passe');
  }
  async open(){
    this.clear();this.status.textContent='';this.form.hidden=true;this.dialog.showModal();
    try{const access=await this.api('/api/access');if(!this.dialog.open)return;if(access.canManage===false){this.status.textContent=this.t('Accès réservé aux administrateurs.');return;}this.enabled=access.enabled;this.tab='users';this.user=access.user;this.render();this.editUser(null);this.form.hidden=false;if(this.enabled)await this.loadUsers();}
    catch(error){if(this.dialog.open)this.status.textContent=error.message;}
  }
  async save(){
    if(this.saving)return;
    if(this.password.value.length<12 || this.password.value.length>1024){this.status.textContent=this.t('Utilisez un mot de passe de 12 à 1024 caractères.');return;}
    if(this.password.value!==this.confirmation.value){this.status.textContent=this.t('Les mots de passe ne correspondent pas.');return;}
    this.saving=true;this.onSaving(true);this.status.textContent='';this.close.disabled=true;this.submit.disabled=true;this.render();
    for(const control of this.dialog.querySelectorAll?.('button,input,select') || [])control.disabled=true;
    for(const field of [this.current,this.password,this.confirmation])field.readOnly=true;
    try{
      await this.beforeSave();
      const result=await this.api('/api/access/password',{username:this.adminName.value || 'admin',currentPassword:this.current.value,password:this.password.value,confirmation:this.confirmation.value});
      this.enabled=result.enabled;this.user=result.user;this.clear();this.status.textContent=this.t('Mot de passe enregistré. Vos autres sessions ont été déconnectées.');this.onSaved(result);try{await this.loadUsers();}catch(error){this.status.textContent+=' '+error.message;}
    }catch(error){this.status.textContent=error.message;}
    finally{this.saving=false;this.onSaving(false);this.close.disabled=false;this.submit.disabled=false;for(const field of [this.current,this.password,this.confirmation])field.readOnly=false;for(const control of this.dialog.querySelectorAll?.('button,input,select') || [])control.disabled=false;this.render();}
  }
  async loadUsers(){
    const result=await this.api('/api/access/users');
    if(!this.dialog.open)return;
    this.users=Array.isArray(result.users)?result.users:[];this.renderUsers();
  }
  renderUsers(){
    const list=document.querySelector('#server-users-list');list.replaceChildren?.();
    document.querySelector('#server-users-count').textContent=`${this.users.length} / 32`;
    for(const user of this.users){
      const row=document.createElement('div');row.className='server-user-row';
      const detail=document.createElement('div'),name=document.createElement('strong'),role=document.createElement('small');
      name.textContent=user.username;role.textContent=this.t(user.role==='admin'?'Administrateur':'Lecture seule');detail.append(name,role);row.append(detail);
      if(user.id===this.user?.id){const own=document.createElement('small');own.textContent=this.t('Votre compte');row.append(own);}
      else{
        for(const [label,action] of [['Modifier',()=>this.editUser(user)],['Supprimer',()=>this.deleteUser(user)]]){
          const button=document.createElement('button');button.type='button';button.className='button secondary';button.textContent=this.t(label);button.disabled=this.saving;button.onclick=()=>{if(!this.saving)action();};row.append(button);
        }
      }
      list.append(row);
    }
  }
  editUser(user,show=Boolean(user)){
    if(this.saving)return;this.userForm.hidden=!show;this.editing=user;this.userName.value=user?.username || '';this.userName.readOnly=Boolean(user);this.userRole.value=user?.role || 'viewer';this.userPassword.value='';this.userConfirmation.value='';
    this.userPassword.required=!user;this.userConfirmation.required=!user;
    document.querySelector('#server-user-form-title').textContent=this.t(user?'Modifier un utilisateur':'Ajouter un utilisateur');
    document.querySelector('#server-user-save').textContent=this.t(user?'Enregistrer les droits':'Ajouter l’utilisateur');
    document.querySelector('#server-user-cancel').hidden=!show;
    document.querySelector('#server-user-password-help').textContent=this.t(user?'Laissez vide pour conserver le mot de passe actuel.':'Au moins 12 caractères. Utilisez un mot de passe unique.');
  }
  async saveUser(){
    if(this.saving)return;
    const body={id:this.editing?.id,username:this.userName.value,role:this.userRole.value,password:this.userPassword.value,confirmation:this.userConfirmation.value};
    if((!this.editing || body.password || body.confirmation) && (body.password.length<12 || body.password.length>1024)){this.status.textContent=this.t('Utilisez un mot de passe de 12 à 1024 caractères.');return;}
    if(body.password!==body.confirmation){this.status.textContent=this.t('Les mots de passe ne correspondent pas.');return;}
    await this.mutateUser(this.editing?'update':'create',body);
  }
  async deleteUser(user){
    if(this.saving || !confirm(this.t('Supprimer le compte {username} et déconnecter ses sessions ?', {username:user.username})))return;
    await this.mutateUser('delete',{id:user.id});
  }
  async mutateUser(operation,body){
    let saved=false;
    this.saving=true;this.onSaving(true);this.status.textContent='';this.close.disabled=true;
    for(const control of this.dialog.querySelectorAll?.('button,input,select') || [])control.disabled=true;
    try{
      const result=await this.api('/api/access/users/'+operation,body);
      this.users=result.users;this.userPassword.value='';this.userConfirmation.value='';
      this.status.textContent=this.t('Utilisateurs enregistrés. Les sessions des comptes modifiés ont été déconnectées.');
      saved=true;
    }catch(error){this.status.textContent=error.message;}
    finally{
      this.saving=false;this.onSaving(false);this.close.disabled=false;
      for(const control of this.dialog.querySelectorAll?.('button,input,select') || [])control.disabled=false;
      this.renderUsers();if(saved)this.editUser(null);
    }
  }

}
