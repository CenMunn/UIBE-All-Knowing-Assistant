window.__topicCardsCleanup?.();
const askPolicy = event => {
  const button = event.target.closest?.('#topic-content .topic-card-ask');
  if (!button || button.disabled) return;
  props.value = {leaf: button.dataset.leaf, section: button.dataset.section};
  trigger('submit');
};
document.addEventListener('click', askPolicy);
window.__topicCardsCleanup = () => document.removeEventListener('click', askPolicy);
