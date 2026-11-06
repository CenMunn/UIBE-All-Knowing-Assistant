const surface=element.querySelector('.documents-surface');
const feedback=element.querySelector('.documents-feedback');
const overlay=element.querySelector('.documents-overlay');
let waiting=false,lastDelivery=null,dirty=false,returnFocus=null,zoom=100;
const fields=()=>Object.fromEntries([...surface.querySelectorAll('[data-form-key]')].map(e=>[e.dataset.formKey,e.value]));
function layout(){
  const grow=surface.querySelector('[data-layout-grow]');if(!grow)return undefined;
  const value={version:1,grow_rows:grow.checked,defaults:{},fields:{}};
  surface.querySelectorAll('[data-layout-property]').forEach(el=>{
    if(!el.value)return;
    const scope=el.dataset.layoutScope;
    const target=scope==='defaults'?value.defaults:(value.fields[scope]??={});
    target[el.dataset.layoutProperty]=el.value;
  });return value;
}
function submit(kind,extra={}) {
  if(waiting)return;
  const action={kind,fields:fields(),layout:layout(),...extra};
  waiting=true;feedback.textContent='正在处理…';
  surface.setAttribute('aria-busy','true');
  surface.querySelectorAll('input,textarea,select,button').forEach(el=>{
    if(el.matches('textarea,input:not([type="checkbox"]):not([type="radio"])'))el.readOnly=true;
    else el.disabled=true;
  });
  props.value={...props.value,action};trigger('submit');
}
function draw(){
  const host=document.querySelector('#files-surface-host');
  if(host&&!host.contains(surface))host.append(surface);
  surface.hidden=!host;
  const state=props.value;if(!state||state.action||state.delivery_id===lastDelivery)return;
  lastDelivery=state.delivery_id;waiting=false;
  surface.removeAttribute('aria-busy');
  const oldField=document.activeElement?.dataset?.formKey;
  const opened=[...surface.querySelectorAll('details[open][data-document-details]')].map(el=>el.dataset.documentDetails);
  surface.innerHTML=state.html||'';feedback.textContent=state.message||'';dirty=Boolean(state.dirty);
  opened.forEach(key=>{const el=surface.querySelector('[data-document-details="'+CSS.escape(key)+'"]');if(el)el.open=true;});
  const oldDialog=overlay.querySelector('dialog');if(oldDialog?.open)oldDialog.close();
  overlay.innerHTML=state.modal||'';
  const dialog=overlay.querySelector('dialog');
  if(dialog){returnFocus=document.activeElement;dialog.showModal();dialog.querySelector('button')?.focus();
    dialog.addEventListener('cancel',e=>{e.preventDefault();submit('close-preview');});
  }else if(returnFocus){returnFocus.focus?.();returnFocus=null;}
  if(oldField)surface.querySelector('[data-form-key="'+CSS.escape(oldField)+'"]')?.focus();
}
const click=e=>{
  const b=e.target.closest?.('[data-doc-action]');if(!b||b.disabled)return;
  const action=b.dataset.docAction;
  if(action.startsWith('zoom-')){
    const frame=overlay.querySelector('iframe');if(!frame)return;
    zoom=action==='zoom-fit'?100:Math.min(200,Math.max(50,zoom+(action==='zoom-in'?10:-10)));
    frame.src=frame.src.split('#')[0]+'#zoom='+zoom;return;
  }
  e.preventDefault();
  const selected=[...surface.querySelectorAll('.file-select:checked')].map(c=>({id:c.dataset.ref,version:Number(c.dataset.version),revision:Number(c.dataset.revision)}));
  const data={...b.dataset};delete data.docAction;
  if(data.version)data.version=Number(data.version);if(data.revision)data.revision=Number(data.revision);
  data.refs=selected;data.query=surface.querySelector('[data-file-search]')?.value||'';
  data.name=overlay.querySelector('[data-rename-name]')?.value||surface.querySelector('[data-copy-name]')?.value||'';
  submit(action,data);
};
const input=e=>{if(e.target.matches?.('[data-form-key],[data-layout-property],[data-layout-grow]')){dirty=true;surface.querySelector('.file-save-state')?.replaceChildren(document.createTextNode('未保存'));}};
const leave=e=>{
  if(!dirty||!surface.getClientRects().length)return;
  const b=e.target.closest?.('button');if(!b)return;
  if(b.id==='profile-open'){e.preventDefault();e.stopImmediatePropagation();submit('draft');return;}
  const target=b.dataset.campusTarget||(b.dataset.target?.startsWith('topic:')?b.dataset.target:({'nav-chat':'chat','nav-materials':'materials','nav-mail':'mail','nav-teachers':'teachers','nav-reminders':'reminders','nav-exams':'exams','nav-files':'files','nav-timetable':'timetable'})[b.id]);
  if(!target)return;
  e.preventDefault();e.stopImmediatePropagation();submit('leave',{target});
};
const unload=e=>{if(dirty){e.preventDefault();e.returnValue='';}};
window.__documentsCleanup?.();
document.addEventListener('click',click);surface.addEventListener('input',input);window.addEventListener('click',leave,true);window.addEventListener('beforeunload',unload);
const hostObserver=new MutationObserver(()=>{const host=document.querySelector('#files-surface-host');if(host&&!host.contains(surface))draw();});
hostObserver.observe(document.body,{childList:true,subtree:true});
window.__documentsCleanup=()=>{hostObserver.disconnect();document.removeEventListener('click',click);surface.removeEventListener('input',input);window.removeEventListener('click',leave,true);window.removeEventListener('beforeunload',unload);};
watch('value',draw);draw();
