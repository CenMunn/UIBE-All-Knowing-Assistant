"""文件管理、原表预览和创建副本入口。"""
from dataclasses import asdict
from html import escape as esc
from pathlib import Path
from uuid import uuid4
import gradio as gr
from campus_navigation import apply_navigation
from document_models import FileRef
from document_routes import issue_document_token
from form_catalog import get_form,template_display_name
from form_ui import button,render_form
from form_session import begin_form
from project_paths import RESOURCE_DIR


def open_template_in_manager(app,template_id):
    if not isinstance(template_id,str) or not template_id.startswith('TPL'):raise ValueError('未知模板。')
    if app.documents.dirty:raise ValueError('请先保存或放弃当前填写内容。')
    app.documents.selected_template=template_id
    app.documents.create_nonce=uuid4().hex
    return {'template_id':template_id}


def create_copy_in_manager(app,service,template_id,name,nonce):
    with app.lock:
        if app.navigation.workspace!='files' or app.documents.selected_template!=template_id:
            raise ValueError('请先在文件管理中选择模板，再确认创建。')
        record=service.create_from_template(template_id,name,nonce)
        if app.documents.ref!=record.ref:begin_form(service,app.documents,record.ref)
        return record


def template_card(form):
    status={'fillable':'指定位置填写','revision_review':'待修订校对','conversion_required':'待格式转换','unsupported':'仅下载与预览'}[form['status']]
    name=template_display_name(form)
    return '<article class="file-template" title="'+esc(form['name'],quote=True)+'"><span class="file-tag">'+esc(form['scope'])+'</span><h3>'+esc(name)+'</h3><p>'+esc(status)+'</p><p class="file-note">'+esc(form['notes'])+'</p>'+button('查看表格 · '+name,'preview',template=form['id'])+'</article>'


def render_manager(service,session,query=''):
    parts=['<section class="file-workspace"><div class="file-top"><div><h2>文件管理</h2></div>'+button('刷新','refresh')+'</div>',
        '<div class="file-toolbar"><label class="file-search">搜索文件<input data-file-search aria-label="搜索文件" value="'+esc(query,quote=True)+'"></label>'+button('搜索','search')+button('下载所选','batch-download')+button('发送邮件','batch-mail')+button('删除所选','delete-preview')+'</div>']
    selected=session.selected_template
    if selected:
        form=service.template(selected)
        parts.append('<div class="file-create"><h3>'+esc(template_display_name(form))+'</h3><p>在文件管理中创建个人副本，原始模板不变。</p>')
        if form['status']=='fillable':
            parts.append('<label>副本名称<input data-copy-name value="'+esc(form['name'],quote=True)+'"></label>'+button('创建个人副本并填写','create',template=selected,nonce=session.create_nonce))
        else:parts.append('<p>'+esc(form['notes'])+'</p>')
        parts.append(button('取消创建','cancel-create')+'</div>')
    records=service.store.list_files(query)
    parts.append('<h3>个人文件</h3>')
    if not records:parts.append('<div class="files-empty">还没有文件。上传本机文件，或从下方模板创建个人副本。</div>')
    else:
        parts.append('<div class="file-table"><table><thead><tr><th>选择</th><th>文件</th><th>大小</th><th>操作</th></tr></thead><tbody>')
        for record in records:
            ref=record.ref;data=dict(id=ref.id,version=ref.version,revision=ref.revision)
            parts.append('<tr><td><input type="checkbox" class="file-select" aria-label="选择'+esc(record.name,quote=True)+'" data-ref="'+esc(str(ref.id),quote=True)+'" data-version="'+str(ref.version)+'" data-revision="'+str(ref.revision)+'"></td><td>'+esc(record.name)+'</td><td>'+str(round(record.size/1024,1))+' KB</td><td>')
            parts.append(button('预览','preview-file',**data))
            if record.template_id:parts.append(button('继续填写','edit',**data))
            parts.append(button('下载','download-file',**data)+button('重命名','rename-preview',**data)+button('发送邮件','mail-file',**data)+'</td></tr>')
        parts.append('</tbody></table></div>')
    parts.append('<details class="file-templates" open><summary>申请表模板</summary><div class="template-grid">')
    parts.extend(template_card(f) for f in service.catalog.values())
    parts.append('</div></details><p class="file-note">本机共用文件库。普通上传文件可管理、下载与作为附件；仅已适配模板可在线填写。</p></section>')
    return ''.join(parts)


def preview_modal(form,secret):
    original='/documents/content?token='+issue_document_token(secret,form['id'],'template-original')
    parts=['<dialog class="template-modal" aria-label="申请表预览"><div class="modal-top"><h2>'+esc(template_display_name(form))+'</h2>'+button('关闭','close-preview')+'</div>',
           '<p class="file-note">'+esc(form['notes'])+'</p><div class="file-toolbar">'+button('−','zoom-out')+button('＋','zoom-in')+button('适应宽度','zoom-fit')+'<a class="file-link" href="'+esc(original,quote=True)+'" download>下载原件</a>']
    if form['status']=='fillable':parts.append(button('在线填写','choose-template',template=form['id']))
    parts.append('</div>')
    preview=form['preview']
    if preview.get('file'):
        url='/documents/content?token='+issue_document_token(secret,form['id'],'template-preview')
        parts.append('<div class="preview-scroll"><iframe title="原表分页预览" src="'+esc(url,quote=True)+'#zoom=page-width"></iframe></div>')
    else:
        parts.append('<div class="structure-preview"><h3>表格结构示例</h3><p>尚无经校对的原样分页预览，请下载原件查看。</p>'+''.join('<p>'+esc(f['label'])+'</p>' for f in form.get('fields',[]))+'</div>')
    parts.append('</dialog>');return ''.join(parts)


def import_uploaded_paths(service,paths,cache_root,nonce):
    if not isinstance(paths,list) or not 1<=len(paths)<=10:raise ValueError('每次请选择1—10个文件。')
    base=Path(cache_root).resolve();items=[]
    for value in paths:
        path=Path(value)
        if not path.resolve().is_relative_to(base) or path.resolve()!=path.absolute():raise ValueError('上传文件位置无效。')
        with path.open('rb') as stream:content=stream.read(10*1024*1024+1)
        items.append((path.name,content))
    return service.import_files(tuple(items),nonce)


def mount_document_panel(service):
    gr.HTML('<div id="files-surface-host"></div>',js_on_load=None)
    bridge=gr.HTML({'html':'<div class="files-empty">正在读取文件库…</div>','delivery_id':uuid4().hex},
        html_template='<div class="documents-surface" hidden></div><div class="documents-feedback" role="status" aria-live="polite"></div><div class="documents-overlay"></div>',
        js_on_load=(RESOURCE_DIR/'documents.js').read_text(encoding='utf-8'),elem_id='documents',apply_default_css=False,render=False)
    with gr.Group(elem_id='file-upload-box') as upload_box:
        uploads=gr.File(label='上传文件到文件管理',file_count='multiple',type='filepath',file_types=['.doc','.docx','.xls','.xlsx','.pdf','.png','.jpg','.jpeg'],elem_id='documents-upload')
    with gr.Group(visible=False,elem_id='form-photo-box') as photo_box:
        photo=gr.File(label='填写原表照片栏（PNG/JPEG，5MiB以内）',type='filepath',file_types=['.png','.jpg','.jpeg'],elem_id='form-photo')
        remove_photo=gr.Button('移除照片',elem_id='form-remove-photo',visible=False)
    return dict(bridge=bridge,uploads=uploads,upload_box=upload_box,photo=photo,photo_box=photo_box,remove_photo=remove_photo)
