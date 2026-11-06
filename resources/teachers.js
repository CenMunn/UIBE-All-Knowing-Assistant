const surface=element.querySelector('.faculty-surface'), feedback=element.querySelector('.faculty-feedback');
let stable=null,last=null,waiting=false;
function send(kind,extra={}){
  if(!stable||waiting)return;
  waiting=true;surface.setAttribute('aria-busy','true');
  props.value={...stable,action:{kind,view_token:stable.view_token,...extra}};trigger('submit');
}
function draw(){
  const value=props.value;
  if(!value||value.action||last===value.delivery_id)return;
  if(value.reply_for&&stable&&value.reply_for!==stable.view_token)return;
  const first=!stable;stable=value;last=value.delivery_id;waiting=false;
  surface.innerHTML=value.html||'';surface.setAttribute('aria-busy','false');feedback.textContent=value.message||'';
  if(!first){
    const button=[...surface.querySelectorAll('[data-college-id]')].find(b=>b.dataset.collegeId===value.focus_college);
    (button||surface.querySelector('h2'))?.focus({preventScroll:true});
  }
}
const click=e=>{
  const b=e.target.closest?.('[data-faculty-action]');if(!b||!element.contains(b))return;
  send(b.dataset.facultyAction,{college_id:b.dataset.collegeId,page:Number(b.dataset.page),query:surface.querySelector('[data-faculty-query]')?.value||''});
};
const change=e=>{if(e.target.matches?.('[data-faculty-group],[data-faculty-staff]'))send('filter',{group_id:surface.querySelector('[data-faculty-group]').value,staff_type:surface.querySelector('[data-faculty-staff]').value});};
const key=e=>{if(e.key==='Enter'&&!e.isComposing&&e.keyCode!==229&&e.target.matches?.('[data-faculty-query]')){e.preventDefault();send('search',{query:e.target.value});}};
element.addEventListener('click',click);element.addEventListener('change',change);element.addEventListener('keydown',key);
watch('value',draw);draw();
