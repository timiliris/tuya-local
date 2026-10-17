'use strict';
const I18N = (() => {
  const supported = ['fr','en','nl','de','es'];
  let language = 'fr', catalogs = {};
  try {language = localStorage.getItem('tuya-local-language') || navigator.language.split('-')[0];} catch {}
  if (!supported.includes(language)) language='fr';
  function source(text) {
    if (catalogs.fr?.[text]) return text;
    for (const values of Object.values(catalogs)) {
      const key=Object.keys(values).find(key=>values[key]===text);
      if(key) return key;
    }
    return text;
  }
  function t(key, variables = {}) {
    let value = catalogs[language]?.[key] || catalogs.fr?.[key] || key || '';
    for (const [name,replacement] of Object.entries(variables)) value=value.replaceAll(`{${name}}`,String(replacement));
    return value;
  }
  function apply(root=document) {
    for (const node of root.querySelectorAll('[data-i18n]')) node.textContent=t(node.dataset.i18n);
    for (const name of ['aria-label','title','alt']) for(const node of root.querySelectorAll(`[data-i18n-${name}]`)) node.setAttribute(name,t(node.getAttribute(`data-i18n-${name}`)));
    document.documentElement.lang=language;
    document.title='Tuya Local — '+t('Caméras');
  }
  async function setLanguage(next) {
    if (!supported.includes(next)) next='fr';
    language=next;
    try {localStorage.setItem('tuya-local-language',language);} catch {}
    apply();
  }
  const ready=fetch('/locales.json').then(response=>{if(!response.ok)throw new Error('catalog');return response.json();}).then(data=>{catalogs=data;apply();}).catch(()=>{language='fr';apply();});
  return {get language(){return language;},ready,t,source,apply,setLanguage};
})();
