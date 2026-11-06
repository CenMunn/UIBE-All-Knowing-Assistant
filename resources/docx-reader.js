(() => {
  const status=document.getElementById('status'),viewport=document.getElementById('viewport');
  const scaled=document.getElementById('scaled');let zoom=1;
  function setZoom(value){zoom=Math.min(2,Math.max(.25,value));scaled.style.zoom=String(zoom);document.getElementById('scale').textContent=Math.round(zoom*100)+'%';}
  document.getElementById('plus').onclick=()=>setZoom(zoom+.1);
  document.getElementById('minus').onclick=()=>setZoom(zoom-.1);
  function fit(){setZoom((viewport.clientWidth-24)/(scaled.scrollWidth||800));}
  document.getElementById('fit').onclick=fit;
  try {
    const encoded=document.getElementById('document-data').textContent.trim();
    const binary=atob(encoded),bytes=new Uint8Array(binary.length);
    for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
    docx.renderAsync(bytes,document.getElementById('document'),null,{
      inWrapper:true,breakPages:true,ignoreLastRenderedPageBreak:false,
      renderAltChunks:false,renderChanges:false,renderComments:false,
      useBase64URL:true,experimental:false
    }).then(()=>{
      // 旧Word自动宽度表的缓存网格可能大于纸张内容区；仅在预览中按比例适配。
      document.querySelectorAll('#document section.docx').forEach(page=>{
        const style=getComputedStyle(page),room=page.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
        page.querySelectorAll('table').forEach(table=>{
          if(table.offsetWidth<=room+1)return;
          const cols=[...table.querySelectorAll(':scope > colgroup > col')];
          const widths=cols.map(c=>parseFloat(c.style.width)||1),sum=widths.reduce((a,b)=>a+b,0);
          cols.forEach((c,i)=>c.style.width=(100*widths[i]/sum)+'%');
          table.style.width='100%';table.style.tableLayout='fixed';
          table.querySelectorAll('td,th').forEach(c=>c.style.width='auto');
        });
        page.querySelectorAll('td p').forEach(p=>{
          const ps=getComputedStyle(p),margin=parseFloat(ps.marginInlineStart),indent=parseFloat(ps.textIndent);
          if(indent<0&&margin>p.parentElement.clientWidth){
            p.style.marginInlineStart=Math.max(0,margin+indent)+'px';p.style.textIndent='0px';
          }
        });
      });
      document.querySelectorAll('#document a').forEach(a=>{a.removeAttribute('href');a.removeAttribute('target');});
      status.textContent='预览已更新';fit();
    }).catch(()=>{status.textContent='此文档暂无法预览，请关闭弹窗并下载文件查看。';});
  }catch(e){status.textContent='预览组件未能加载，请关闭弹窗后重试，或下载查看。';}
})();
