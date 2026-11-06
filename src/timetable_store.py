"""Versioned local schedule snapshots; no access to existing business databases."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from timetable_models import ScheduleRef, snapshot_from_dict, snapshot_to_dict
from timetable_rules import validate_schedule


class ScheduleDeleted(ValueError):
    pass


class TimetableStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,
                    name TEXT NOT NULL,term TEXT NOT NULL,active INTEGER NOT NULL,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(id TEXT NOT NULL,revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,PRIMARY KEY(id,revision));
                CREATE TABLE IF NOT EXISTS creations(token TEXT PRIMARY KEY,request TEXT NOT NULL,id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS schedule_deletions(id TEXT PRIMARY KEY,revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS intents(token TEXT PRIMARY KEY,event_id TEXT NOT NULL,
                    status TEXT NOT NULL,task_id TEXT UNIQUE,payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_live_intent ON intents(event_id)
                    WHERE status IN ('reserved','bound');
            ''')

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if write: db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally: db.close()

    def close(self):
        pass  # Connections belong to individual transactions, not this facade.

    def list_schedules(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT id,revision,name,term,active FROM schedules WHERE id NOT IN (SELECT id FROM schedule_deletions) ORDER BY rowid DESC')]

    def is_live(self, schedule_id):
        with self.connection() as db:
            return db.execute('SELECT 1 FROM schedules WHERE id=? AND id NOT IN (SELECT id FROM schedule_deletions)',(schedule_id,)).fetchone() is not None

    def _check_deleted(self, db, schedule_id):
        if db.execute('SELECT 1 FROM schedule_deletions WHERE id=?',(schedule_id,)).fetchone():
            raise ScheduleDeleted('课表已删除，请重新选择。')

    def load(self, schedule_id):
        with self.connection() as db:
            self._check_deleted(db,schedule_id)
            row = db.execute('SELECT payload FROM schedules WHERE id=?', (schedule_id,)).fetchone()
        if row is None: raise ValueError('课表不存在，请刷新列表。')
        return snapshot_from_dict(json.loads(row['payload']))

    def _write_version(self, db, s):
        payload = json.dumps(snapshot_to_dict(s), ensure_ascii=False, separators=(',', ':'))
        db.execute('INSERT OR REPLACE INTO schedules VALUES(?,?,?,?,?,?)',
                   (s.ref.id, s.ref.revision, s.name, s.term, int(s.active), payload))
        db.execute('INSERT INTO versions VALUES(?,?,?)', (s.ref.id, s.ref.revision, payload))

    def _deactivate_others(self, db, s):
        if not s.active: return
        rows = db.execute('SELECT payload FROM schedules WHERE active=1 AND id<>?', (s.ref.id,)).fetchall()
        for row in rows:
            old = snapshot_from_dict(json.loads(row['payload']))
            self._write_version(db, replace(old, active=False,
                ref=ScheduleRef(old.ref.id, old.ref.revision+1)))

    def create(self, snapshot, token):
        validate_schedule(snapshot)
        if not isinstance(token, str) or not 1 <= len(token) <= 100: raise ValueError('创建确认无效。')
        request = json.dumps(snapshot_to_dict(snapshot), sort_keys=True, ensure_ascii=False)
        with self.connection(True) as db:
            old = db.execute('SELECT * FROM creations WHERE token=?', (token,)).fetchone()
            if old:
                self._check_deleted(db,old['id'])
                if old['request'] != request: raise ValueError('重复创建内容已改变，请重新确认。')
                return snapshot_from_dict(json.loads(db.execute('SELECT payload FROM schedules WHERE id=?', (old['id'],)).fetchone()[0]))
            if db.execute('SELECT 1 FROM schedules WHERE id=?', (snapshot.ref.id,)).fetchone():
                raise ValueError('课表编号已存在。')
            result = replace(snapshot, ref=ScheduleRef(snapshot.ref.id, 1))
            self._deactivate_others(db, result)
            self._write_version(db, result)
            db.execute('INSERT INTO creations VALUES(?,?,?)', (token, request, result.ref.id))
            return result

    def save(self, snapshot, expected):
        validate_schedule(snapshot)
        if snapshot.ref != expected: raise ValueError('保存目标已变化。')
        with self.connection(True) as db:
            self._check_deleted(db,expected.id)
            row = db.execute('SELECT revision FROM schedules WHERE id=?', (expected.id,)).fetchone()
            if row is None or row['revision'] != expected.revision:
                raise ValueError('课表已在另一处修改，请重新打开后再保存。')
            result = replace(snapshot, ref=ScheduleRef(expected.id, expected.revision+1))
            self._deactivate_others(db, result)
            self._write_version(db, result)
            return result

    def delete(self, expected, *, finished_tokens=()):
        with self.connection(True) as db:
            self._check_deleted(db,expected.id)
            row=db.execute('SELECT revision,payload FROM schedules WHERE id=?',(expected.id,)).fetchone()
            if row is None or row['revision']!=expected.revision:
                raise ValueError('课表已变化，请重新打开后确认删除。')
            snapshot=snapshot_from_dict(json.loads(row['payload']))
            event_ids={e.id for e in snapshot.events}
            intents=[]
            for token in finished_tokens:
                intent=db.execute('SELECT payload FROM intents WHERE token=?',(token,)).fetchone()
                data=json.loads(intent[0]) if intent else None
                if not data or data['event_id'] not in event_ids or data.get('schedule_id')!=expected.id:
                    raise ValueError('删除关联的提醒确认无效。')
                intents.append(data)
            live={r['token'] for r in db.execute("SELECT token,event_id FROM intents WHERE status IN ('reserved','bound')") if r['event_id'] in event_ids}
            if live-set(finished_tokens):raise ValueError('请先核对该课表的全部关联提醒。')
            result=replace(snapshot,active=False,ref=ScheduleRef(expected.id,expected.revision+1))
            self._write_version(db,result)
            db.execute('INSERT INTO schedule_deletions VALUES(?,?)',(expected.id,result.ref.revision))
            for data in intents:
                data['status']='finished'
                db.execute('UPDATE intents SET status=?,payload=? WHERE token=?',('finished',json.dumps(data,ensure_ascii=False),data['token']))

    def event(self, event_id):
        for row in self.list_schedules():
            s = self.load(row['id'])
            for event in s.events:
                if event.id == event_id: return s, event
        raise ValueError('来源事务不存在。')

    def get_intent(self, token):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM intents WHERE token=?', (token,)).fetchone()
        return json.loads(row[0]) if row else None

    def reserve_intent(self, data):
        old = self.get_intent(data['token'])
        if old:
            if any(old.get(k) != v for k, v in data.items()): raise ValueError('提醒确认内容已变化。')
            return old
        try:
            with self.connection(True) as db:
                db.execute('INSERT INTO intents VALUES(?,?,?,?,?)', (data['token'], data['event_id'],
                    'reserved', None, json.dumps(data | {'status':'reserved'}, ensure_ascii=False)))
        except sqlite3.IntegrityError as exc:
            raise ValueError('该事务已有提醒或未完成的创建，请先核对记录。') from exc
        return self.get_intent(data['token'])

    def update_intent(self, token, **fields):
        with self.connection(True) as db:
            row = db.execute('SELECT payload FROM intents WHERE token=?', (token,)).fetchone()
            if not row: raise ValueError('提醒来源记录不存在。')
            data = json.loads(row[0]) | fields
            db.execute('UPDATE intents SET status=?,task_id=?,payload=? WHERE token=?',
                (data['status'], data.get('task_id'), json.dumps(data, ensure_ascii=False), token))
        return data

    def bind_task(self, token, task_id):
        return self.update_intent(token, status='bound', task_id=task_id)

    def intents_for_event(self, event_id):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM intents WHERE event_id=?', (event_id,))]

    def find_intent_for_task(self, task_id):
        with self.connection() as db:
            row = db.execute('SELECT payload FROM intents WHERE task_id=?', (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def unresolved_intents(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM intents WHERE status='reserved'")]
