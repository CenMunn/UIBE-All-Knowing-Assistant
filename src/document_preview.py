"""已保存DOCX字节的隔离只读预览，不调用Office或外部服务。"""
import base64
from html import escape
from secrets import token_urlsafe

ASSETS={'docx-preview.0.4.1.min.js','jszip.3.10.1.min.js','docx-reader.js'}


def docx_preview_page(name,data):
    nonce=token_urlsafe(24)
    content=('''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+escape(name)+'''</title>
<style>body{margin:0;color:#263752;background:#eaf0f8;font-family:Arial,"宋体",sans-serif}
header{position:sticky;top:0;z-index:5;padding:10px 14px;background:#fff;border-bottom:1px solid #d9e2f0;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
button{border:1px solid #ccd9ef;background:#f3f6fc;color:#253d95;border-radius:7px;padding:6px 10px;cursor:pointer}
button:focus-visible{outline:3px solid #7595e5}#status{font-size:13px}#viewport{overflow:auto;height:calc(100vh - 110px)}
#scaled{width:max-content;margin:0 auto;transform-origin:top left}#document .docx-wrapper{padding:18px;background:transparent}
#note{margin:0;padding:8px 14px;font-size:12px;background:#fff;color:#687994}
</style></head><body><header><strong>已保存副本</strong><button id="minus" aria-label="缩小">−</button><span id="scale">100%</span><button id="plus" aria-label="放大">＋</button><button id="fit">适应宽度</button><span id="status" role="status">正在读取文档…</span></header>
<p id="note">浏览器近似排版；正式提交和打印请核对下载的Word文件。</p>
<div id="viewport"><div id="scaled"><div id="document"></div></div></div>
<script id="document-data" type="application/octet-stream">'''+base64.b64encode(data).decode()+'''</script>
<script nonce="'''+nonce+'''" src="/documents/assets/jszip.3.10.1.min.js"></script>
<script nonce="'''+nonce+'''" src="/documents/assets/docx-preview.0.4.1.min.js"></script>
<script nonce="'''+nonce+'''" src="/documents/assets/docx-reader.js"></script></body></html>''')
    csp=("default-src 'none'; script-src 'nonce-"+nonce+"'; style-src 'unsafe-inline'; img-src data: blob:; "
         "font-src data: blob:; connect-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; "
         "frame-ancestors 'self'; sandbox allow-scripts")
    return content.encode('utf8'),csp
