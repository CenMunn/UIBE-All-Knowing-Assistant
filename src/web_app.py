"""贸大百事通助理的本地网页入口；模型调用仍由assistant.py完成。"""

import base64
from html import escape
import os
from pathlib import Path
from urllib.parse import urlparse

import gradio as gr
from openai import OpenAI

import assistant
from app_session import new_app_session, operation, request_profile, resolve_profile, ensure_chat_mutation
from session_profile import Profile, validate_profile, model_background
from profile_ui import mount_profile_ui, profile_summary, guarded_event
from response_delivery import delivery_event
from dataclasses import asdict
from datetime import datetime
from mail_service import (MailSession, read_mail_settings, preview_mail, cancel_mail,
                          deliver_mail, send_mail, save_mail_log)
from reminder_service import ReminderService, ReminderStore
from reminder_ui import mount_reminder_panel
from exam_store import ExamStore
from exam_ui import mount_exam_panel
from teacher_ui import mount_teacher_panel
from teacher_mail import teacher_choices, teacher_contact, fill_teacher_recipient, college_choices
from conversation_store import ConversationStore
from conversation_service import bind_history
from conversation_ui import mount_conversation_controls, bind_conversation_events, archive_values
from chat_edit_ui import mount_chat_edit_ui, bind_chat_edit_ui
import sqlite3
from web_session import (new_session, iter_reply, snapshot, request_settings,
                         resolve_settings, clear_session)


from project_paths import PROJECT_DIR, IMAGE_DIR, RESOURCE_DIR
from ui_assets import image_data
from campus_navigation import apply_navigation, leaf, TOOLS
from campus_navigation_ui import mount_navigation, browse_menu, navigation_payload
from materials_ui import render_materials,materials_breadcrumb
from campus_pages import topic_page, render_topic, policy_question
from campus_presentation import SHORTCUTS, prepare_suggestion
from document_ui import mount_document_panel, import_uploaded_paths
from document_controller import DocumentController
from form_session import stage_fields, stage_layout, update_fields, renew_form
from document_validation import validate_photo
from mail_attachments import resolve_attachments, deliver_managed_mail
from timetable_ui import (mount_timetable_panel, bind_timetable_events, mount_task_source,
    bind_task_source, task_source_payload)


def header_html():
    logo = base64.b64encode((IMAGE_DIR/'uibe-logo.png').read_bytes()).decode('ascii')
    return ('<a class="skip-link" href="#module-navigation">跳到功能导航</a>'
            '<header class="college-header">'
            '<button id="navigation-toggle" type="button" aria-label="校园功能导航" aria-expanded="false" aria-controls="settings-panel">☰</button>'
            f'<img src="data:image/png;base64,{logo}" width="180" height="129" '
            'alt="对外经济贸易大学校徽及中英文校名">'
            '<div class="college-heading"><h1>贸大百事通助理</h1>'
            '<p>课程、学习与校园事务</p></div>'
            '<span class="local-label">本地运行</span></header>')


def render_frame(frame, capabilities=None, *, model_catalog=None):
    capabilities=capabilities or dict(web_available=False,thinking_available=False,web_reason='当前端点未配置联网。',thinking_reason='当前模型未配置思考模式。')
    record = frame['record'] or {}
    settings = frame['settings']
    if model_catalog is not None:capabilities=model_catalog.capabilities(settings['model'])
    busy = frame['busy']
    pending = bool(frame['pending'])
    editing = bool(frame.get('edit_view', {}).get('editing'))
    enabled = not (busy or pending or editing)
    labels = {'success': '回答完成', 'error': '请求未完成', 'incomplete': '回答不完整，未计入历史'}
    if record.get('status') == 'incomplete' and (record.get('error_message') or '').startswith('来源核验未通过'):
        labels['incomplete'] = '来源核验未通过，未计入历史'
    status = frame['status']
    if record and not busy and status == '尚未发送请求':
        status = record.get('status', status)
    status = labels.get(status, status)
    if frame.get('capability_notice') and frame['capability_notice'] not in status:
        status+='；'+frame['capability_notice']
    if record.get('status') == 'error' and record.get('error_message'):
        status += '：' + record['error_message']
    if record and not record.get('log_saved', False):
        status += '；日志未保存，请检查本地目录权限'
    if record.get('dropped_rounds', 0):
        status += f'；已从模型上下文裁剪最早的{record["dropped_rounds"]}轮历史，页面记录仍保留'
    if record.get('cleanup_warning'):
        status += '；' + record['cleanup_warning']
    if frame.get('archive_error'):
        status += '；' + frame['archive_error']
    feature=record.get('feature_run')
    if feature and not busy and feature['search_status'] in ('error','empty'):
        hint={'http_401':'检索接口认证失败','http_402':'检索接口报告余额不足'}.get(feature['error_code'],'联网未取得可用资料')
        status+='；'+hint+'，未核实最新信息'

    def seconds(key):
        value = record.get(key)
        return '—' if value is None else f'{value:.2f}秒'

    timing = (f'首段收到耗时：{seconds("first_text_seconds")}　|　'
              f'总耗时：{seconds("total_seconds")}　|　历史：{frame["history_rounds"]}轮') if record else (
                  status if busy or status != '尚未发送请求' else '')
    if record:timing+='　|　实际调用模型：'+(record.get('model') or '旧记录未保存')
    feature=record.get('feature_run')
    if feature:
        search_labels=dict(disabled='未开启',local_only='本地已覆盖，未检索',used='已提供联网摘录',empty='未取得可用摘录',error='检索失败，未核实最新',blocked='未检索：需明确公开主题')
        timing += ('　|　模式：'+('深度思考' if feature['thinking'] else '普通回答')+'　|　联网：'+search_labels[feature['search_status']]
                   +f"　|　Search {feature['search_requests']} / Fetch {feature['fetch_requests']}　|　首响应：{seconds('first_response_seconds')}")
        usage=record.get('usage')
        if usage:timing+='　|　用量(tokens)：'+str(usage)
    reasons=[capabilities.get(k,'') for k in ('web_reason','thinking_reason') if capabilities.get(k)]
    hint='；'.join(reasons) or ('深度思考可能耗时更久' if settings.get('deep_thinking') else '')
    values = {
        'chat': [dict(message, content=escape(message['content'], quote=False)) if message['role'] == 'user'
                 else message for message in frame['messages']],
        'status': (f'<div role="status" aria-live="polite">{escape(status if record or busy or status != "尚未发送请求" else "")}</div>'
                   + (f'<details class="run-details"><summary>运行详情</summary><p>{escape(timing)}</p>'
                      '<small>Python端测量，不包含完整浏览器渲染时间。</small></details>' if record else '')),
        'send': gr.update(interactive=enabled, value='正在回答…' if busy else '发送'),
        'question': gr.update(interactive=enabled),
        'prompt': gr.update(value=settings['prompt_version'], interactive=enabled),
        'stream': gr.update(value=settings['stream'], interactive=enabled),
        'history': gr.update(value=settings['history_enabled'], interactive=enabled),
        'temperature': gr.update(value=settings['temperature'], interactive=enabled and not settings.get('deep_thinking'),label='温度（深度思考时不生效）' if settings.get('deep_thinking') else '温度'),
        'clear': gr.update(interactive=enabled),
        'confirmation': gr.update(visible=frame['pending'] in ('settings','clear')),
        'web_feature':gr.update(value=settings.get('web_enabled',False),interactive=enabled and capabilities['web_available']),
        'thinking_feature':gr.update(value=settings.get('deep_thinking',False),interactive=enabled and capabilities['thinking_available']),
        'feature_confirmation':gr.update(visible=frame['pending']=='features'),
        'feature_hint':'<span>'+escape(hint)+'</span>',
        'edit_tools': frame.get('edit_view', {'nodes': [], 'token': '', 'action': None}),
    }
    if model_catalog is not None:
        from chat_model_ui import model_updates
        values.update(model_updates(frame,model_catalog))
    return values


def resolve_action(session, accepted):
    if session.pending_action not in ('settings','clear'):raise ValueError('当前没有待确认的会话重置。')
    if session.pending_action == 'clear' and accepted:
        clear_session(session, True)
    else:
        resolve_settings(session, accepted)


def mail_preview_html(draft):
    attachments = '<ul>'+''.join('<li>'+escape(a.name)+' · '+str(round(a.size/1024,1))+' KB</li>' for a in draft.attachments)+'</ul>' if draft.attachments else '<p>无附件</p>'
    return ('<section class="mail-preview"><h3>确认这封邮件</h3>'
            f'<p>发件人：{escape(draft.sender)}<br>收件人：{escape(draft.recipient)}<br>'
            f'主题：{escape(draft.subject)}</p><pre>{escape(draft.body)}</pre>'+attachments+'</section>')


def mail_result_html(record):
    labels = {'accepted': '服务器已接受', 'failed': '发送失败', 'unknown': '发送状态不明'}
    text = labels.get(record['status'], record['status']) + '：' + record['detail']
    if record.get('total_seconds') is not None:
        text += f' 耗时：{record["total_seconds"]:.2f}秒。'
    if record.get('message_id'):
        text += f' 邮件编号：{record["message_id"]}'
    if record.get('log_saved') is False:
        text += ' 本地邮件日志未保存。'
    return f'<div role="status" aria-live="polite">{escape(text)}</div>'


def self_test_fields(mail_settings):
    if mail_settings is None:
        raise ValueError('请先配置发件邮箱。')
    now = datetime.now(assistant.BEIJING).strftime('%Y-%m-%d %H:%M:%S %z')
    return (mail_settings.username, '[实验2] 信息学院助理邮件发送测试',
            '这是一封发给本人邮箱的课程实验测试邮件。\n'
            '用于验证本地网页的邮件编辑、预览确认及 SMTP 发送流程。\n'
            f'测试时间：{now}\n无需回复。')


def switch_workspace(module):
    is_topic = isinstance(module, str) and module.startswith('topic:')
    is_category=isinstance(module,str) and module.startswith('category:')
    if not is_topic and not is_category and module not in TOOLS:
        raise ValueError('未知功能模块。')
    # 只改变可见性，不重建控件、不读写会话，也不触发发送或提醒操作。
    title = leaf(module[6:])['label'] if is_topic else ('资料卡片' if is_category else TOOLS[module])
    return [gr.update(visible=True if module == name else 'hidden',
                      elem_classes=[] if module == name else ['module-inactive'])
            for name in ('chat', 'mail', 'reminders', 'exams', 'teachers', 'chat')] + [
        gr.update(variant='primary' if module == name else 'secondary')
        for name in ('chat', 'mail', 'reminders', 'exams', 'teachers')] + [
        f'<span role="status" aria-live="polite" aria-atomic="true">当前功能：{title}</span>']


def build_app(model, extra_body, client_factory, mail_settings=None,
              mail_sender=send_mail, mail_logger=save_mail_log, reminder_service=None,
              exam_store=None, exam_service=None, conversation_store=None, conversation_error='',
              document_service=None, document_secret=None, timetable_controller=None, *, search_factory=None, feature_capabilities=None, model_catalog=None):
    import secrets
    from chat_feature_ui import mount_feature_controls, bind_feature_controls
    capabilities=feature_capabilities or dict(web_available=False,thinking_available=False,web_reason='当前端点未配置联网。',thinking_reason='当前模型未配置思考模式。')
    from model_catalog import ModelCatalog
    from chat_model_ui import mount_model_controls,bind_model_controls,model_updates
    directory=model_catalog or ModelCatalog(client_factory,'',model)
    if model_catalog is not None:capabilities=model_catalog.capabilities(model)
    document_controller = DocumentController(document_service, document_secret or secrets.token_bytes(32)) if document_service else None
    def initial_session():
        current = new_app_session(model, extra_body)
        bind_history(current.chat, conversation_store, owner_key=None)
        return current
    with gr.Blocks(title='贸大百事通助理', analytics_enabled=False, fill_width=True) as app:
        session = gr.State()
        initialized = app.load(initial_session, [], session, queue=False, api_name=False, show_progress='hidden')
        revision = gr.Number(value=0, visible=False, elem_id='profile-revision')
        gr.HTML(header_html(), elem_id='college-brand')
        if conversation_error:
            gr.HTML('<div role="alert">' + escape(conversation_error) + '</div>', elem_id='history-unavailable')
        with gr.Row(elem_id='workspace'):
            with gr.Column(scale=0, min_width=240, elem_id='settings-panel'):
                gr.Markdown('## 工作台', elem_id='navigation-title')
                with gr.Column(elem_id='module-navigation', min_width=0):
                    navigation = mount_navigation()
                    nav_chat = navigation['chat']
                    nav_teachers, nav_mail, nav_reminders, nav_exams = (
                        navigation['tools'][k] for k in ('teachers','mail','reminders','exams'))
                module_status = gr.HTML(switch_workspace('chat')[-1], elem_id='module-status')
                with gr.Column(min_width=0, elem_id='profile-entry'):
                    profile_status = gr.HTML(profile_summary(Profile()), elem_id='profile-status', js_on_load=None)
                    profile_open = gr.Button('个人资料', elem_id='profile-open')
            with gr.Column(visible=True, scale=0, elem_id='chat-settings', min_width=0) as chat_settings:
                archive = mount_conversation_controls(conversation_store)
            with gr.Column(scale=1, min_width=240, elem_id='main-panel'):
                with gr.Column(visible=True, elem_id='chat-workspace', min_width=0) as chat_workspace:
                    with gr.Row(elem_id='chat-toolbar'):
                        gr.Button('会话', scale=0, min_width=44, elem_id='history-toggle')
                        archive['heading'].render()
                        gr.Button('设置', scale=0, min_width=60, elem_id='chat-options-toggle')
                    with gr.Column(elem_id='chat-options', min_width=0):
                        gr.Markdown('### 对话设置')
                        prompt = gr.Dropdown(list(assistant.PROMPT_VERSIONS), value='campus_v7', label='Prompt 版本',
                                             allow_custom_value=False, elem_id='prompt-version')
                        stream = gr.Checkbox(value=True, label='流式输出', elem_id='stream-setting')
                        history = gr.Checkbox(value=True, label='携带上下文', elem_id='history-setting')
                        gr.Markdown('控制模型是否参考前文，不影响本机聊天保存。', elem_classes='subtle')
                        temperature = gr.Slider(0, 2, value=.3, step=.1, label='温度', elem_id='temperature-setting')
                        clear = gr.Button('清空当前对话', elem_id='clear-chat')
                        with gr.Accordion('使用与隐私说明', open=False, elem_id='chat-help'):
                            gr.Markdown('更改温度、上下文或 Prompt 前，会确认是否清空对话。'
                                '已填学号的聊天按学号保存在本机；游客不共享、不持久保存。自填学号不是身份认证，其他工具暂为本机共用。请勿输入密码或密钥。'
                                '\n\n耗时由 Python 端测量，不包含完整的浏览器渲染时间。'
                                '刷新后可从历史列表手动打开已保存对话；个人资料需重新填写。'
                                '关闭页面不等于取消平台处理。清空对话会清空当前会话的全部消息版本，不删除原实验日志。'
                                '\n\n邮件须预览确认后发送；提醒须在独立区域确认创建；联网补充由输入区开关与授权控制，仅查公开官网，不登录个人教务。', elem_classes='measurement-note')
                    chat = gr.Chatbot(value=[], label='对话', height=400, elem_id='chat',
                        placeholder=f'<div class="welcome"><img src="{image_data("assistant-avatar.png")}" alt="贸小通">'
                                    '<h2>你好，我是贸小通</h2><p>从课程、学习，聊到你的校园生活。</p></div>',
                        avatar_images=tuple({'path': image_data(name), 'url': image_data(name)}
                                            for name in ('user-avatar.png', 'assistant-avatar.png')),
                        show_label=False,
                        sanitize_html=True, allow_tags=False, allow_file_downloads=False,
                        buttons=['copy'], layout='bubble', group_consecutive_messages=False, autoscroll=False)
                    edit_tools = mount_chat_edit_ui()
                    with gr.Column(elem_id='welcome-panel', min_width=0):
                        gr.HTML(f'<div class="welcome"><img src="{image_data("assistant-avatar.png")}" alt="贸小通">'
                                '<h2>你好，我是贸小通</h2><p>从课程、学习，聊到你的校园生活。</p></div>', js_on_load=None)
                        with gr.Row(elem_id='welcome-shortcuts'):
                            shortcuts = [gr.Button(label, elem_id='welcome-shortcut-'+str(index), size='sm')
                                         for index, (label, _) in enumerate(SHORTCUTS)]
                    with gr.Group(visible=False, elem_id='reset-confirmation') as confirmation:
                        confirm_text = gr.Markdown('确认清空当前会话的全部消息版本、模型历史及本机聊天档案内容？原实验日志保留。')
                        with gr.Row():
                            confirm = gr.Button('确认清空并继续', variant='primary')
                            cancel = gr.Button('取消，保留会话')
                    feature_controls=mount_feature_controls(capabilities)
                    with gr.Column(elem_id='composer',min_width=0):
                        question = gr.Textbox(label='输入问题', show_label=False, placeholder='向贸小通提问…',
                            lines=2, max_lines=5, scale=1, elem_id='question', interactive=True)
                        with gr.Row(elem_id='composer-actions'):
                            model_controls=mount_model_controls(directory)
                            send = gr.Button('发送', variant='primary', scale=0, min_width=64, elem_id='send')
                    model_hint=gr.HTML(model_updates(snapshot(new_session(model,extra_body)),directory)['model_hint'],elem_id='model-catalog-hint')
                    status = gr.HTML(render_frame(snapshot(new_session(model, extra_body)))['status'], elem_id='run-status')
                with gr.Column(visible='hidden', elem_id='mail-panel', elem_classes=['module-inactive'], min_width=0) as mail_workspace:
                    gr.Markdown('## 发送邮件')
                    mail_session = session
                    gr.Markdown('写好邮件，预览核对后再发送。支持单个收件人。', elem_classes='workspace-intro')
                    gr.Textbox(value=mail_settings.username if mail_settings else '尚未配置发件邮箱',
                               label='发件邮箱', interactive=False, elem_id='mail-from')
                    mail_to = gr.Textbox(label='收件邮箱', placeholder='完整邮箱地址', elem_id='mail-to')
                    with gr.Accordion('快捷填写教师邮箱', open=True, elem_id='teacher-mail-picker'):
                        with gr.Row():
                            mail_teacher_college = gr.Dropdown(college_choices(), label='学院', value='',
                                allow_custom_value=False, filterable=True, elem_id='mail-teacher-college')
                            mail_teacher = gr.Dropdown(teacher_choices(), label='选择教师', value=None,
                                allow_custom_value=False, filterable=True, elem_id='mail-teacher')
                            teacher_email = gr.Dropdown([], label='公开邮箱', value=None,
                                allow_custom_value=False, interactive=False, elem_id='mail-teacher-email')
                        teacher_source = gr.HTML('', elem_id='mail-teacher-source')
                        teacher_fill = gr.Button('填入收件邮箱', interactive=False, elem_id='mail-teacher-fill')
                    mail_subject = gr.Textbox(label='邮件主题', max_lines=1, elem_id='mail-subject')
                    mail_body = gr.Textbox(label='邮件正文', lines=7, max_lines=16, elem_id='mail-body')
                    with gr.Accordion('附件', open=True, elem_id='mail-attachments'):
                        mail_upload = gr.File(label='拖拽或选择本机文件',file_count='multiple',type='filepath',
                            file_types=['.doc','.docx','.xls','.xlsx','.pdf','.png','.jpg','.jpeg'],elem_id='mail-attachment-upload')
                        mail_file_select = gr.Dropdown([],value=[],multiselect=True,label='从文件管理选择附件',elem_id='mail-file-select')
                        mail_files_refresh = gr.Button('刷新文件管理列表',elem_id='mail-files-refresh')
                        mail_attachment_list = gr.HTML('<p class="file-note">尚未添加附件。</p>',elem_id='mail-attachment-list')
                    with gr.Row():
                        fill_test = gr.Button('填入发给自己的测试邮件', elem_id='mail-self-test')
                        mail_preview = gr.Button('预览邮件', variant='primary', elem_id='mail-preview-button')
                    with gr.Group(visible=False, elem_id='mail-confirmation') as mail_confirmation:
                        mail_preview_text = gr.HTML()
                        with gr.Row():
                            mail_confirm = gr.Button('确认发送邮件', variant='primary', elem_id='mail-confirm')
                            mail_cancel = gr.Button('取消，继续编辑', elem_id='mail-cancel')
                    ready = bool(mail_settings and mail_settings.password)
                    mail_status = gr.HTML('<div role="status">' + (
                        '发件配置已读取，尚未发送邮件。' if ready else
                        '请在本机配置邮箱凭据并重新启动；密码不要填入聊天或邮件正文。') + '</div>', elem_id='mail-status')
                with gr.Column(visible='hidden', elem_id='reminder-workspace', elem_classes=['module-inactive'], min_width=0) as reminder_workspace:
                    gr.Markdown('## 定时提醒')
                    task_source = mount_task_source()
                    reminder_binding = mount_reminder_panel(reminder_service, app, standalone=True, app_state=session, revision=revision)
                with gr.Column(visible='hidden', elem_id='exam-workspace', elem_classes=['module-inactive'], min_width=0) as exam_workspace:
                    exam_binding = mount_exam_panel(exam_store, exam_service, session, revision)
                with gr.Column(visible='hidden', elem_id='teacher-workspace', elem_classes=['module-inactive'], min_width=0) as teacher_workspace:
                    mount_teacher_panel()
                with gr.Column(visible='hidden', elem_id='files-workspace', elem_classes=['module-inactive'], min_width=0) as files_workspace:
                    document_parts = mount_document_panel(document_service)
                with gr.Column(visible='hidden', elem_id='timetable-workspace', elem_classes=['module-inactive'], min_width=0) as timetable_workspace:
                    timetable_parts = mount_timetable_panel(timetable_controller,session,revision)
                with gr.Column(visible='hidden', elem_id='topic-workspace', elem_classes=['module-inactive'], min_width=0) as topic_workspace:
                    topic_content = gr.HTML('', elem_id='topic-content', js_on_load=None)
                    topic_action = gr.HTML({}, html_template='', elem_id='topic-card-action',
                        js_on_load=(RESOURCE_DIR/'topic-cards.js').read_text(encoding='utf-8'))
                    topic_ask = gr.Button('询问此专题的其他问题', elem_id='topic-ask', variant='secondary')
                    with gr.Row(elem_id='topic-tools'):
                        topic_tools = {key: gr.Button(label, visible=False, elem_id='topic-tool-'+key)
                                       for key, label in TOOLS.items() if key != 'chat'}
                with gr.Column(visible='hidden',elem_id='materials-workspace',elem_classes=['module-inactive'],min_width=0) as materials_workspace:
                    materials_content=gr.HTML('',elem_id='materials-content',js_on_load=None)
        archive['confirmation'].render()
        pending_suggestion = gr.State()
        with gr.Column(visible=False, elem_id='suggestion-confirmation', min_width=0) as suggestion_confirmation:
            gr.Markdown('输入框已有草稿，是否替换为所选问题？不会自动发送。')
            with gr.Row():
                suggestion_accept = gr.Button('替换草稿', variant='primary', elem_id='suggestion-accept')
                suggestion_cancel = gr.Button('保留原草稿', elem_id='suggestion-cancel')
        suggestion_status = gr.HTML('', elem_id='suggestion-status')
        profile = mount_profile_ui(session, loading=True)
        document_parts['bridge'].render()
        gr.HTML('<footer>课程实验 · 非学校官方服务</footer>', elem_id='page-footer')

        navigation_outputs = [chat_workspace, mail_workspace, reminder_workspace, exam_workspace, teacher_workspace, chat_settings,
                              nav_chat, nav_mail, nav_reminders, nav_exams, nav_teachers, module_status]
        navigation_outputs += [topic_workspace, topic_content, *topic_tools.values(), files_workspace,
                               timetable_workspace, timetable_parts['bridge'], navigation['tools']['timetable'], task_source, reminder_binding.panel,
                               materials_workspace,materials_content,
                               document_parts['bridge'], document_parts['upload_box'], document_parts['photo_box'], navigation['bridge']]

        def open_workspace(value, current, fixed=None):
            if current is None:
                return [gr.skip()] * (len(navigation_outputs)-1) + [navigation_payload(error='页面正在初始化，请稍后再试。')]
            try:
                action = value.get('action') if isinstance(value, dict) else None
                target = fixed or (action.get('target') if isinstance(action, dict) and action.get('kind') == 'open' else None)
                # 先读取资料再提交导航，读取失败不改变当前工作区。
                page = topic_page(target[6:], profile=current.profile) if isinstance(target, str) and target.startswith('topic:') else None
                content = materials_breadcrumb(target)+render_topic(page) if page else gr.skip()
                is_materials=target=='materials' or (isinstance(target,str) and target.startswith('category:'))
                materials_html=render_materials(target) if is_materials else gr.skip()
                apply_navigation(current, 'open', target, value.get('revision'))
                if target != 'files' and current.documents.ref and not current.documents.dirty:
                    from form_session import request_leave, resolve_leave
                    request_leave(current.documents,target)
                    resolve_leave(document_service,current.documents,'discard')
                module = current.navigation.workspace
                doc_view = document_controller.payload(current) if document_controller and module == 'files' else None
                return switch_workspace(module) + [
                    gr.update(visible=True if page else 'hidden', elem_classes=[] if page else ['module-inactive']),
                    content,
                    *[gr.update(visible=bool(page and key in page['tools'])) for key in topic_tools],
                    gr.update(visible=True if module == 'files' else 'hidden', elem_classes=[] if module == 'files' else ['module-inactive']),
                    gr.update(visible=True if module == 'timetable' else 'hidden', elem_classes=[] if module == 'timetable' else ['module-inactive']),
                    timetable_controller.payload(current) if timetable_controller and module=='timetable' else gr.skip(),
                    gr.update(variant='primary' if module=='timetable' else 'secondary'),
                    task_source_payload(timetable_controller.service.reminder_bridge,current) if timetable_controller else gr.skip(),
                    gr.update(visible=current.timetable.source_reminder is None),
                    gr.update(visible=True if is_materials else 'hidden',elem_classes=[] if is_materials else ['module-inactive']),
                    materials_html,
                    doc_view if doc_view else gr.skip(),
                    gr.update(visible=module == 'files' and not current.documents.ref),
                    gr.update(visible=module == 'files' and bool(current.documents.ref and current.documents.form.get('photo'))),
                    navigation_payload(current)]
            except (ValueError, OSError) as error:
                return [gr.skip()] * (len(navigation_outputs)-1) + [navigation_payload(current,
                    str(error) if isinstance(error, ValueError) else '资料读取失败，请检查本地资料文件。')]

        navigation['bridge'].input(browse_menu, [navigation['bridge'], session], navigation['bridge'],
            queue=False, api_name=False, show_progress='hidden')
        navigation['bridge'].submit(open_workspace, [navigation['bridge'], session], navigation_outputs,
            queue=False, api_name=False, show_progress='hidden')
        for button, module in ((nav_chat, 'chat'), (nav_mail, 'mail'), (nav_reminders, 'reminders'),
                               (nav_exams, 'exams'), (nav_teachers, 'teachers'), (navigation['tools']['files'], 'files'),
                               (navigation['tools']['timetable'], 'timetable'),(navigation['tools']['materials'],'materials'), *[(b,k) for k,b in topic_tools.items()]):
            button.click(lambda value, current, selected=module: open_workspace(value, current, selected),
                         [navigation['bridge'], session], navigation_outputs,
                         queue=False, api_name=False, show_progress='hidden')

        bind_timetable_events(timetable_parts,timetable_controller,session,revision,
            lambda target,current:open_workspace(navigation_payload(current),current,target),navigation_outputs,initialized)
        bind_task_source(task_source,timetable_controller,session,revision,
            lambda target,current:open_workspace(navigation_payload(current),current,target),navigation_outputs)

        def documents_ready(current):
            return document_controller.payload(current) if document_controller else dict(html='<div class="files-empty">文件功能尚未初始化，请按运行说明重启主程序。</div>',delivery_id='unavailable')
        initialized.then(documents_ready, [session], document_parts['bridge'], queue=False, api_name=False, show_progress='hidden')
        document_outputs = [*navigation_outputs, document_parts['uploads'], document_parts['photo'],mail_attachment_list,mail_file_select]

        def mail_attachment_values(current):
            if document_service is None:return '<p class="file-note">文件管理尚未初始化。</p>',gr.update(choices=[],value=[])
            records=document_service.store.list_files()
            current.documents.file_catalog={r.ref.id:r.ref for r in records}
            names=[]
            for ref in current.documents.mail_refs:
                try:
                    record=document_service.store.get(ref)
                    names.append(escape(record.name)+' · '+str(round(record.size/1024,1))+' KB')
                except ValueError:names.append('所选文件已改变，请移除后重新选择。')
            html='<ul>'+''.join('<li>'+n+'</li>' for n in names)+'</ul>' if names else '<p class="file-note">尚未添加附件。</p>'
            return html,gr.update(choices=[(r.name,r.ref.id) for r in records],value=[r.id for r in current.documents.mail_refs if r.id in current.documents.file_catalog])

        def document_response(payload, current):
            target=payload.pop('navigate',None)
            nav=open_workspace(navigation_payload(current),current,target) if target else [gr.skip()]*(len(navigation_outputs)-1)+[navigation_payload(current)]
            nav[-4]=payload
            nav[-3]=gr.update(visible=current.navigation.workspace=='files' and not current.documents.ref)
            nav[-2]=gr.update(visible=current.navigation.workspace=='files' and bool(current.documents.ref and current.documents.form.get('photo')))
            return nav+[gr.update(value=None),gr.update(value=None),*mail_attachment_values(current)]

        def document_event(value,to,subject,body,photo_path,current,version):
            if current is None or document_controller is None:
                raise gr.Error('文件功能未就绪，请稍后重试。')
            try:
                action=value.get('action') if isinstance(value,dict) else None
                if not isinstance(action,dict):raise ValueError('文件操作无效。')
                current.documents.draft_present=bool(to or subject or body)
                if current.documents.ref and 'fields' in action:
                    stage_fields(current.documents,action['fields'])
                if current.documents.ref and action.get('layout') is not None:
                    stage_layout(current.documents,action['layout'])
                if action['kind']=='apply-photo':
                    from pathlib import Path
                    path=Path(photo_path) if photo_path else None
                    if path is None or not path.resolve().is_relative_to(Path(document_parts['photo'].GRADIO_CACHE).resolve()):raise ValueError('请先选择照片。')
                    with path.open('rb') as stream:data=stream.read(5*1024*1024+1)
                    update_fields(current.documents,action.get('fields',{}),validate_photo(data))
                    payload=document_controller.payload(current,'照片已加入草稿，点击保存后生效。')
                elif action['kind']=='remove-photo':
                    update_fields(current.documents,action.get('fields',{}),None)
                    payload=document_controller.payload(current,'照片已移除，请保存。')
                else:payload=document_controller.handle(current,action)
            except (ValueError,OSError,sqlite3.Error,KeyError,TypeError) as error:
                payload=document_controller.payload(current,str(error) if isinstance(error,ValueError) else '文件操作未完成，原保存版本不变。')
            return document_response(payload,current)
        delivery_event(document_parts['bridge'].submit,document_event,
            [document_parts['bridge'],mail_to,mail_subject,mail_body,document_parts['photo'],session,revision],document_outputs,
            queue=False,api_name=False,show_progress='hidden')

        def upload_documents(paths,current,version):
            if document_controller is None:raise gr.Error('文件功能未就绪。')
            try:
                if current.navigation.workspace!='files' or current.documents.ref:raise ValueError('请先返回文件管理再上传。')
                from uuid import uuid4
                import_uploaded_paths(document_service,paths,document_parts['uploads'].GRADIO_CACHE,uuid4().hex)
                payload=document_controller.payload(current,'文件已加入个人文件列表。')
            except (ValueError,OSError) as error:payload=document_controller.payload(current,str(error))
            return document_response(payload,current)
        delivery_event(document_parts['uploads'].upload,upload_documents,[document_parts['uploads'],session,revision],document_outputs,
            queue=False,api_name=False,show_progress='hidden')
        renew_timer=gr.Timer(30,active=document_service is not None)
        def renew_document(current):
            try:
                if current is not None:renew_form(current.documents)
            except ValueError:pass
        renew_timer.tick(renew_document,[session],[],queue=False,api_name=False,show_progress='hidden')

        def refresh_mail_files(current):return mail_attachment_values(current)
        mail_files_refresh.click(refresh_mail_files,[session],[mail_attachment_list,mail_file_select],queue=False,api_name=False,show_progress='hidden')
        def select_mail_files(ids,current,version):
            if document_service is None:raise gr.Error('文件管理尚未就绪。')
            with current.lock:
                try:
                    cancel_mail(current.mail)
                    refs=tuple(current.documents.file_catalog[i] for i in ids)
                    resolve_attachments(document_service.store,refs)
                    current.documents.mail_refs=refs
                except (ValueError,KeyError) as error:raise gr.Error(str(error)) from None
                return *mail_attachment_values(current),gr.update(visible=False),'附件已更新，请重新预览。'
        delivery_event(mail_file_select.input,select_mail_files,[mail_file_select,session,revision],
            [mail_attachment_list,mail_file_select,mail_confirmation,mail_status],queue=False,api_name=False,show_progress='hidden')
        def upload_mail_files(paths,current,version):
            if document_service is None:raise gr.Error('文件管理尚未就绪。')
            with current.lock:
                cancel_mail(current.mail)
                from uuid import uuid4
                records=import_uploaded_paths(document_service,paths,mail_upload.GRADIO_CACHE,uuid4().hex)
                refs=tuple(dict.fromkeys(current.documents.mail_refs+tuple(r.ref for r in records)))
                resolve_attachments(document_service.store,refs)
                current.documents.mail_refs=refs
                return *mail_attachment_values(current),gr.update(visible=False),'附件已加入文件管理及邮件草稿，请预览确认。',gr.update(value=None)
        delivery_event(mail_upload.upload,upload_mail_files,[mail_upload,session,revision],
            [mail_attachment_list,mail_file_select,mail_confirmation,mail_status,mail_upload],queue=False,api_name=False,show_progress='hidden')

        def preview_email(to, subject, body, current):
            try:
                attachments = resolve_attachments(document_service.store,current.documents.mail_refs) if document_service else ()
                draft = preview_mail(current.mail, mail_settings, to, subject, body, attachments=attachments)
                return gr.update(visible=True), mail_preview_html(draft), '请核对收件人、主题和正文，再确认发送。'
            except ValueError as error:
                return gr.update(visible=False), '', escape(str(error))

        def invalidate_email(current):
            try:
                cancel_mail(current.mail)
                return gr.update(visible=False), '草稿已更新，请重新预览。'
            except ValueError as error:
                return gr.update(), escape(str(error))

        suggestion_outputs = [question, pending_suggestion, suggestion_confirmation, suggestion_status, *navigation_outputs]

        def fill_suggestion(existing, pending, current, suggested='', decision='stage'):
            try:
                if current is None:
                    raise ValueError('页面正在初始化，请稍后再试。')
                with current.lock, current.chat.lock:
                    if isinstance(suggested, dict):
                        suggested = policy_question(current, suggested)
                    if suggested == '__topic__':
                        workspace = current.navigation.workspace
                        if not workspace.startswith('topic:'):
                            raise ValueError('请先打开校园专题。')
                        suggested = topic_page(workspace[6:], profile=current.profile)['question']
                    result = prepare_suggestion(current, existing, suggested, pending, decision)
                    nav_updates = [gr.skip()] * (len(navigation_outputs)-1) + [navigation_payload(current)]
                    if result['apply']:
                        nav_updates = open_workspace(navigation_payload(current), current, 'chat')
                        if nav_updates[-1].get('error'):
                            raise ValueError(nav_updates[-1]['error'])
                    return [result['text'] if result['apply'] else gr.skip(), result['pending'],
                            gr.update(visible=bool(result['pending'])), '', *nav_updates]
            except (ValueError, OSError) as error:
                message = str(error) if isinstance(error, ValueError) else '资料读取失败，请稍后重试。'
                return [gr.skip(), None, gr.update(visible=False), '<p role="alert">'+escape(message)+'</p>',
                        *[gr.skip() for _ in navigation_outputs[:-1]], navigation_payload(current)]

        for button, (_, suggested) in zip(shortcuts, SHORTCUTS):
            button.click(lambda text, pending, current, proposal=suggested:
                         fill_suggestion(text, pending, current, proposal),
                         [question, pending_suggestion, session], suggestion_outputs, queue=False,
                         api_name=False, show_progress='hidden')
        topic_ask.click(lambda text, pending, current: fill_suggestion(text, pending, current, '__topic__'),
                        [question, pending_suggestion, session], suggestion_outputs, queue=False,
                        api_name=False, show_progress='hidden')
        topic_action.submit(lambda action, text, pending, current:
                            fill_suggestion(text, pending, current, action if isinstance(action, dict) else {}),
                            [topic_action, question, pending_suggestion, session], suggestion_outputs,
                            queue=False, api_name=False, show_progress='hidden')
        for button, decision in ((suggestion_accept, 'confirm'), (suggestion_cancel, 'cancel')):
            button.click(lambda text, pending, current, action=decision:
                         fill_suggestion(text, pending, current, decision=action),
                         [question, pending_suggestion, session], suggestion_outputs, queue=False,
                         api_name=False, show_progress='hidden')

        def filter_teacher_college(college_id, current):
            cancel_mail(current.mail)
            return (gr.update(choices=teacher_choices(college_id=college_id),value=None),
                    gr.update(choices=[],value=None,interactive=False),'',gr.update(interactive=False),
                    gr.update(visible=False),'','教师筛选已更新，请重新选择公开邮箱。')

        def select_teacher(teacher_id, college_id, current):
            if teacher_id is None:
                return gr.update(choices=[], value=None, interactive=False), '', gr.update(interactive=False)
            row = teacher_contact(teacher_id)
            if college_id and not any(a['college_id']==college_id for a in row['affiliations']):
                raise ValueError('学院已变化，请重新选择教师。')
            emails = row['emails']
            text = '未收录明确的公开邮箱。' if not emails else '选择后点击填入；不会自动发送。'
            if len(emails) > 1:
                text = '该教师有多个公开邮箱，请选择一个，再点击填入。'
            source = (f'<p>{escape(text)} <a href="{escape(row["source_url"], quote=True)}" '
                      'target="_blank" rel="noopener noreferrer">查看官网资料</a></p>')
            return (gr.update(choices=emails, value=None,
                              interactive=bool(emails)), source, gr.update(interactive=bool(emails)))

        def fill_teacher(teacher_id, address, current):
            address = fill_teacher_recipient(current, teacher_id, address)
            return address, gr.update(visible=False), '', '收件邮箱已填入，请预览并核对后发送。'

        def fill_self_test(current):
            try:
                cancel_mail(current.mail)
                fields = self_test_fields(mail_settings)
                return [*fields, gr.update(visible=False), '测试邮件已填入，请预览确认。']
            except ValueError as error:
                return [gr.update(), gr.update(), gr.update(), gr.update(), escape(str(error))]

        mail_controls = [mail_to, mail_subject, mail_body, fill_test, mail_preview, mail_confirm, mail_cancel,
                         mail_upload,mail_file_select,mail_files_refresh]

        def submit_email(to, subject, body, current, version):
            try:
                with operation(current, version):
                    yield ['正在连接邮件服务器，请等待结果…', gr.update()] + [gr.update(interactive=False)] * len(mail_controls)
                    if document_service:
                        record = deliver_managed_mail(document_service.store,current.documents.mail_refs,current.mail,
                            mail_settings,to,subject,body,sender=mail_sender,logger=mail_logger)
                    else:
                        record = deliver_mail(current.mail, mail_settings, to, subject, body,
                                              sender=mail_sender, logger=mail_logger)
                    result = mail_result_html(record)
                    yield [result, gr.update(visible=False)] + [gr.update(interactive=True)] * len(mail_controls)
            except ValueError as error:
                if not current.mail.busy:
                    cancel_mail(current.mail)
                yield [escape(str(error))+' 请重新选择附件并预览；收件人和正文已保留。',
                       gr.update(visible=False)] + [gr.update(interactive=True)] * len(mail_controls)

        mail_inputs = [mail_to, mail_subject, mail_body, mail_session, revision]
        delivery_event(mail_teacher_college.select, guarded_event(filter_teacher_college), [mail_teacher_college,session,revision],
                       [mail_teacher,teacher_email,teacher_source,teacher_fill,mail_confirmation,mail_preview_text,mail_status],api_name=False,show_progress='hidden')
        delivery_event(mail_teacher.select, guarded_event(select_teacher), [mail_teacher, mail_teacher_college, session, revision],
                       [teacher_email, teacher_source, teacher_fill], api_name=False, show_progress='hidden')
        delivery_event(teacher_fill.click, guarded_event(fill_teacher), [mail_teacher, teacher_email, session, revision],
                       [mail_to, mail_confirmation, mail_preview_text, mail_status], api_name=False, show_progress='hidden')
        delivery_event(mail_preview.click, guarded_event(preview_email), mail_inputs, [mail_confirmation, mail_preview_text, mail_status],
                           api_name=False, show_progress='hidden')
        delivery_event(fill_test.click, guarded_event(fill_self_test), [mail_session, revision],
                        [mail_to, mail_subject, mail_body, mail_confirmation, mail_status],
                        api_name=False, show_progress='hidden')
        for field in (mail_to, mail_subject, mail_body):
            delivery_event(field.input, guarded_event(invalidate_email), [mail_session, revision], [mail_confirmation, mail_status],
                        api_name=False, show_progress='hidden')
        delivery_event(mail_cancel.click, guarded_event(invalidate_email), [mail_session, revision], [mail_confirmation, mail_status],
                          api_name=False, show_progress='hidden')
        delivery_event(mail_confirm.click, submit_email, mail_inputs, [mail_status, mail_confirmation, *mail_controls],
                           trigger_mode='once', concurrency_limit=1, api_name=False, show_progress='hidden')

        parts = {'chat': chat, 'status': status, 'send': send, 'question': question,
                 'prompt': prompt, 'stream': stream, 'history': history, 'temperature': temperature,
                 'clear': clear, 'confirmation': confirmation, 'edit_tools': edit_tools,
                 'web_feature':feature_controls['web'],'thinking_feature':feature_controls['thinking'],
                 'feature_confirmation':feature_controls['confirmation'],'feature_hint':feature_controls['hint'],
                 'model':model_controls['selector'],'model_refresh':model_controls['refresh'],'model_hint':model_hint}
        outputs = list(parts.values())

        def to_outputs(frame, reset_input=False):
            values = render_frame(frame,capabilities,model_catalog=model_catalog)
            values.update(model_updates(frame,directory))
            if not frame.get('refresh_model_choices') and any(m['id']==frame['settings']['model'] for m in directory.snapshot()['models']):
                values['model'].pop('choices',None)
            if reset_input:
                values['question']['value'] = ''
            return [values[key] for key in parts]

        def send_message(text, current, version):
            try:
                with operation(current, version):
                    for frame in iter_reply(current.chat, text, client_factory, model_background(current.profile),search_factory=search_factory,capabilities=capabilities,model_catalog=model_catalog):
                        yield to_outputs(frame, reset_input=frame['busy'] and frame['record'] is None)
            except ValueError as error:
                raise gr.Error(str(error)) from None

        def change_settings(version, use_stream, use_history, temp, current):
            proposed = dict(prompt_version=version, stream=use_stream, history_enabled=use_history, temperature=temp)
            with current.lock:
                try:
                    ensure_chat_mutation(current)
                    result = request_settings(current.chat, proposed)
                    message = '请确认参数修改；确认后会清空当前会话。' if result == 'confirm' else '设置已更新。'
                except ValueError as error:
                    message = str(error)
            details = f'新设置：Prompt {version}，温度 {temp}，历史{"开启" if use_history else "关闭"}。'
            return to_outputs(snapshot(current.chat, message)) + [details + ' 确认后清空当前会话的全部消息版本、模型历史及本机聊天档案内容，原实验日志保留。']

        def ask_clear(current):
            with current.lock:
                try:
                    ensure_chat_mutation(current)
                    done = clear_session(current.chat, False)
                    message = '会话已清空。' if done else '请确认是否清空当前会话。'
                except ValueError as error:
                    message = str(error)
            return to_outputs(snapshot(current.chat, message)) + ['确认清空当前会话的全部消息版本、模型历史及本机聊天档案内容？原实验日志保留。']

        def confirm_change(current, accepted):
            with current.lock:
                try:
                    ensure_chat_mutation(current)
                    resolve_action(current.chat, accepted)
                    message = '已确认，会话已清空。' if accepted else '已取消，原会话与设置保留。'
                except ValueError as error:
                    message = str(error)
            return to_outputs(snapshot(current.chat, message))

        send_dependency = delivery_event(send.click, send_message, [question, session, revision], outputs, concurrency_limit=None,
                   trigger_mode='once', show_progress='hidden', api_name=False)
        for control in (prompt, stream, history, temperature):
            delivery_event(control.input, guarded_event(change_settings), [prompt, stream, history, temperature, session, revision],
                          outputs + [confirm_text], concurrency_limit=None, api_name=False, show_progress='hidden')
        delivery_event(clear.click, guarded_event(ask_clear), [session, revision], outputs + [confirm_text], api_name=False, show_progress='hidden')
        delivery_event(chat.clear, guarded_event(ask_clear), [session, revision], outputs + [confirm_text], api_name=False, show_progress='hidden')
        delivery_event(confirm.click, guarded_event(lambda current: confirm_change(current, True)), [session, revision], outputs, api_name=False, show_progress='hidden')
        delivery_event(cancel.click, guarded_event(lambda current: confirm_change(current, False)), [session, revision], outputs, api_name=False, show_progress='hidden')
        archive_refresh = bind_conversation_events(archive, conversation_store, app, session, revision, question, outputs, to_outputs,model_catalog=model_catalog)
        bind_chat_edit_ui(edit_tools, session, revision, outputs, to_outputs, client_factory, archive_refresh, archive['outputs'],search_factory=search_factory,capabilities=capabilities,model_catalog=model_catalog)
        bind_feature_controls(feature_controls,session,revision,outputs,to_outputs,capabilities,model_catalog=model_catalog)
        bind_model_controls(model_controls,session,revision,outputs,to_outputs,directory)
        def history_ready():
            return [gr.update(interactive=True) for _ in range(3)]
        if archive_refresh:
            loaded = delivery_event(initialized.then, archive_refresh, [session, revision], archive['outputs'],
                                    api_name=False, show_progress='hidden', queue=False)
            loaded.then(history_ready, [], [profile['save'], profile['skip'], profile['clear']],
                        queue=False, api_name=False, show_progress='hidden')
            delivery_event(send_dependency.then, archive_refresh, [session, revision], archive['outputs'],
                           api_name=False, show_progress='hidden')
        else:
            initialized.then(history_ready, [], [profile['save'], profile['skip'], profile['clear']],
                             queue=False, api_name=False, show_progress='hidden')
        # 资料更新统一输出到各工作区，页面草稿与服务端预览同时清理。
        mail_reset_outputs = [mail_to, mail_subject, mail_body, mail_confirmation, mail_preview_text, mail_status,
                              mail_teacher_college,mail_teacher,teacher_email,teacher_source,teacher_fill]
        document_reset_outputs = [document_parts['bridge'],document_parts['uploads'],document_parts['photo'],document_parts['photo_box'],document_parts['upload_box'],mail_attachment_list,mail_file_select,mail_upload]
        reset_outputs = outputs + mail_reset_outputs + reminder_binding.outputs + exam_binding.outputs + archive['outputs'] + [topic_content] + document_reset_outputs + timetable_parts['outputs'] + [task_source,reminder_binding.panel]
        profile_outputs = [profile['dialog'], profile['confirmation'], profile['info'], profile_status, revision,
                           *profile['fields'], profile['consent'], profile['save'], profile['skip'], *reset_outputs]

        def profile_result(current, visible=False, confirm_pending=False, message='', reset=False):
            displayed = current.pending_profile if confirm_pending and current.pending_profile is not None else current.profile
            values = [gr.update(value=value, interactive=not confirm_pending) for value in asdict(displayed).values()]
            updates = [gr.update(visible=visible), gr.update(visible=confirm_pending), escape(message),
                       profile_summary(current.profile), current.revision, *values,
                       gr.update(value='保存资料' if current.entered else '填写资料并进入', interactive=not confirm_pending),
                       gr.update(value='取消，保留原资料' if current.entered else '暂不填写')]
            if reset:
                updates += (to_outputs(snapshot(current.chat, '个人资料已更新，旧对话已清空。'), reset_input=True)
                            + ['', '', '', gr.update(visible=False), '', '草稿已清空，尚未发送邮件。']
                            + [gr.update(value=''),gr.update(choices=teacher_choices(),value=None),
                               gr.update(choices=[],value=None,interactive=False),'',gr.update(interactive=False)]
                            + reminder_binding.reset_values(current.profile.reminder_email)
                            + exam_binding.reset_values(current.profile.reminder_email)
                            + archive_values(conversation_store, current)
                            + [materials_breadcrumb(current.navigation.workspace)+render_topic(topic_page(current.navigation.workspace[6:], profile=current.profile))
                               if current.navigation.workspace.startswith('topic:') else gr.skip()]
                            + [documents_ready(current),gr.update(value=None),gr.update(value=None),gr.update(visible=False),
                               gr.update(visible=current.navigation.workspace=='files'),'<p class="file-note">尚未添加附件。</p>',
                               gr.update(choices=[],value=[]),gr.update(value=None)]
                            + [timetable_controller.payload(current) if timetable_controller else gr.skip(),gr.update(value=None)]
                            + [task_source_payload(timetable_controller.service.reminder_bridge if timetable_controller else None,current),
                               gr.update(visible=True)])
            else:
                updates += [gr.update() for _ in reset_outputs]
            return updates

        def check_profile_version(current, version):
            if current.revision != version:
                raise gr.Error('资料已改变，请重新打开个人资料。')

        def open_profile(current, version):
            with current.lock:
                check_profile_version(current, version)
                return profile_result(current, visible=True, confirm_pending=current.pending_profile is not None)

        def save_profile(*args):
            current, version = args[-2:]
            values = dict(zip(asdict(Profile()), args[:-2]))
            with current.lock:
                check_profile_version(current, version)
                try:
                    proposed = validate_profile(values)
                    result = request_profile(current, proposed, current.entered)
                except ValueError as error:
                    # 保留用户正在编辑的内容，不误显示保存成功。
                    current.pending_profile = None
                    updates = [gr.update() for _ in profile_outputs]
                    updates[1], updates[2] = gr.update(visible=False), escape(str(error))
                    for index in range(5, 5 + len(profile['fields']) + 2):
                        updates[index] = gr.update(interactive=True)
                    return updates
                if result != 'confirm':
                    current.entered = True
                return profile_result(current, visible=result=='confirm', confirm_pending=result=='confirm',
                                      message='请确认清理旧内容。' if result=='confirm' else '资料已保存。',
                                      reset=result=='applied')

        def clear_profile(current, version):
            with current.lock:
                check_profile_version(current, version)
                try:
                    result = request_profile(current, Profile(), current.entered)
                except ValueError as error:
                    raise gr.Error(str(error)) from None
                if result != 'confirm':
                    current.entered = True
                return profile_result(current, visible=result=='confirm', confirm_pending=result=='confirm',
                                      message='请确认清除。' if result=='confirm' else '现在以访客使用。',
                                      reset=result=='applied')

        def finish_profile(current, version, accepted=False):
            with current.lock:
                check_profile_version(current, version)
                try:
                    applied = resolve_profile(current, accepted)
                except ValueError as error:
                    raise gr.Error(str(error)) from None
                current.entered = True
                return profile_result(current, message='资料已更新。' if applied else '继续使用当前资料。', reset=applied)

        def accept_profile(*args):
            current, version = args[-2:]
            with current.lock:
                check_profile_version(current, version)
                try:
                    displayed = validate_profile(dict(zip(asdict(Profile()), args[:-2])))
                    if displayed != current.pending_profile:
                        raise ValueError('资料已修改，请重新保存并确认。')
                except ValueError as error:
                    current.pending_profile = None
                    return profile_result(current, visible=True, message=str(error))
                return finish_profile(current, version, True)

        profile_opts = dict(api_name=False, show_progress='hidden', queue=False, profile_event=True)
        delivery_event(profile_open.click, open_profile, [session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['save'].click, save_profile, [*profile['fields'], profile['consent'], session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['clear'].click, clear_profile, [session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['skip'].click, finish_profile, [session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['accept'].click, accept_profile, [*profile['fields'], profile['consent'], session, revision], profile_outputs, **profile_opts)
        delivery_event(profile['reject'].click, finish_profile, [session, revision], profile_outputs, **profile_opts)
    return app


def launch_options():
    return dict(server_name='127.0.0.1', server_port=7861, share=False,
                show_error=False, allowed_paths=[], blocked_paths=[str(PROJECT_DIR)],
                strict_cors=True, enable_monitoring=False, mcp_server=False,
                footer_links=[], css_paths=[str(RESOURCE_DIR/'web.css'), str(RESOURCE_DIR/'campus.css'), str(RESOURCE_DIR/'documents.css'), str(RESOURCE_DIR/'timetable.css'),str(RESOURCE_DIR/'teachers.css')],
                css=('#nav-chat::before { background-image: url("'+image_data('xiaozhi-navigation.png')+'"); }'
                     '#chat { --chat-watermark-image: url("'+image_data('uibe-logo.png')+'"); }'),
                js=(RESOURCE_DIR/'profile.js').read_text(encoding='utf-8'),
                theme=gr.themes.Base(font=['Microsoft YaHei UI', 'Arial', 'sans-serif'],
                                    font_mono=['Consolas', 'monospace']))


def ensure_local_bypass():
    # 仅本进程的回环地址直连，避免系统代理拦截Gradio启动自检。
    for name in ('NO_PROXY', 'no_proxy'):
        rules = [value.strip() for value in os.environ.get(name, '').split(',') if value.strip()]
        os.environ[name] = ','.join(dict.fromkeys(rules + ['127.0.0.1', 'localhost', '::1']))


def ensure_server_port_available(server_port: int) -> None:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', server_port))


def make_reminder_service(settings):
    if settings is None:
        return None
    try:
        store = ReminderStore(RESOURCE_DIR / 'reminders' / 'reminders.sqlite3')
        return ReminderService(store, settings)
    except (OSError, sqlite3.Error):
        return None


def make_conversation_store():
    try:
        return ConversationStore(PROJECT_DIR / 'data' / 'conversations.sqlite3'), ''
    except (OSError, sqlite3.Error):
        return None, '历史会话不可用：本次问答不会持久保存。请检查 data/conversations.sqlite3 与目录权限；原文件未删除。'


def make_timetable_runtime(vision_factory,base_url,reminder_service,origin,*,project_dir=None):
    from timetable_runtime import TimetableRuntime
    from timetable_vision import VisionSettings
    try:
        parsed=urlparse(base_url)
        verified=(parsed.scheme=='https' and parsed.hostname=='api.deepseek.com') or os.getenv('TIMETABLE_VISION_ENDPOINT_CONFIRMED','').lower()=='true'
        model=os.getenv('TIMETABLE_VISION_MODEL','deepseek-flash').strip()
        if not model or len(model)>100:raise ValueError('识别模型配置无效。')
        return TimetableRuntime(project_dir or PROJECT_DIR,origin=origin,now=lambda:datetime.now(assistant.BEIJING),
            vision_factory=vision_factory,settings=VisionSettings(model=model,verified_endpoint=verified),reminder_service=reminder_service)
    except (ValueError,OSError,sqlite3.Error):
        return None


def main(server_port=7861):
    ensure_local_bypass()
    reminder_service, exam_service, app, document_runtime, timetable_runtime = None, None, None, None, None
    try:
        ensure_server_port_available(server_port)
        api_key, base_url, model = assistant.read_settings()
        mail_settings = read_mail_settings()
        if mail_settings and not mail_settings.password:
            import getpass
            import sys
            from dataclasses import replace
            if sys.stdin.isatty():
                password = getpass.getpass('邮箱密码（隐藏输入；直接回车则暂不启用发送）：')
                mail_settings = replace(mail_settings, password=password)
        extra_body = {'thinking': {'type': 'disabled'}} if urlparse(base_url).hostname == 'api.deepseek.com' else {}
        # 密钥只存在于服务端工厂闭包，不进入State、控件或浏览器URL。
        def client_factory():
            return OpenAI(api_key=api_key, base_url=base_url, timeout=45.0, max_retries=0)
        from assistant_features import feature_capabilities
        from web_search import SearchClient
        capabilities=feature_capabilities(base_url,model)
        from model_catalog import ModelCatalog
        model_directory=ModelCatalog(client_factory,base_url,model)
        model_directory.refresh()
        def search_factory():
            if not capabilities['web_available']:raise ValueError(capabilities['web_reason'])
            return SearchClient(api_key)
        reminder_service = make_reminder_service(mail_settings)
        exam_store = ExamStore(RESOURCE_DIR / 'exams' / 'exams.sqlite3')
        if mail_settings:
            exam_service = ReminderService(exam_store, mail_settings, draft_factory=exam_store.make_mail)
        conversation_store, conversation_error = make_conversation_store()
        from document_runtime import DocumentRuntime, create_local_web_app, run_local_web_app
        try:
            document_runtime=DocumentRuntime(PROJECT_DIR,f'http://127.0.0.1:{server_port}')
        except (ValueError,OSError,sqlite3.Error):
            print('文件管理暂不可用，问答等功能仍可使用；请检查文档目录权限及模板资源。')
        timetable_runtime=make_timetable_runtime(client_factory,base_url,reminder_service,f'http://127.0.0.1:{server_port}')
        app = build_app(model, extra_body, client_factory, mail_settings, reminder_service=reminder_service,
                        exam_store=exam_store, exam_service=exam_service,
                        conversation_store=conversation_store, conversation_error=conversation_error,
                        document_service=document_runtime.service if document_runtime else None,
                        document_secret=document_runtime.secret if document_runtime else None,
                        timetable_controller=timetable_runtime.controller if timetable_runtime else None,
                        search_factory=search_factory,feature_capabilities=capabilities,model_catalog=model_directory)
        root_app=create_local_web_app(app,document_runtime,server_port=server_port,
            extra_routers=(timetable_runtime.router,) if timetable_runtime else ())
        def start_workers():
            if reminder_service is not None:reminder_service.start()
            if exam_service is not None:exam_service.start()
        run_local_web_app(root_app,server_port=server_port,on_started=start_workers)
    except KeyboardInterrupt:
        # Ctrl+C是主动停止；清理仍交给finally，其他异常不在这里吞掉。
        pass
    except (ValueError, OSError, sqlite3.Error):
        print(f'网页未能启动。请检查项目.env、依赖、资料文件和{server_port}端口；不要公开密钥。')
        return 1
    finally:
        if reminder_service is not None:
            reminder_service.stop()
        if exam_service is not None:
            exam_service.stop()
        if app is not None:
            app.close()
        if timetable_runtime is not None:
            timetable_runtime.close()
        if document_runtime is not None:
            document_runtime.close()
    print('服务已停止')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
