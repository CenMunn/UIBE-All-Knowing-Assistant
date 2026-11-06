"""会话档案与网页状态之间的适配。"""
from copy import deepcopy
import sqlite3

import assistant
import conversation_tree as ct
from conversation_store import RECORD_FIELDS
from conversation_ownership import owner_for_profile, validate_owner_key


def bind_history(chat, store, *, owner_key):
    if owner_key is not None:
        validate_owner_key(owner_key)
    with chat.lock:
        if chat.archive_owner != owner_key and (chat.messages or chat.archive_id is not None):
            raise ValueError('请先保存并清空旧对话，再切换历史归属。')
        chat.archive_store = store
        chat.archive_owner = owner_key


def history_owner(app):
    owner = owner_for_profile(app.profile)
    if app.chat.archive_owner != owner:
        raise ValueError('历史会话与当前资料不一致，请重新填写资料。')
    return owner


def detach_history(chat):
    chat.archive_id = None
    chat.archive_revision = None
    chat.archive_title = '新对话'
    chat.archive_error = ''
    chat.archive_dirty = False


def persist_chat(chat, strict=False):
    with chat.lock:
        if chat.archive_owner is None or chat.archive_store is None:
            if chat.archive_id is not None:
                raise ValueError('历史会话缺少有效归属，不能保存。')
            chat.archive_dirty = False
            chat.archive_error = ''
            return
        if not chat.archive_dirty:
            return
        if not chat.messages and chat.archive_id is None:
            chat.archive_dirty = False
            return
        payload = {'messages': deepcopy(chat.messages), 'history': deepcopy(chat.state['history']),
                   'settings': {k: chat.state[k] for k in ('model', 'prompt_version', 'temperature', 'stream', 'history_enabled', 'web_enabled', 'deep_thinking')},
                   'record': {k: v for k, v in chat.last_record.items() if k in RECORD_FIELDS} if chat.last_record else None}
        from web_session import ensure_tree
        ensure_tree(chat)
        payload.update(format_version=2, tree=deepcopy(chat.tree))
        nodes = ct.active_nodes(chat.tree)
        payload['record'] = deepcopy(nodes[-1]['record']) if nodes else None
        try:
            if chat.archive_id is None:
                title = (chat.messages[0]['content'].strip().replace('\n', ' ')[:30] or '新对话')
                row = chat.archive_store.create(payload, title, owner_key=chat.archive_owner)
            else:
                row = chat.archive_store.save(chat.archive_id, chat.archive_revision, payload, owner_key=chat.archive_owner)
            chat.archive_id, chat.archive_revision, chat.archive_title = row['id'], row['revision'], row['title']
            chat.archive_dirty = False
            chat.archive_error = ''
        except (ValueError, OSError, sqlite3.Error) as error:
            detail = str(error) if isinstance(error, ValueError) else '请检查本机存储是否可用。'
            chat.archive_error = '历史保存失败：' + detail + ' 当前页面回答仍保留；可先复制，再在管理会话中确认放弃未保存更改。'
            if strict:
                raise ValueError(chat.archive_error + ' 请先复制需要保留的内容，勿直接刷新。') from None


def _idle(app):
    from app_session import _ensure_idle
    _ensure_idle(app)
    history_owner(app)
    if app.pending_profile is not None or app.chat.pending_action is not None or app.pending_conversation is not None or app.pending_edit:
        raise ValueError('请先确认或取消当前修改。')


def _fresh(app, store):
    from web_session import new_session
    chat = new_session(app.chat.state['model'], app.chat.state['extra_body'])
    chat.configured_model=app.chat.configured_model or app.chat.state['model']
    bind_history(chat, store, owner_key=owner_for_profile(app.profile))
    return chat


def new_conversation(app, store):
    with app.lock:
        _idle(app)
        persist_chat(app.chat, strict=True)
        app.chat = _fresh(app, store)
        app.revision += 1


def discard_unsaved_conversation(app, store):
    with app.lock:
        _idle(app)
        if not app.chat.archive_error or not app.chat.archive_dirty:
            raise ValueError('当前没有保存失败的更改。')
        app.chat = _fresh(app, store)
        app.revision += 1


def open_conversation(app, store, identifier, *, model_catalog=None):
    with app.lock:
        _idle(app)
        persist_chat(app.chat, strict=True)
        row = store.get(identifier, owner_key=history_owner(app))
        chat = _fresh(app, store)
        payload = row['payload']
        chat.state.update(deepcopy(payload['settings']))
        if 'model' not in payload['settings']:chat.state['model']=chat.configured_model
        from assistant_features import feature_settings
        chat.state.update(feature_settings(payload['settings']))
        chat.state['system_prompt'] = assistant.load_system_prompt(chat.state['prompt_version'])
        chat.state['history'] = deepcopy(payload['history'])
        chat.messages = deepcopy(payload['messages'])
        chat.tree = deepcopy(payload['tree']) if payload.get('format_version') == 2 else ct.from_legacy(
            payload['messages'], payload['history'], payload['record'])
        chat.last_record = deepcopy(payload['record'])
        if chat.state['web_enabled']:
            chat.pending_action='features'
            chat.pending_features=feature_settings(chat.state)
        chat.archive_id, chat.archive_revision, chat.archive_title = row['id'], row['revision'], row['title']
        from web_session import reconcile_model_features
        reconcile_model_features(chat,model_catalog)
        app.chat = chat
        app.revision += 1


def rename_conversation(app, store, title):
    with app.lock:
        _idle(app)
        persist_chat(app.chat, strict=True)
        if app.chat.archive_id is None: raise ValueError('当前对话尚未保存，请先完成一次问答。')
        row = store.rename(app.chat.archive_id, app.chat.archive_revision, title, owner_key=history_owner(app))
        app.chat.archive_revision, app.chat.archive_title = row['revision'], row['title']


def delete_conversation(app, store, identifier, revision):
    with app.lock:
        _idle(app)
        store.delete(identifier, revision, owner_key=history_owner(app))
        if app.chat.archive_id == identifier:
            app.chat = _fresh(app, store)
            app.revision += 1
