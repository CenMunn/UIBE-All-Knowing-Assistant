"""本机会话档案；不保存凭据、完整Prompt或个人资料对象。"""
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import uuid
from evidence_view import validate_evidence
from assistant import PROMPT_VERSIONS
from conversation_tree import validate_tree, project_messages, project_history, active_nodes
from conversation_ownership import validate_owner_key

MAX_BYTES = 2 * 1024 * 1024
RECORD_FIELDS = {'status', 'log_saved', 'first_text_seconds', 'total_seconds', 'dropped_rounds',
                 'finish_reason', 'error_message', 'cleanup_warning', 'evidence', 'feature_run', 'usage', 'first_response_seconds', 'model'}


def validate_payload(data):
    old_keys = {'messages', 'history', 'settings', 'record'}
    if not isinstance(data, dict) or set(data) not in (old_keys, old_keys | {'format_version', 'tree'}):
        raise ValueError('会话记录结构无效。')
    versioned = 'format_version' in data
    if versioned and (type(data['format_version']) is not int or data['format_version'] != 2):
        raise ValueError('会话格式版本无效。')
    for key in ('messages', 'history'):
        messages = data[key]
        if not isinstance(messages, list) or len(messages) > 1000 or len(messages) % 2:
            raise ValueError('会话消息结构无效或超过1000条限制。')
        for index, message in enumerate(messages):
            if (not isinstance(message, dict) or set(message) != {'role', 'content'} or
                message['role'] != ('user' if index % 2 == 0 else 'assistant') or
                not isinstance(message['content'], str)):
                raise ValueError('会话消息格式无效。')
    settings = data['settings']
    old_settings = {'prompt_version', 'temperature', 'stream', 'history_enabled'}
    if (not isinstance(settings, dict) or set(settings) not in (old_settings, old_settings | {'web_enabled', 'deep_thinking'}, old_settings | {'web_enabled', 'deep_thinking', 'model'}) or
        settings['prompt_version'] not in PROMPT_VERSIONS or
        any(type(settings[key]) is not bool for key in ('stream', 'history_enabled'))):
        raise ValueError('会话设置无效。')
    from assistant_features import feature_settings
    feature_settings(settings)
    if 'model' in settings:
        from model_catalog import valid_model_id
        valid_model_id(settings['model'])
    temp = settings['temperature']
    if type(temp) not in (int, float) or not math.isfinite(temp) or not 0 <= temp <= 2:
        raise ValueError('会话温度无效。')
    if not settings['history_enabled'] and data['history']:
        raise ValueError('关闭上下文的会话不应保存模型历史。')
    if versioned:
        tree = data['tree']
        validate_tree(tree)
        if data['messages'] != project_messages(tree):
            raise ValueError('会话消息与选中分支不一致。')
        expected = project_history(tree, settings['history_enabled'])
        cursor = 0
        for index in range(0, len(data['history']), 2):
            pair = data['history'][index:index + 2]
            while cursor < len(expected) and expected[cursor:cursor + 2] != pair:
                cursor += 2
            if cursor >= len(expected):
                raise ValueError('模型历史不属于当前成功分支。')
            cursor += 2
        nodes = active_nodes(tree)
        if data['record'] != (nodes[-1]['record'] if nodes else None):
            raise ValueError('运行记录与选中分支不一致。')
    record = data['record']
    if record is not None:
        if not isinstance(record, dict) or set(record) - RECORD_FIELDS:
            raise ValueError('会话运行记录无效。')
        for key, value in record.items():
            if key == 'model':
                from model_catalog import valid_model_id
                valid_model_id(value)
            elif key == 'feature_run':
                from assistant_features import validate_feature_run
                validate_feature_run(value)
            elif key == 'usage':
                from assistant_features import archive_usage
                if archive_usage(value)!=value:raise ValueError('用量记录包含不支持的字段。')
            elif key == 'evidence':
                validate_evidence(value)
            elif key in ('first_text_seconds', 'total_seconds', 'dropped_rounds', 'first_response_seconds'):
                if value is not None and (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
                    raise ValueError('会话耗时无效。')
            elif key == 'log_saved':
                if type(value) is not bool: raise ValueError('日志状态无效。')
            elif value is not None and (not isinstance(value, str) or len(value) > 4000):
                raise ValueError('会话运行文本无效。')
        if record.get('status') not in ('success', 'error', 'incomplete'):
            raise ValueError('会话运行状态无效。')
    text = json.dumps(data, ensure_ascii=False, allow_nan=False)
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('单个会话超过2MiB保存上限，请新建对话。')
    return text


def valid_title(title):
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 80:
        raise ValueError('会话名称须为1—80个字符。')
    return title.strip()


def valid_id(identifier):
    if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
        raise ValueError('请选择有效会话。')


class ConversationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversations'").fetchone()
            if exists and 'owner_key' not in {row['name'] for row in conn.execute('PRAGMA table_info(conversations)')}:
                self.backup_before_ownership_upgrade()
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('''CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, revision INTEGER NOT NULL, title TEXT NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                owner_key TEXT)''')
            if 'owner_key' not in {row['name'] for row in conn.execute('PRAGMA table_info(conversations)')}:
                conn.execute('ALTER TABLE conversations ADD COLUMN owner_key TEXT')
            conn.execute('CREATE INDEX IF NOT EXISTS conversations_owner_updated ON conversations(owner_key,updated_at DESC,id DESC)')

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _decode(row):
        if row is None: raise ValueError('会话已删除或不存在，请刷新列表。')
        result = dict(row)
        raw = result.pop('payload_json')
        try:
            if len(raw.encode('utf-8')) > MAX_BYTES: raise ValueError('过大')
            result['payload'] = json.loads(raw)
            validate_payload(result['payload'])
            valid_title(result['title'])
            valid_id(result['id'])
            if type(result['revision']) is not int or result['revision'] < 1: raise ValueError('版本无效')
        except (TypeError, KeyError, ValueError) as error:
            raise ValueError('会话记录损坏或格式不支持，不能恢复。') from error
        return result

    def list(self, *, owner_key):
        if owner_key is None:
            return []
        validate_owner_key(owner_key)
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(
                'SELECT id,title,revision,updated_at FROM conversations WHERE owner_key=? ORDER BY updated_at DESC,id DESC',
                (owner_key,))]

    def get(self, identifier, *, owner_key):
        validate_owner_key(owner_key)
        valid_id(identifier)
        with closing(self._connect()) as conn:
            return self._decode(conn.execute('SELECT * FROM conversations WHERE id=? AND owner_key=?',
                                             (identifier, owner_key)).fetchone())

    def create(self, payload, title, *, owner_key):
        validate_owner_key(owner_key)
        raw, title = validate_payload(payload), valid_title(title)
        identifier = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as conn, conn:
            conn.execute('INSERT INTO conversations(id,revision,title,payload_json,created_at,updated_at,owner_key) VALUES (?,?,?,?,?,?,?)',
                         (identifier, 1, title, raw, now, now, owner_key))
            return self._decode(conn.execute('SELECT * FROM conversations WHERE id=? AND owner_key=?',
                                             (identifier, owner_key)).fetchone())

    def _mutate(self, identifier, revision, assignment, values=(), *, owner_key):
        validate_owner_key(owner_key)
        valid_id(identifier)
        if type(revision) is not int or revision < 1: raise ValueError('会话版本无效。')
        with closing(self._connect()) as conn, conn:
            if assignment is None:
                cursor = conn.execute('DELETE FROM conversations WHERE id=? AND revision=? AND owner_key=?',
                                      (identifier, revision, owner_key))
            else:
                cursor = conn.execute(f'UPDATE conversations SET {assignment}, revision=revision+1, updated_at=? WHERE id=? AND revision=? AND owner_key=?',
                    (*values, datetime.now(timezone.utc).isoformat(), identifier, revision, owner_key))
            if cursor.rowcount != 1:
                raise ValueError('会话已被另一页面改变或删除，请刷新列表后重新打开。')
            if assignment is not None:
                return self._decode(conn.execute('SELECT * FROM conversations WHERE id=? AND owner_key=?',
                                                 (identifier, owner_key)).fetchone())

    def save(self, identifier, revision, payload, *, owner_key):
        validate_owner_key(owner_key)
        raw = validate_payload(payload)
        valid_id(identifier)
        if type(revision) is not int or revision < 1: raise ValueError('会话版本无效。')
        with closing(self._connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            old = self._decode(conn.execute('SELECT * FROM conversations WHERE id=? AND owner_key=?',
                                            (identifier, owner_key)).fetchone())
            if old['revision'] != revision:
                raise ValueError('会话已被另一页面改变或删除，请刷新列表后重新打开。')
            upgrade = payload.get('format_version') == 2 and 'format_version' not in old['payload']
            if upgrade and conn.execute('PRAGMA user_version').fetchone()[0] < 2:
                self.backup_before_upgrade()
                conn.execute('PRAGMA user_version=2')
            conn.execute('UPDATE conversations SET payload_json=?,revision=revision+1,updated_at=? WHERE id=? AND revision=? AND owner_key=?',
                         (raw, datetime.now(timezone.utc).isoformat(), identifier, revision, owner_key))
            return self._decode(conn.execute('SELECT * FROM conversations WHERE id=? AND owner_key=?',
                                             (identifier, owner_key)).fetchone())

    def backup_before_ownership_upgrade(self):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        target = self.path.with_name(f'{self.path.stem}.pre-ownership-{stamp}-{uuid.uuid4().hex}.sqlite3')
        with target.open('xb'):
            pass
        with closing(self._connect()) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
            if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise sqlite3.DatabaseError('历史会话升级备份未通过完整性检查。')
        return target

    def backup_before_upgrade(self):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        target = self.path.with_name(f'{self.path.stem}.pre-branches-{stamp}-{uuid.uuid4().hex}.sqlite3')
        with target.open('xb'):
            pass
        with closing(self._connect()) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
        return target

    def rename(self, identifier, revision, title, *, owner_key):
        return self._mutate(identifier, revision, 'title=?', (valid_title(title),), owner_key=owner_key)

    def delete(self, identifier, revision, *, owner_key):
        self._mutate(identifier, revision, None, owner_key=owner_key)
