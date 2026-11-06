const errorHost = element.querySelector('.nav-error');
let lastState = null, requested = false, animation = 0;
// A single browser delivery at a time: server locks alone cannot order network responses.
window.__campusDeliveryCleanup?.();
let delivery = null;
const transportStatus = document.createElement('div');
transportStatus.id = 'navigation-delivery-status'; transportStatus.setAttribute('role', 'status');
document.body.append(transportStatus);
const navigationTrigger = '#module-navigation button[id^="nav-"], [data-campus-target], #topic-tools button';
const suggestionTrigger = '#welcome-shortcuts button, #topic-ask, .topic-card-ask, #suggestion-accept, #suggestion-cancel';
const captureDelivery = event => {
  const button = event.target.closest?.('button');
  if (!button || button.disabled) return;
  if (delivery) { event.preventDefault(); event.stopImmediatePropagation(); return; }
  if (!button.matches(navigationTrigger + ', ' + suggestionTrigger)) return;
  const field = document.querySelector('#question textarea');
  delivery = {id: props.value?.delivery_id, field,
    readOnly: field?.readOnly, suggestion: button.matches(suggestionTrigger),
    controls: [...document.querySelectorAll('button')].map(el => [el, el.getAttribute('aria-disabled')])};
  delivery.controls.forEach(([el]) => el.setAttribute('aria-disabled','true'));
  if (delivery.suggestion && field) { field.readOnly = true; field.setAttribute('aria-busy','true'); }
  transportStatus.textContent = delivery.suggestion ? '正在填入问题…' : '正在切换…';
  delivery.timer = setTimeout(() => {
    transportStatus.textContent = '界面响应较慢，请等待；也可复制草稿后刷新页面。';
  }, 15000);
};
const finishDelivery = state => {
  if (!delivery || state.delivery_id === delivery.id) return;
  const pending = delivery;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (delivery !== pending) return;
    clearTimeout(pending.timer);
    pending.controls.forEach(([el, value]) => value === null ? el.removeAttribute('aria-disabled') : el.setAttribute('aria-disabled',value));
    if (pending.suggestion && pending.field) {
      pending.field.readOnly = pending.readOnly; pending.field.removeAttribute('aria-busy');
    }
    delivery = null; transportStatus.textContent = '';
  }));
};
document.addEventListener('click', captureDelivery, true);
window.__campusDeliveryCleanup = () => {
  document.removeEventListener('click', captureDelivery, true);
  if (delivery) {
    clearTimeout(delivery.timer);
    delivery.controls.forEach(([el, value]) => value === null ? el.removeAttribute('aria-disabled') : el.setAttribute('aria-disabled',value));
    if (delivery.suggestion && delivery.field) {
      delivery.field.readOnly = delivery.readOnly; delivery.field.removeAttribute('aria-busy');
    }
  }
  transportStatus.remove(); document.removeEventListener('scroll', saveScroll, true);
  document.removeEventListener('click',openMaterial);
  iconObserver?.disconnect();
};
let iconObserver=null;
const openMaterial = event => {
  const button=event.target.closest?.('[data-campus-target]');
  if(!button||button.disabled||requested)return;
  requested=true;
  props.value={...props.value,action:{kind:'open',target:button.dataset.campusTarget}};
  trigger('submit');
};
document.addEventListener('click',openMaterial);
function syncNavigation(state){
  if(!state?.workspace)return;
  const active=state.workspace==='materials'||state.workspace.startsWith('category:')||state.workspace.startsWith('topic:')?'materials':state.workspace;
  document.querySelectorAll('#module-navigation button[id^="nav-"]').forEach(button=>{
    const selected=button.id==='nav-'+active;
    if(button.classList.contains('nav-current')!==selected)button.classList.toggle('nav-current',selected);
    if(selected&&button.getAttribute('aria-current')!=='page')button.setAttribute('aria-current','page');
    else if(!selected&&button.hasAttribute('aria-current'))button.removeAttribute('aria-current');
  });
}
function draw() {
  const state = props.value;
  if (!state || state.action) return;
  requested = false;
  errorHost.textContent = state.error || '';
  const oldState = lastState;
  if (oldState && state.revision < oldState.revision) return;
  finishDelivery(state);
  syncNavigation(state);
  if(typeof decorateNavigationIcons==='function')decorateNavigationIcons(document);
  lastState = {...state};
  const switched = oldState && oldState.workspace !== state.workspace;
  if (switched) {
    document.querySelector('#settings-panel')?.classList.remove('navigation-open');
    document.querySelector('#navigation-toggle')?.setAttribute('aria-expanded','false');
    if (state.workspace === 'chat') requestAnimationFrame(() => {
      const scroller = document.querySelector('#chat .bubble-wrap');
      if (scroller && savedScroll !== null) scroller.scrollTop = savedScroll;
    });
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (state.workspace.startsWith('topic:')) {
        const heading = document.querySelector('#topic-content h1, #topic-content h2');
        if (heading) { heading.tabIndex = -1; heading.focus({preventScroll:true}); }
      } else if (matchMedia('(max-width:767px)').matches) {
        document.querySelector('#navigation-toggle')?.focus({preventScroll:true});
      }
    }));
  }
}
let savedScroll = null;
const saveScroll = event => {
  if (event.target.matches?.('#chat .bubble-wrap') && event.target.getClientRects().length &&
      !document.querySelector('#chat-workspace')?.classList.contains('module-inactive')) savedScroll = event.target.scrollTop;
};
document.addEventListener('scroll', saveScroll, true);
iconObserver=new MutationObserver(()=>{
  syncNavigation(lastState||props.value);
  if(typeof decorateNavigationIcons==='function')decorateNavigationIcons(document);
});
const sidebar=document.querySelector('#module-navigation');
if(sidebar)iconObserver.observe(sidebar,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});
watch('value', draw);
draw();
