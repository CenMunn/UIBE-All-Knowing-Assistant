const citationUI = (() => {
  let currentContext = null, dialog = null, opener = null, scroll = null, position = 0;
  const safeLink = value => {
    if (typeof value !== 'string') return false;
    if (/^\/sources\/[A-Z]{1,6}\d{2,}\/content(?:\?(?:part=\d+(?:&download=true)?|download=true))?(?:#page=\d+)?$/.test(value)) return true;
    try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password; }
    catch { return false; }
  };
  function close() {
    if (!dialog) return;
    const previous = dialog; dialog = null;
    previous.close(); previous.remove();
    if (scroll?.isConnected) scroll.scrollTop = position;
    if (opener?.isConnected) opener.focus({preventScroll:true});
  }
  function link(label, href) {
    const item = document.createElement('a'); item.textContent = label;
    item.href = href; item.target = '_blank'; item.rel = 'noopener noreferrer';
    return item;
  }
  function show(row, trigger) {
    close(); opener = trigger;
    scroll = document.querySelector('#chat .bubble-wrap'); position = scroll?.scrollTop || 0;
    dialog = document.createElement('dialog'); dialog.className = 'citation-dialog';
    dialog.setAttribute('aria-label', row.title); dialog.setAttribute('aria-modal', 'true');
    const header = document.createElement('header');
    const heading = document.createElement('h2'); heading.textContent = row.title;
    const dismiss = document.createElement('button'); dismiss.type = 'button'; dismiss.textContent = '×';
    dismiss.setAttribute('aria-label', '关闭资料预览'); dismiss.addEventListener('click', close);
    header.append(heading, dismiss); dialog.append(header);
    const tools = document.createElement('div'); tools.className = 'citation-toolbar';
    const badge = document.createElement('span'); tools.append(badge);
    const download = link('下载原件', '#'); tools.append(download);
    const sources=row.source_links || (row.links || []).filter(safeLink).map(url=>({url,label:'来源网页 · '+url}));
    sources.filter(source=>safeLink(source.url)).forEach(source=>tools.append(link(source.label,source.url)));
    dialog.append(tools);
    const host=document.createElement('div');host.className='citation-content';dialog.append(host);
    function renderReadings() {
      const pane=document.createElement('div');pane.className='citation-reading-pane';
      for (const reading of row.readings || []) {
        const article=document.createElement('article');article.className='citation-reading';
        const meta=document.createElement('div');meta.className='citation-reading-meta';
        const kind=document.createElement('span');kind.textContent=reading.label;meta.append(kind);
        if (reading.captured_on) {const date=document.createElement('small');
          date.textContent=(reading.kind==='original'?'收录日期：':'核对日期：')+reading.captured_on;meta.append(date);}
        article.append(meta);
        const title=document.createElement('h3');title.textContent=reading.title;article.append(title);
        if (reading.publisher) {const publisher=document.createElement('p');publisher.className='citation-reading-publisher';publisher.textContent=reading.publisher;article.append(publisher);}
        if (reading.note) {const note=document.createElement('p');note.className='citation-reading-note';note.textContent=reading.note;article.append(note);}
        for (const text of reading.paragraphs || []) {const paragraph=document.createElement('p');paragraph.textContent=text;article.append(paragraph);}
        pane.append(article);
      }
      host.append(pane);
    }
    function displayDocument(doc) {
      host.replaceChildren(); badge.textContent=row.id+(doc.page ? ` · PDF第${doc.page}页起` : '');
      download.hidden=!(doc.download && safeLink(doc.download));
      if (!download.hidden) download.href=doc.download;
    if (doc.preview && safeLink(doc.preview)) {
      const note = document.createElement('p'); note.className = 'citation-note';
      note.textContent = '可在预览中滚动、缩放。若浏览器无法展示，请下载原件查看。'; host.append(note);
      const frame = document.createElement('iframe'); frame.title = row.title+'原文预览';
      frame.src = doc.preview; frame.referrerPolicy = 'no-referrer';
      // DOCX渲染器仅需脚本，不能获得同源权限；PDF采用浏览器内置查看器。
      if (!doc.page) frame.setAttribute('sandbox', 'allow-scripts');
      host.append(frame);
    } else if (row.readings?.length) {
      if (doc.download) {const note=document.createElement('p');note.className='citation-note';note.textContent='此附件请下载查看；下方为本条资料的收录正文或摘要，不代表所选附件全文。';host.append(note);}
      renderReadings();
    } else {
      const note = document.createElement('p'); note.className = 'citation-unavailable';
      note.textContent = doc.download ? '此原件暂不支持浏览器预览，请下载后查看。' :
        (row.links?.length ? '暂未收录可阅读的正文或摘要，请通过上方具名链接查看原始页面。' : '暂未提供可打开的原件或来源链接，请查看回答下方的“查看依据”。');
      host.append(note);
    }
    }
    if (row.documents?.length>1) {
      const choose=document.createElement('select');choose.setAttribute('aria-label','选择资料原件');
      row.documents.forEach((doc,index)=>{const option=document.createElement('option');option.value=String(index);option.textContent=doc.name;choose.append(option);});
      choose.addEventListener('change',()=>displayDocument(row.documents[Number(choose.value)]));tools.insertBefore(choose,download);
    }
    displayDocument(row.documents?.[0] || row);
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('click', event => { if (event.target===dialog) { const r=dialog.getBoundingClientRect();
      if (event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) close(); } });
    document.body.append(dialog); dialog.showModal(); dismiss.focus({preventScroll:true});
  }
  function decorate(root, rows) {
    if (!root) return;
    const registry = new Map((rows || []).map(row => [row.id, row]));
    const configure = (item, row) => {
      item.title = row.title; item.setAttribute('aria-label', '查看依据 '+row.id);
      if (item.tagName==='BUTTON') item.onclick = () => show(row, item);
    };
    root.querySelectorAll('.answer-citation').forEach(item => {
      const row = registry.get(item.dataset.citationId);
      const external=row && !row.preview && !row.download && !row.readings?.length && row.links?.length===1 && safeLink(row.links[0]);
      if (!row || (item.tagName==='A')!==!!external || (external && item.getAttribute('href')!==row.links[0])) {
        item.replaceWith(document.createTextNode(item.textContent));
      } else configure(item, row);
    });
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: node => node.parentElement?.closest('a,button,pre,code,script,style,math,.katex') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
    });
    const nodes=[]; while(walker.nextNode()) nodes.push(walker.currentNode);
    const pattern = /[〔\[]\s*([A-Z]{1,6}\d{2,}(?:\s*[,，、;；/]\s*[A-Z]{1,6}\d{2,})*)\s*[〕\]]|[（(]\s*((?:CK|LR|C|T|P|S|E|G|D|R|A|W)\d{2,}(?:\s*[,，、;；/]\s*(?:CK|LR|C|T|P|S|E|G|D|R|A|W)\d{2,})*)\s*[）)]/g;
    for (const node of nodes) {
      const text=node.textContent; let cursor=0, changed=false; const fragment=document.createDocumentFragment();
      for (const match of text.matchAll(pattern)) {
        const ids=(match[1] || match[2]).match(/[A-Z]{1,6}\d{2,}/g);
        if (!ids.some(id => registry.has(id))) continue;
        fragment.append(document.createTextNode(text.slice(cursor, match.index)));
        ids.forEach(id => {
          const row=registry.get(id);
          if (!row) { fragment.append(document.createTextNode('〔'+id+'〕')); return; }
          const external=!row.preview && !row.download && !row.readings?.length && row.links?.length===1 && safeLink(row.links[0]);
          const item=external ? link('〔'+id+'〕',row.links[0]) : document.createElement('button');
          if (!external) { item.type='button'; item.textContent='〔'+id+'〕'; }
          item.className='answer-citation'; item.dataset.citationId=id; configure(item,row); fragment.append(item);
        });
        cursor=match.index+match[0].length; changed=true;
      }
      if (changed) { fragment.append(document.createTextNode(text.slice(cursor))); node.replaceWith(fragment); }
    }
  }
  return {decorate, context(value) { if (currentContext!==value) close(); currentContext=value; }};
})();
