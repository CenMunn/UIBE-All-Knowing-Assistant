(function(root) {
  function shouldSend(event, composing) {
    return event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey &&
      !event.altKey && !event.isComposing && !composing && event.keyCode !== 229 && !event.repeat;
  }
  function bindSendKeys(field, send) {
    if (field.__assistantSendKeys) return;
    field.__assistantSendKeys = true;
    let composing = false;
    field.addEventListener('compositionstart', () => composing = true);
    field.addEventListener('compositionend', () => composing = false);
    // Gradio多行文本框默认在keypress阶段把Shift+Enter当提交。
    // 阻止其委托处理，但保留浏览器插入换行的默认行为。
    field.addEventListener('keypress', event => {
      if (event.key === 'Enter' && event.shiftKey) event.stopPropagation();
    }, true);
    field.addEventListener('keydown', event => {
      if (!shouldSend(event, composing)) return;
      event.preventDefault(); event.stopPropagation();
      if (field.value.trim()) send();
    });
  }
  root.AssistantKeys = {shouldSend, bindSendKeys};
  if (typeof module !== 'undefined') module.exports = root.AssistantKeys;
})(typeof window === 'undefined' ? globalThis : window);
