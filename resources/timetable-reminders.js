const surface=element.querySelector('.tt-source-surface'),status=element.querySelector('.tt-source-status');
let state=null,last=null,waiting=false,pending=null;
function send(kind){if(waiting||!state)return;waiting=true;pending=crypto.randomUUID();const lead=Number(surface.querySelector('[data-source-lead]')?.value||0),custom=Number(surface.querySelector('[data-source-custom]')?.value||0);props.value={...state,action:{kind,lead:lead||custom,view_token:state.view_token,source_view_token:state.source_view_token,profile_revision:state.profile_revision,request_id:pending}};trigger('submit');}
const click=e=>{const b=e.target.closest?.('[data-source-action]');if(!b||!element.contains(b))return;e.preventDefault();send(b.dataset.sourceAction);};
const change=e=>{if(e.target.matches?.('[data-source-lead],[data-source-custom]')&&state){const confirm=surface.querySelector('[data-source-action="confirm"]');if(confirm)confirm.disabled=true;send('invalidate');}};
function draw(){const next=props.value;if(!next||next.action||next.delivery_id===last)return;
  if(state&&next.profile_revision<state.profile_revision)return;
  if(next.reply_to&&(next.reply_to!==pending||next.reply_for_source!==state?.source_view_token||next.profile_revision!==state?.profile_revision))return;
  last=next.delivery_id;waiting=false;pending=null;state=next;if(!next.no_redraw)surface.innerHTML=next.html||'';status.textContent=next.message||'';}
element.addEventListener('click',click);element.addEventListener('input',change);watch('value',draw);draw();
