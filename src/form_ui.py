"""固定字段填写视图；HTML值全部转义，操作仍在服务端校验。"""
from html import escape as esc
import base64
import sqlite3
from document_models import DocumentError
from document_routes import issue_document_token


def button(label,action,**data):
    attrs=' '.join('data-'+k.replace('_','-')+'="'+esc(str(v),quote=True)+'"' for k,v in data.items())
    return f'<button type="button" data-doc-action="{action}" {attrs}>{esc(label)}</button>'


def format_controls(style,scope,label,*,inherit=False,font_only=False):
    parts=['<div class="format-controls">']
    for key,title,choices in [('font_size','字号',[(10.5,'五号 · 10.5'),(11,'11'),(12,'小四 · 12'),(14,'四号 · 14'),(16,'三号 · 16')]),
        ('line_spacing','行距',[(1,'单倍'),(1.15,'1.15倍'),(1.5,'1.5倍'),(2,'双倍')]),
        ('alignment','对齐',[('original','原表'),('left','左对齐'),('center','居中'),('right','右对齐')])]:
        if font_only and key!='font_size':continue
        value=style.get(key,'')
        parts.append('<label>'+title+'<select aria-label="'+esc(label+title,quote=True)+'" data-layout-scope="'+esc(scope,quote=True)+'" data-layout-property="'+key+'">')
        if inherit:parts.append('<option value="">跟随统一设置</option>')
        for choice,text in choices:
            selected=value==choice or str(value)==str(choice)
            parts.append('<option value="'+str(choice)+'"'+(' selected' if selected else '')+'>'+text+'</option>')
        parts.append('</select></label>')
    return ''.join(parts)+'</div>'


def render_field(field,session,*,formatting=True):
    key=field['id'];value=session.values.get(key,'');fid='form-field-'+key
    attrs=f'id="{fid}" data-form-key="{key}" maxlength="{field["max_chars"]}"'
    parts=['<div class="form-field'+(' wide' if field['kind']=='multiline' else '')+'"><label for="'+fid+'">'+esc(field['label'])+'</label>']
    if field['kind']=='multiline':parts.append(f'<textarea {attrs} rows="5">'+esc(value)+'</textarea>')
    elif field['kind']=='choice':
        parts.append('<select '+attrs+'><option value="">请选择</option>'+''.join('<option'+(' selected' if o==value else '')+'>'+esc(o)+'</option>' for o in field['options'])+'</select>')
    else:parts.append('<input '+attrs+' type="'+('date' if field['kind']=='date' else 'text')+'" value="'+esc(value,quote=True)+'">')
    if formatting:
        parts.append('<details class="field-format" data-document-details="format-'+key+'"><summary>此栏排版</summary>'+format_controls(session.layout['fields'].get(key,{}),key,field['label'],inherit=True,font_only='paragraph' in field['target'])+'</details>')
    return ''.join(parts)+'</div>'


def render_form(service,session):
    form=session.form;name=session.file_name;conflict=False
    try:name=service.store.get(session.ref).name
    except (DocumentError,OSError,sqlite3.Error):conflict=True
    state='未保存' if session.dirty else '已保存'
    parts=['<section class="file-workspace"><div class="file-top"><div><p class="file-eyebrow">个人副本</p><h2>'+esc(name)+'</h2></div><span class="file-save-state" role="status">'+state+'</span></div>',
        '<div class="file-toolbar">'+button('← 文件管理','leave',target='files')+button('从个人资料填入','profile')+button('保存','save')+button('保存并预览','preview-form')+button('下载','download')+button('发送邮件','mail')+'</div>',
        '<p class="file-note">'+esc(form['notes'])+'</p>']
    if conflict:
        parts.append('<p class="file-note" role="alert">文件已改变或暂不可读取。此处草稿仍保留，不会覆盖新版本；请先复制需要的文字，再通过“文件管理”选择不保存离开，重新打开最新文件。</p>')
    if session.layout_upgrade:parts.append('<p class="file-note">这是旧版副本。下次保存或预览将更新填写排版，已填信息与旧版本保留。</p>')
    formatting=form['format']=='docx'
    if formatting:
        parts.append('<details class="layout-panel" data-document-details="layout"><summary>排版设置</summary>'+format_controls(session.layout['defaults'],'defaults','统一')+
            '<label class="layout-grow"><input type="checkbox" data-layout-grow'+(' checked' if session.layout['grow_rows'] else '')+'>填写行随内容自动增高</label>'+
            '<p class="file-note">只调整填写区域，不更改表头和审批内容。调整字号或行高可能改变页数；原表规定的字号与页数要求仍需遵守。</p></details>')
    if form.get('photo'):
        parts.append('<div class="file-toolbar"><span class="file-photo-status">照片：'+('已加入草稿' if session.photo else '未选择')+'</span>'+button('应用下方选择的照片','apply-photo')+button('移除照片','remove-photo')+'</div>')
        if session.photo:parts.append('<img class="form-photo-preview" alt="当前个人照片预览" src="data:image/png;base64,'+base64.b64encode(session.photo).decode('ascii')+'">')
    parts.append('<div class="fixed-form" data-template="'+esc(form['id'])+'">')
    for field in form['fields']:
        if field.get('group')!='awards':parts.append(render_field(field,session,formatting=formatting))
    awards=[f for f in form['fields'] if f.get('group')=='awards']
    if awards:
        parts.append('<fieldset class="award-fields wide"><legend>主要获奖情况</legend>')
        for number in sorted({f['record'] for f in awards}):
            parts.append('<section class="award-entry"><h3>获奖记录 '+str(number)+'</h3><div class="award-columns">')
            parts.extend(render_field(f,session,formatting=formatting) for f in awards if f['record']==number)
            parts.append('</div></section>')
        parts.append('</fieldset>')
    parts.append('</div><p class="file-note">表头、合并结构和审核区域保留原样。此处为填写视图，导出后仍须核对打印分页与手签要求。</p></section>')
    return ''.join(parts)


def mount_form_panel(service):
    # 填写视图共用文档桥接组件，照片上传由原生Gradio控件处理。
    return dict(render=render_form)
