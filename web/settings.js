'use strict';
const CameraSettings = (() => {
  function displayValue(field, translate) {
    if (field.value === null) return translate('Non communiqué');
    if (field.valueLabel) return translate(field.valueLabel);
    if (field.type === 'bool') return translate(field.value ? 'Activé' : 'Désactivé');
    if (field.type === 'enum') {
      const option = field.options.find(item => item.value === field.value);
      return option?.label ? translate(option.label) : translate('Option {value}', {value: field.value});
    }
    if (field.type === 'value') return `${field.value / 10 ** field.scale}${field.unit ? ' ' + field.unit : ''}`;
    return String(field.value);
  }
  function inputValue(field, input) {
    if (field.type === 'bool') return input.checked;
    if (field.type === 'enum') return input.value;
    if (!input.value.trim() || !input.checkValidity()) throw new Error('Valeur de réglage invalide.');
    const scaled = Number(input.value) * 10 ** field.scale;
    const raw = Math.round(scaled);
    if (!Number.isFinite(scaled) || Math.abs(scaled - raw) > 1e-7 || raw < field.min || raw > field.max || (raw - field.min) % field.step) throw new Error('Valeur de réglage invalide.');
    return raw;
  }
  const stages = {
    reading: 'Vérification des réglages…', waking: 'Réveil automatique de la caméra…',
    applying: 'Application des changements…', confirming: 'Confirmation des nouvelles valeurs…',
  };
  class Panel {
    constructor({api, cameras, translate, delay = ms => new Promise(resolve => setTimeout(resolve, ms))}) {
      this.api = api; this.cameras = cameras; this.t = translate; this.delay = delay;
      this.generation = 0; this.busy = false; this.drafts = new Map(); this.draft = new Map(); this.invalid = new Set();
      this.dialog = document.querySelector('#settings-dialog');
      this.select = document.querySelector('#settings-camera');
      this.content = document.querySelector('#settings-content');
      this.message = document.querySelector('#settings-status');
      this.select.onchange = () => this.load();
      document.querySelector('#settings-refresh').onclick = () => this.load();
      document.querySelector('#settings-close').onclick = () => this.dialog.close();
      document.querySelector('#settings-reset').onclick = () => {this.draft.clear();this.invalid.clear();this.render();this.notice('Changements annulés.');};
      document.querySelector('#settings-save').onclick = () => this.saveDraft();
      document.querySelector('#settings-cancel').onclick = () => this.cancelWake();
      this.dialog.addEventListener('cancel', event => {if(this.busy)event.preventDefault();});
      this.dialog.addEventListener('close', () => {this.generation++; this.result = null;});
    }
    notice(key, kind='idle') {this.message.textContent=this.t(key);this.message.dataset.state=kind;}
    open(device) {
      if(this.busy)return;
      this.select.replaceChildren(...this.cameras().map(camera => {
        const option = document.createElement('option'); option.value = camera.deviceId; option.textContent = camera.deviceName; return option;
      }));
      this.select.value = device;
      if (!this.dialog.open) this.dialog.showModal();
      this.load();
    }
    setBusy(value) {
      this.busy=value;this.select.disabled=value;
      document.querySelector('#settings-refresh').disabled=value;
      document.querySelector('#settings-close').disabled=value;
      for(const input of this.content.querySelectorAll('input,select,button'))input.disabled=value;
      this.content.setAttribute('aria-busy',String(value));this.updateActions();
    }
    updateActions() {
      const count=this.draft.size;
      const save=document.querySelector('#settings-save');save.disabled=this.busy || !count || !!this.invalid.size;
      save.textContent=this.t(count ? 'Enregistrer ({count})' : 'Enregistrer',{count});
      document.querySelector('#settings-reset').disabled=this.busy || !count;
      document.querySelector('#settings-hint').textContent=this.t(this.busy ? 'La connexion temporaire reste locale et sera fermée automatiquement.' : count ? 'Un seul réveil pour toutes les modifications.' : this.result?.autoWake ? 'Le réveil est automatique lors de l’enregistrement.' : 'Seuls les réglages disponibles sur cette caméra sont affichés.');
    }
    async load() {
      if(this.busy)return;
      const generation=++this.generation;
      this.draft=this.drafts.get(this.select.value) || new Map();this.drafts.set(this.select.value,this.draft);
      this.invalid.clear();this.result=null;this.content.replaceChildren();document.querySelector('#settings-summary').replaceChildren();document.querySelector('#settings-nav').replaceChildren();this.setBusy(true);
      this.notice('Lecture des réglages…','busy');
      try {
        const result=await this.api('/api/settings/read',{deviceId:this.select.value});
        if(generation!==this.generation)return;
        this.result=result;
        for(const field of result.settings){const draft=this.draft.get(field.code);if(draft && draft.value===field.value)this.draft.delete(field.code);}
        this.render();this.notice('Valeurs rapportées par Tuya. Elles peuvent dater de la dernière connexion.');
      } catch(error){if(generation===this.generation){this.message.textContent=error.message;this.message.dataset.state='error';}}
      finally{this.setBusy(false);}
    }
    updateDraft(field,input,row) {
      try {
        const value=inputValue(field,input);this.invalid.delete(field.code);
        if(value===field.value)this.draft.delete(field.code);
        else this.draft.set(field.code,{code:field.code,value,expectedValue:this.draft.has(field.code)?this.draft.get(field.code).expectedValue:field.value});
        row.classList.toggle('modified',this.draft.has(field.code));input.setCustomValidity('');
        this.notice('Les changements attendent ton enregistrement.');
      }catch(error){this.invalid.add(field.code);input.setCustomValidity(this.t(error.message));input.reportValidity();}
      this.updateActions();
    }
    render() {
      if(!this.result)return;
      this.invalid.clear();this.content.replaceChildren();
      const groups=new Map();for(const field of this.result.settings){if(!groups.has(field.group))groups.set(field.group,[]);groups.get(field.group).push(field);}
      if(!groups.has(this.group))this.group=groups.keys().next().value;
      const nav=document.querySelector('#settings-nav');nav.replaceChildren();
      for(const [group,fields] of groups){
        const button=document.createElement('button');button.type='button';button.textContent=this.t(group);button.className='settings-category';button.setAttribute('aria-pressed',String(group===this.group));
        button.onclick=()=>{this.group=group;this.render();};nav.append(button);
      }
      const summary=document.querySelector('#settings-summary');summary.replaceChildren();
      const battery=this.result.settings.find(field=>field.code==='wireless_electricity');
      const card=this.result.settings.find(field=>field.code==='sd_status');
      for(const field of [battery,card].filter(Boolean)){
        const item=document.createElement('span');item.textContent=this.t(field.label)+': '+displayValue(field,this.t);summary.append(item);
      }
      const local=document.createElement('span');local.className='settings-local';local.textContent=this.t('Vidéo locale');summary.append(local);
      const heading=document.createElement('h3');heading.textContent=this.t(this.group || 'Réglages');this.content.append(heading);
      for(const field of groups.get(this.group) || []){
        if(field.code==='sd_storge' && card?.value===5)continue;
        const row=document.createElement('div');row.className='setting-row';row.classList.toggle('modified',this.draft.has(field.code));
        const label=document.createElement('label');label.textContent=this.t(field.label);
        const id=`setting-${field.code}`;label.htmlFor=id;
        const description=document.createElement('div');description.className='setting-description';description.append(label);
        const note=document.createElement('small');
        if(field.code==='basic_private')note.textContent=this.t('Le mode confidentialité coupe la vidéo.');
        if(field.code==='pir_switch')note.textContent=this.t('Les codes PIR dépendent du fabricant : vérifie leur correspondance dans Tuya.');
        if(field.code==='record_mode')note.textContent=this.t('L’enregistrement continu augmente la consommation.');
        description.append(note);row.append(description);
        if(!field.writable){const output=document.createElement('output');output.id=id;output.textContent=displayValue(field,this.t);row.append(output);}
        else{
          const value=this.draft.has(field.code)?this.draft.get(field.code).value:field.value;
          const controls=document.createElement('div');controls.className='setting-controls';
          const input=document.createElement(field.type==='enum'?'select':'input');input.id=id;
          if(field.type==='bool'){
            input.type='checkbox';input.setAttribute('role','switch');input.checked=value===true;input.className='setting-switch';
            if(value===null){const unknown=document.createElement('small');unknown.textContent=this.t('Non communiqué');description.append(unknown);}
          }else if(field.type==='value'){
            const factor=10**field.scale;input.type='number';input.min=field.min/factor;input.max=field.max/factor;input.step=field.step/factor;input.value=value===null?'':value/factor;
          }else{
            const empty=document.createElement('option');empty.value='';empty.textContent=this.t('Choisir…');empty.disabled=true;input.append(empty);
            for(const item of field.options){const option=document.createElement('option');option.value=item.value;option.textContent=item.label?this.t(item.label):this.t('Option {value}',{value:item.value});input.append(option);}
            input.value=value===null?'':String(value);
          }
          input.required=field.type!=='bool';input.disabled=this.busy;input.onchange=()=>this.updateDraft(field,input,row);
          controls.append(input);
          if(field.type==='value' && (field.unit || field.code==='wireless_lowpower')){const unit=document.createElement('span');unit.textContent=field.unit || '%';controls.append(unit);}
          row.append(controls);
        }
        this.content.append(row);
      }
      if(!this.result.settings.length)this.content.textContent=this.t('Aucun réglage compatible exposé par cette caméra.');
      this.updateActions();
    }
    async cancelWake() {
      if(!this.job)return;
      const button=document.querySelector('#settings-cancel');button.disabled=true;
      try{await this.api('/api/settings/cancel',{job:this.job});}catch(error){this.message.textContent=error.message;}finally{button.disabled=false;}
    }
    async saveDraft() {
      if(this.busy || !this.draft.size || this.invalid.size)return;
      const generation=this.generation;
      const changes=[...this.draft.values()];this.setBusy(true);this.notice('Vérification des réglages…','busy');
      const cancelButton=document.querySelector('#settings-cancel');
      try{
        const started=await this.api('/api/settings/start',{deviceId:this.select.value,changes});this.job=started.job;
        let operation;
        const deadline=Date.now()+300000;
        while(true){
          operation=await this.api('/api/settings/status',{job:this.job});
          if(generation!==this.generation)return;
          cancelButton.hidden=operation.done || !['reading','waking'].includes(operation.stage);
          if(operation.done)break;
          this.notice(stages[operation.stage] || 'Application des changements…','busy');
          if(Date.now()>deadline)throw new Error(this.t('L’opération prend plus de temps que prévu. Actualise les valeurs avant de réessayer.'));
          await this.delay(800);
        }
        if(operation.error)throw new Error(this.t(operation.error));
        this.result=operation.result;
        if(['reported','unchanged'].includes(this.result.writeStatus))this.draft.clear();
        this.render();this.notice(this.result.writeStatus==='reported'?'Changements confirmés dans Tuya.':this.result.writeStatus==='unchanged'?'Ce réglage possède déjà cette valeur.':'Commande acceptée, confirmation en attente. Actualise les valeurs dans quelques instants.',this.result.writeStatus==='pending'?'pending':'success');
      }catch(error){if(generation===this.generation){this.message.textContent=error.message;this.message.dataset.state='error';}}
      finally{this.job=null;cancelButton.hidden=true;this.setBusy(false);}
    }
  }
  return {Panel, displayValue, inputValue};
})();
