"""文档操作状态机；所有浏览器传入编号都重新核对。"""
from dataclasses import asdict
from html import escape as esc
from uuid import uuid4
from document_models import FileRef
from document_routes import issue_document_token
from document_ui import render_manager,preview_modal,open_template_in_manager,create_copy_in_manager
from form_ui import render_form,button
from form_session import stage_fields,stage_layout,save_form_session,prepare_form_action,request_leave,resolve_leave,fill_from_profile,begin_form
from form_catalog import forms_for_policy


class DocumentController:
    def __init__(self,service,secret):self.service=service;self.secret=secret

    def payload(self,app,message='',modal='',navigate=None,extra=''):
        session=app.documents
        html=render_form(self.service,session) if session.ref else render_manager(self.service,session,session.query)
        return dict(html=html+extra,message=message,modal=modal,dirty=session.dirty,navigate=navigate,
                    photo=bool(session.ref and session.form.get('photo')),editing=session.ref is not None,
                    delivery_id=uuid4().hex)

    def _template_allowed(self,app,key):
        form=self.service.template(key)
        workspace=app.navigation.workspace
        if workspace=='files':return form
        if workspace.startswith('topic:'):
            from campus_pages import topic_page
            page=topic_page(workspace[6:],profile=app.profile)
            source_ids={s.get('source_id',s['id']) for s in page['sections']}
            if source_ids.intersection(form['policy_ids']):return form
        raise ValueError('此模板不在当前资料区域，请从文件管理选择。')

    def _refs(self,action):
        if action.get('id'):
            refs=(FileRef(action['id'],action['version'],action['revision']),)
        else:
            values=action.get('refs',[])
            if not isinstance(values,list) or not 1<=len(values)<=10:raise ValueError('请选择1—10个文件。')
            refs=tuple(FileRef(**v) for v in values)
        for ref in refs:self.service.store.get(ref)
        return refs

    def _download(self,refs):
        parts=['<div class="file-toolbar">']
        for ref in refs:
            record=self.service.store.get(ref)
            url='/documents/content?token='+issue_document_token(self.secret,ref,'download')
            parts.append('<a class="file-link" href="'+esc(url,quote=True)+'" download>'+esc('下载 '+record.name)+'</a>')
        return ''.join(parts)+'</div>'

    def _preview(self,app,ref):
        record=self.service.store.get(ref)
        url='/documents/content?token='+issue_document_token(self.secret,ref,'file-preview')
        if record.media_type in {'application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document'}:
            sandbox=' sandbox="allow-scripts"' if record.name.casefold().endswith('.docx') else ''
            content='<div class="preview-scroll"><iframe title="个人文件预览"'+sandbox+' src="'+esc(url,quote=True)+'#zoom=page-width"></iframe></div>'
        elif record.media_type in {'image/png','image/jpeg'}:
            content='<div class="preview-scroll"><img class="personal-image-preview" alt="个人文件预览" src="'+esc(url,quote=True)+'"></div>'
        else:content='<p>此类型暂不支持在线预览，请下载最新文件查看。</p>'
        modal='<dialog class="template-modal"><div class="modal-top"><h2>'+esc(record.name)+'</h2>'+button('关闭','close-preview')+'</div>'+content+self._download((ref,))+'</dialog>'
        return self.payload(app,modal=modal)

    def handle(self,app,action):
        with app.lock,app.documents.lock:
            if not isinstance(action,dict) or not isinstance(action.get('kind'),str):raise ValueError('文件操作无效。')
            session=app.documents;kind=action['kind'];service=self.service
            # 任一表格动作携带当前字段快照，保存/跳转不得只依赖前端dirty标志。
            if session.ref and 'fields' in action:
                stage_fields(session,action['fields'])
            if session.ref and action.get('layout') is not None:stage_layout(session,action['layout'])
            if kind=='preview':return self.payload(app,modal=preview_modal(self._template_allowed(app,action['template']),self.secret))
            if kind=='close-preview':
                session.pending_delete=();session.pending_rename=None
                return self.payload(app)
            if kind=='choose-template':
                self._template_allowed(app,action['template']);open_template_in_manager(app,action['template'])
                return self.payload(app,navigate='files')
            if app.navigation.workspace!='files':raise ValueError('请先打开文件管理。')
            if kind=='create':
                if action.get('nonce')!=session.create_nonce or action.get('template')!=session.selected_template:raise ValueError('创建请求已改变，请重新选择模板。')
                create_copy_in_manager(app,service,action['template'],action['name'],action['nonce'])
                return self.payload(app,'个人副本已创建并保存。')
            if kind=='cancel-create':session.selected_template=None
            elif kind=='search':session.query=action.get('query','')
            elif kind=='refresh':pass
            elif kind=='draft':return self.payload(app,'表格尚未保存，请先保存或返回文件管理选择放弃。')
            elif kind=='preview-file':
                return self._preview(app,self._refs(action)[0])
            elif kind=='preview-form':return self._preview(app,prepare_form_action(service,session,'preview').ref)
            elif kind=='edit':begin_form(service,session,self._refs(action)[0])
            elif kind=='profile':fill_from_profile(session,app.profile)
            elif kind=='save':save_form_session(service,session)
            elif kind in {'download','mail'}:
                record=prepare_form_action(service,session,kind)
                if kind=='download':return self.payload(app,'文件已保存，请点击下载。',extra=self._download((record.ref,)))
                return self._mail(app,(record.ref,))
            elif kind=='leave':
                target=action.get('target','files')
                if request_leave(session,target):
                    modal='<dialog class="template-modal"><h2>表格尚未保存</h2><p>保存后离开，或放弃本次更改？</p><div class="file-confirm-actions">'+button('保存并离开','leave-save')+button('不保存并离开','leave-discard')+button('返回填写','leave-cancel')+'</div></dialog>'
                    return self.payload(app,modal=modal)
                target=resolve_leave(service,session,'discard');return self.payload(app,navigate=target)
            elif kind in {'leave-save','leave-discard','leave-cancel'}:
                target=resolve_leave(service,session,kind.removeprefix('leave-'))
                return self.payload(app,navigate=target)
            elif kind in {'download-file','batch-download'}:return self.payload(app,extra=self._download(self._refs(action)))
            elif kind in {'mail-file','batch-mail'}:return self._mail(app,self._refs(action))
            elif kind=='rename-preview':
                ref=self._refs(action)[0];session.pending_rename=ref;record=service.store.get(ref)
                modal='<dialog class="template-modal"><h2>重命名文件</h2><label>文件名<input data-rename-name value="'+esc(record.name,quote=True)+'"></label><div class="file-toolbar">'+button('保存名称','rename-confirm')+button('取消','close-preview')+'</div></dialog>'
                return self.payload(app,modal=modal)
            elif kind=='rename-confirm':
                if session.pending_rename is None:raise ValueError('请先选择要改名的文件。')
                service.store.rename(session.pending_rename,action['name']);session.pending_rename=None
            elif kind=='delete-preview':
                refs=self._refs(action);session.pending_delete=refs
                names='、'.join(service.store.get(r).name for r in refs)
                modal='<dialog class="template-modal"><h2>确认删除</h2><p>'+esc(names)+'</p><p>删除后不再显示在个人文件列表中。</p><div class="file-toolbar">'+button('确认删除','delete-confirm')+button('取消','close-preview')+'</div></dialog>'
                return self.payload(app,modal=modal)
            elif kind=='delete-confirm':
                if not session.pending_delete:raise ValueError('请先预览待删除文件。')
                service.store.delete(session.pending_delete);session.pending_delete=()
            elif kind=='mail-merge':
                session.mail_refs=tuple(dict.fromkeys(session.mail_refs+session.pending_mail_refs));session.pending_mail_refs=()
                from mail_service import cancel_mail
                cancel_mail(app.mail)
                if session.ref:
                    request_leave(session,'mail');resolve_leave(self.service,session,'discard')
                return self.payload(app,'附件已加入邮件草稿，尚未发送。',navigate='mail')
            elif kind=='mail-keep':session.pending_mail_refs=()
            else:raise ValueError('未知文件操作。')
            return self.payload(app,'已保存。' if kind in {'save','rename-confirm'} else '')

    def _mail(self,app,refs):
        # 有既有邮件内容时，由web层告知draft_present，不能覆盖收件人或正文。
        session=app.documents
        if getattr(session,'draft_present',False) or session.mail_refs:
            session.pending_mail_refs=refs
            modal='<dialog class="template-modal"><h2>邮件已有草稿</h2><p>是否将所选文件加入现有草稿？不会自动发送。</p><div class="file-toolbar">'+button('合并附件','mail-merge')+button('保留原草稿','mail-keep')+'</div></dialog>'
            return self.payload(app,modal=modal)
        session.mail_refs=refs
        if session.ref:
            request_leave(session,'mail');resolve_leave(self.service,session,'discard')
        return self.payload(app,'附件已加入邮件草稿，尚未发送。',navigate='mail')
