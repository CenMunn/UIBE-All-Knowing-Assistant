const surface=element.querySelector('.tt-surface'), feedback=element.querySelector('.tt-feedback'), overlay=element.querySelector('.tt-overlay');
let stable=null,lastDelivery=null,waiting=false,dirty=false,returnFocus=null,stageTimer=null,zoom=100;
function rememberFocus(node){
  if(!returnFocus&&node&&surface.contains(node))returnFocus={node,data:{...node.dataset}};
}
function restoreFocus(){
  if(!returnFocus)return;
  const saved=returnFocus;returnFocus=null;
  const match=[...surface.querySelectorAll('[data-tt-action]')].find(b=>['ttAction','id','day','period','week'].every(k=>b.dataset[k]===saved.data[k]));
  (saved.node.isConnected?saved.node:match||surface.querySelector('.tt-heading button'))?.focus();
}
function fields(root=overlay){return Object.fromEntries([...root.querySelectorAll('[data-tt-field]')].filter(e=>!e.closest('.tt-import-record')).map(e=>[e.dataset.ttField,e.type==='checkbox'?e.checked:e.value]));}
function rows(){return [...overlay.querySelectorAll('[data-record-index]')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-tt-field]')].map(e=>[e.dataset.ttField,e.type==='checkbox'?e.checked:e.value])));}
function submit(kind,extra={}){
  if(!stable||waiting)return;
  clearTimeout(stageTimer);
  waiting=true;
  surface.setAttribute('aria-busy','true');
  if(kind!=='stage'&&kind!=='tick'){feedback.textContent=kind==='recognize'?'正在识别图片，请稍候…':'正在处理…';}
  const action={kind,view_token:stable.view_token,profile_revision:stable.profile_revision,fields:fields(),rows:rows(),conflicts:[...overlay.querySelectorAll('[data-conflict]:checked')].map(e=>e.dataset.conflict),...extra};
  props.value={...stable,action};trigger(kind==='tick'?'input':'submit');
}
function draw(){
  const state=props.value;
  if(!state||state.action||state.delivery_id===lastDelivery)return;
  if(state.reply_for&&stable?.view_token&&state.reply_for!==stable.view_token)return;
  lastDelivery=state.delivery_id;waiting=false;stable=state;
  surface.setAttribute('aria-busy',state.busy?'true':'false');
  dirty=Boolean(state.dirty);feedback.textContent=state.message||'';
  if(state.no_redraw){
    if(state.clock){surface.querySelectorAll('.tt-today,.tt-current').forEach(e=>e.remove());
      const grid=surface.querySelector('.tt-grid'),p=state.clock;
      if(grid&&p.weekday){const today=document.createElement('div');today.className='tt-today';today.style.gridColumn=String(p.weekday+1);today.style.gridRow='1 / -1';today.setAttribute('aria-label','今天所在列');grid.append(today);
        if(p.period){const now=document.createElement('div');now.className='tt-current';now.style.gridColumn=String(p.weekday+1);now.style.gridRow=String(p.period+1);const label=document.createElement('span');label.textContent='当前时段';now.append(label);grid.append(now);}}
    }return;
  }
  const scroll=surface.querySelector('.tt-grid-scroll')?.scrollLeft||0;
  rememberFocus(document.activeElement);
  surface.innerHTML=state.html||'';
  const scroller=surface.querySelector('.tt-grid-scroll');if(scroller)scroller.scrollLeft=scroll;
  const previous=overlay.querySelector('dialog');if(previous?.open)previous.close();
  overlay.innerHTML=state.modal||'';
  const dlg=overlay.querySelector('dialog');
  if(dlg){dlg.showModal();(dlg.querySelector('[data-tt-action="close-editor"]')||dlg.querySelector('input,select,textarea,button'))?.focus();dlg.addEventListener('cancel',e=>{e.preventDefault();submit('close-editor');});}
  else restoreFocus();
  if(state.busy&&dlg){dlg.querySelectorAll('button,input,select,textarea').forEach(el=>el.disabled=true);const stop=document.createElement('button');stop.type='button';stop.className='tt-button';stop.dataset.ttAction='cancel-recognition';stop.textContent='取消本次识别';dlg.querySelector('.tt-dialog-body').prepend(stop);}
  if(state.navigate==='profile')setTimeout(()=>document.querySelector('#profile-open')?.click(),0);
}
const click=e=>{
  const b=e.target.closest?.('[data-tt-action]');if(!b||!element.contains(b))return;
  e.preventDefault();const kind=b.dataset.ttAction;
  if(kind==='cancel-recognition'){document.querySelector('#timetable-cancel')?.click();return;}
  if(kind==='upload'){document.querySelector('#timetable-upload input[type=file]')?.click();return;}
  if(kind.startsWith('zoom-')){zoom=kind==='zoom-fit'?100:Math.min(250,Math.max(50,zoom+(kind==='zoom-in'?20:-20)));overlay.querySelectorAll('.tt-source-image').forEach(im=>im.style.width=zoom+'%');return;}
  const extra={};for(const k of ['id','day','period','week'])if(b.dataset[k]!==undefined)extra[k]=['day','period','week'].includes(k)?Number(b.dataset[k]):b.dataset[k];
  if(kind==='adopt-teacher')Object.assign(extra,{row_index:Number(b.dataset.rowIndex),teacher_id:b.dataset.teacherId,recognition_id:b.dataset.recognitionId,teacher_candidate_generation:b.dataset.teacherCandidateGeneration});
  rememberFocus(b);
  submit(kind,extra);
};
const input=e=>{
  if(e.target.matches?.('[data-tt-field]')&&overlay.contains(e.target)){dirty=true;clearTimeout(stageTimer);stageTimer=setTimeout(()=>{if(!waiting)submit('stage');},350);}
};
const change=e=>{if(e.target.dataset?.ttField==='view_week'&&surface.contains(e.target))submit('week',{week:Number(e.target.value)});};
const leave=e=>{
  if(!surface.getClientRects().length)return;
  const b=e.target.closest?.('button');if(!b||element.contains(b)||b.id==='timetable-cancel')return;
  const target=b.dataset.campusTarget||(b.dataset.target?.startsWith('topic:')?b.dataset.target:({'nav-chat':'chat','nav-materials':'materials','nav-timetable':'timetable','nav-mail':'mail','nav-teachers':'teachers','nav-reminders':'reminders','nav-exams':'exams','nav-files':'files','profile-open':'profile'})[b.id]);
  if(!target)return;
  if(waiting||stable?.busy){e.preventDefault();e.stopImmediatePropagation();feedback.textContent='请先等待或取消本次课表识别。';return;}
  if(dirty){e.preventDefault();e.stopImmediatePropagation();submit('leave',{target});}
};
const unload=e=>{if(dirty){e.preventDefault();e.returnValue='';}};
const tick=()=>{if(document.visibilityState==='visible'&&surface.getClientRects().length&&!waiting&&!dirty&&!overlay.querySelector('dialog'))submit('tick');};
window.__timetableCleanup?.();
element.addEventListener('click',click);element.addEventListener('input',input);element.addEventListener('change',change);window.addEventListener('click',leave,true);window.addEventListener('beforeunload',unload);document.addEventListener('visibilitychange',tick);
const timer=setInterval(tick,60000);
window.__timetableCleanup=()=>{clearInterval(timer);clearTimeout(stageTimer);element.removeEventListener('click',click);element.removeEventListener('input',input);element.removeEventListener('change',change);window.removeEventListener('click',leave,true);window.removeEventListener('beforeunload',unload);document.removeEventListener('visibilitychange',tick);};
watch('value',draw);draw();
