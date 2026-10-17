'use strict';
const AccessControl={
  isAdmin(access){return access?.authenticated===true && access?.canManage===true && access?.user?.role==='admin';},
  apply(document,access,translate){
    const admin=this.isAdmin(access);
    document.documentElement.classList.toggle('viewer-session',!admin);
    for(const id of ['nav-ai','nav-diagnostics','server-settings-open','account-button','refresh','detail-settings','detail-copy','empty-connect']){
      const node=document.getElementById(id);if(node)node.hidden=!admin;
    }
    const pad=document.getElementById('ptz-pad');if(pad?.parentElement)pad.parentElement.hidden=!admin;
    const identity=document.getElementById('access-identity');
    if(identity)identity.textContent=access?.enabled && access.user?`${access.user.username} · ${translate(admin?'Administrateur':'Lecture seule')}`:'';
    return admin;
  }
};
