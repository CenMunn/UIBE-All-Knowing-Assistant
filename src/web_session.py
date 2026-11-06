"""浏览会话状态与线程队列，不依赖网页组件。"""

from copy import deepcopy
from dataclasses import dataclass, field
import math
from queue import SimpleQueue
from threading import RLock, Thread
import uuid

import assistant
import conversation_tree as ct
from assistant_features import feature_settings


@dataclass
class WebSession:
    state: dict
    configured_model: str = ''
    capability_notice: str = ''
    messages: list = field(default_factory=list)
    busy: bool = False
    pending_settings: dict | None = None
    pending_action: str | None = None
    web_consent: bool = False
    pending_features: dict | None = None
    phase: str = ''
    last_record: dict | None = None
    tree: dict = field(default_factory=ct.empty_tree)
    view_token: str = field(default_factory=lambda: uuid.uuid4().hex)
    edit_node: str | None = None
    edit_draft: str = ''
    archive_store: object = field(default=None, repr=False)
    archive_owner: str | None = field(default=None, repr=False)
    archive_id: str | None = None
    archive_revision: int | None = None
    archive_title: str = '新对话'
    archive_error: str = ''
    archive_dirty: bool = False
    lock: RLock = field(default_factory=RLock, repr=False)

    def __deepcopy__(self, memo):
        with self.lock:
            other = WebSession(deepcopy(self.state, memo),configured_model=self.configured_model,
                               messages=deepcopy(self.messages, memo))
            other.state['session_id'] = uuid.uuid4().hex
            other.last_record = deepcopy(self.last_record, memo)
            other.tree = deepcopy(self.tree, memo)
            return other


def new_session(model, extra_body):
    return WebSession({
        'session_id': uuid.uuid4().hex, 'model': model, 'entry': 'web',
        'prompt_version': 'campus_v7', 'system_prompt': assistant.load_system_prompt('campus_v7'),
        'temperature': .3, 'stream': True, 'history_enabled': True, 'history': [],
        'max_chars': 24000, 'max_tokens': 800, 'extra_body': deepcopy(extra_body),
        **feature_settings({}),
    },configured_model=model)


def settings(session):
    return {key: session.state[key] for key in ('model', 'prompt_version', 'stream', 'history_enabled', 'temperature')} | feature_settings(session.state)


def request_model(session, model_id, catalog):
    with session.lock:
        _ensure_idle(session)
        if session.pending_action or session.archive_error:
            raise ValueError('请先处理当前待确认操作或历史保存错误。')
        catalog.require_available(model_id)
        session.state['model']=model_id
        session.capability_notice=''
        notice=reconcile_model_features(session,catalog,persist=False)
        _save_archive(session)
        return '模型已切换，下一条消息生效；原对话保留。'+notice


def reconcile_model_features(session,catalog,*,persist=True):
    with session.lock:
        if catalog is None or session.busy:return ''
        caps=catalog.capabilities(session.state['model']);disabled=[]
        for key,cap,label in (('deep_thinking','thinking_available','深度思考'),('web_enabled','web_available','联网补充')):
            if session.state.get(key) and not caps[cap]:
                session.state[key]=False;disabled.append(label)
            if session.pending_features is not None and not caps[cap]:session.pending_features[key]=False
        if session.pending_action=='features' and session.pending_features is not None and not session.pending_features.get('web_enabled'):
            session.pending_action=None;session.pending_features=None
        if not disabled:return ''
        session.capability_notice='当前模型不支持，已关闭：'+'、'.join(disabled)+'。'
        if persist:_save_archive(session)
        return session.capability_notice


def request_features(session, proposed):
    with session.lock:
        _ensure_idle(session)
        if session.pending_action:
            raise ValueError('请先确认或取消当前修改。')
        if set(proposed) - {'web_enabled', 'deep_thinking'}:
            raise ValueError('不支持的功能设置。')
        updated = feature_settings(feature_settings(session.state) | proposed)
        if updated['web_enabled'] and not session.web_consent:
            session.pending_features = updated
            session.pending_action = 'features'
            return 'confirm'
        session.state.update(updated)
        _save_archive(session)
        return 'applied'


def resolve_features(session, accepted):
    with session.lock:
        _ensure_idle(session)
        if session.pending_action != 'features':
            raise ValueError('当前没有待确认的联网授权。')
        if accepted and session.pending_features:
            session.web_consent = True
            session.state.update(session.pending_features)
            _save_archive(session)
        elif session.state.get('web_enabled') and not session.web_consent:
            # 恢复的开关偏好不是许可；拒绝后可直接继续本地聊天。
            session.state['web_enabled']=False
            _save_archive(session)
        session.pending_features = None
        session.pending_action = None


def _ensure_idle(session):
    if session.busy:
        raise ValueError('当前请求尚未结束，请稍后操作。')
    if session.edit_node:
        raise ValueError('请先发送或取消正在编辑的消息。')


def ensure_tree(session):
    if not session.tree['nodes'] and session.messages:
        session.tree = ct.from_legacy(session.messages, session.state['history'], session.last_record)


def reset_tree(session):
    session.tree = ct.empty_tree()
    session.edit_node = None
    session.edit_draft = ''
    session.web_consent = False
    session.pending_features = None
    session.phase = ''
    session.capability_notice=''
    session.view_token = uuid.uuid4().hex


def tree_view(session):
    if len(session.messages) % 2 == 0:
        ensure_tree(session)
    rows = []
    for node in ct.active_nodes(session.tree):
        versions = ct.sibling_versions(session.tree, node['id'])
        notice = {'error': '此轮请求未完成，未计入上下文。', 'incomplete': '此轮回答不完整，未计入上下文。'}.get(node['status'], '')
        detail = (node['record'] or {}).get('error_message') or ''
        if node['status'] == 'incomplete' and detail.startswith('来源核验未通过'):
            notice = '此轮来源核验未通过，未计入上下文。'
            detail = detail.removeprefix('来源核验未通过：').removesuffix('。原回答保留供核对，本轮不计入历史。')
        if notice and detail:
            notice += ' ' + detail
        from evidence_view import render_evidence
        from citation_links import citation_targets
        rows.append({'id': node['id'], 'versions': versions, 'index': versions.index(node['id']), 'notice': notice,
                     'citations': citation_targets((node['record'] or {}).get('evidence', [])),
                     'evidence_html': render_evidence(node['assistant'], (node['record'] or {}).get('evidence', []))})
    return {'nodes': rows, 'token': session.view_token, 'busy': session.busy,
            'editing': session.edit_node, 'blocked': bool(session.pending_action or session.archive_error),
            'draft': session.edit_draft if session.edit_node else '', 'action': None}


def _apply(session, proposed, reset):
    rules = assistant.load_system_prompt(proposed['prompt_version'])
    session.state.update(proposed)
    session.state['system_prompt'] = rules
    if reset:
        session.state['history'] = []
        session.messages = []
        session.last_record = None
        reset_tree(session)
    session.pending_settings = None
    session.pending_action = None
    _save_archive(session)


def _save_archive(session):
    if session.archive_store is not None:
        from conversation_service import persist_chat
        session.archive_dirty = True
        persist_chat(session)


def request_settings(session, proposed):
    with session.lock:
        _ensure_idle(session)
        current = settings(session)
        if set(proposed) - {'prompt_version', 'stream', 'history_enabled', 'temperature'}:
            raise ValueError('不支持的设置。')
        updated = current | proposed
        if updated['prompt_version'] not in assistant.PROMPT_VERSIONS:
            raise ValueError('请选择已提供的Prompt版本。')
        value = float(updated['temperature'])
        if not math.isfinite(value) or not 0 <= value <= 2:
            raise ValueError('温度须在0到2之间。')
        updated['temperature'] = value
        if any(type(updated[key]) is not bool for key in ('stream', 'history_enabled')):
            raise ValueError('开关值无效。')
        reset = any(updated[key] != current[key] for key in ('prompt_version', 'temperature', 'history_enabled'))
        if reset and session.messages:
            session.pending_settings = updated
            session.pending_action = 'settings'
            return 'confirm'
        _apply(session, updated, reset)
        return 'applied'


def resolve_settings(session, accepted):
    with session.lock:
        _ensure_idle(session)
        if accepted and session.pending_settings:
            _apply(session, session.pending_settings, True)
        else:
            session.pending_settings = None
            session.pending_action = None


def clear_session(session, confirmed):
    with session.lock:
        _ensure_idle(session)
        if session.messages and not confirmed:
            session.pending_action = 'clear'
            return False
        session.state['history'] = []
        session.messages = []
        session.last_record = None
        reset_tree(session)
        session.pending_action = None
        session.pending_settings = None
        _save_archive(session)
        return True


def snapshot(session, status=None):
    with session.lock:
        record = deepcopy(session.last_record)
        return {'messages': deepcopy(session.messages), 'busy': session.busy,
                'status': status or ({'search':'正在查找资料…','thinking':'正在思考…','answer':'正在回答…'}.get(session.phase,'正在等待响应…') if session.busy else '尚未发送请求'),
                'record': record, 'history_rounds': len(session.state['history']) // 2,
                'pending': session.pending_action, 'settings': settings(session),
                'web_consent':session.web_consent,
                'capability_notice':session.capability_notice,
                'archive_error': session.archive_error, 'archive_title': session.archive_title,
                'archive_id': session.archive_id, 'edit_view': tree_view(session)}


def iter_reply(session, user_text, client_factory, learning_background=None, *, edit_parent=None, editing=False, search_factory=None, capabilities=None, model_catalog=None):
    text = user_text.strip()
    with session.lock:
        model_error=None
        if model_catalog is not None:
            try:model_catalog.require_available(session.state['model'])
            except ValueError as error:model_error=str(error)
            if not session.busy and not model_error:
                session.capability_notice=''
                reconcile_model_features(session,model_catalog)
        if session.busy:
            rejected = snapshot(session, '当前请求尚未结束，请勿重复发送。')
        elif session.pending_action or (session.edit_node and not editing):
            rejected = snapshot(session, '请先确认或取消会话重置。')
        elif not text:
            rejected = snapshot(session, '请输入问题。')
        elif model_error:
            rejected = snapshot(session,model_error)
        elif session.state.get('web_enabled') and not session.web_consent:
            session.pending_action='features'
            session.pending_features=feature_settings(session.state)
            rejected=snapshot(session,'请先确认联网补充授权；输入问题仍保留。')
        else:
            ensure_tree(session)
            previous_tree = deepcopy(session.tree)
            previous_messages = deepcopy(session.messages)
            previous_record = session.last_record
            previous_history = deepcopy(session.state['history'])
            node_id = ct.append_turn(session.tree, text, edit_parent)
            rejected = None
            session.busy = True
            frozen = deepcopy(session.state)
            frozen_capabilities=model_catalog.capabilities(frozen['model']) if model_catalog is not None else None
            frozen_consent=session.web_consent
            session.phase=''
            frozen['learning_background'] = deepcopy(learning_background or {})
            if editing:
                frozen['history'] = ct.project_history(session.tree, frozen['history_enabled'])
            session.messages = ct.project_messages(session.tree)
            session.view_token = uuid.uuid4().hex
            session.edit_node = None
            session.last_record = None
    if rejected:
        yield rejected
        return

    events = SimpleQueue()
    started = False

    def display(part='', **kwargs):
        if kwargs.get('end') == '' and part:
            with session.lock:
                session.tree['nodes'][node_id]['assistant'] += part
                session.messages = ct.project_messages(session.tree)
                events.put(snapshot(session))

    def worker():
        result = None
        try:
            with client_factory() as client:
                from contextlib import nullcontext
                def phase(value):
                    if value not in ('search','thinking','answer'):return
                    with session.lock:
                        if session.phase!=value:
                            session.phase=value
                            events.put(snapshot(session))
                with (search_factory() if search_factory and frozen.get('web_enabled') else nullcontext(None)) as search:
                    ask_options=dict(search_client=search,web_consent=frozen_consent,on_phase=phase)
                    if frozen_capabilities is not None:ask_options['model_capabilities']=frozen_capabilities
                    result = assistant.ask(client, frozen, text, display=display, **ask_options)
        except Exception as error:
            if result is not None:
                # ask已返回并落盘；关闭客户端失败不能推翻已完成的问答结果。
                result['cleanup_warning'] = '客户端清理未完成，本轮问答结果已保留；可重启本地程序释放资源。'
            else:
                result = {'status': 'error', 'error_message': '网页调用未完成，请检查本地配置或依赖。',
                          'error_type': type(error).__name__, 'output': '', 'log_saved': False,
                          'first_text_seconds': None, 'total_seconds': None}
        finally:
            with session.lock:
                if result is not None:result['model']=frozen['model']
                session.state['history'] = frozen['history']
                session.last_record = result
                node = session.tree['nodes'][node_id]
                content = node['assistant'] or (result or {}).get('error_message', '未收到正文。')
                ct.finish_turn(session.tree, node_id, content, result)
                session.messages = ct.project_messages(session.tree)
                _save_archive(session)
                session.busy = False
                session.phase=''
                events.put(snapshot(session, (result or {}).get('status', 'error')))
            events.put(None)

    try:
        yield snapshot(session, '正在等待模型响应…')
        thread = Thread(target=worker, daemon=True)
        thread.start()
        started = True
        while True:
            frame = events.get()
            if frame is None:
                break
            yield frame
    finally:
        # 已启动的请求由worker负责释放；关闭页面不等于平台取消处理。
        if not started:
            with session.lock:
                session.tree = previous_tree
                session.messages = previous_messages
                session.last_record = previous_record
                session.state['history'] = previous_history
                session.view_token = uuid.uuid4().hex
                session.busy = False
