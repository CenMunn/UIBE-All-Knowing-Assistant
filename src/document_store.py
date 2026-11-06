"""SQLite元数据与不可变内容版本，失败不发布半成品。"""
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import RLock, Event, Thread
import time
from uuid import uuid4

from document_models import FileRef, FileRecord, DocumentError, DocumentConflict
from document_validation import validate_upload


def fingerprint(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def display_name(name,extension):
    if not isinstance(name,str):raise DocumentError('文件名无效。')
    name=name.strip()
    if (not name or len(name)>180 or name.endswith(('.', ' '))
            or any(ord(c)<32 or c in '/\\:*?"<>|' for c in name)
            or not name.casefold().endswith(extension)):
        raise DocumentError('请使用有效文件名，并保留原扩展名。')
    stem=name[:-len(extension)].strip()
    if not stem or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?',stem,re.I):
        raise DocumentError('不能使用系统保留的文件名。')
    return name


class DocumentStore:
    def __init__(self,root: Path, *, clock=time.time):
        raw=Path(root).absolute()
        if raw.resolve()!=raw:raise DocumentError('文件库目录不能使用链接。')
        raw.mkdir(parents=True,exist_ok=True)
        self.root=raw;self.clock=clock;self.lock=RLock()
        self._active_mail_leases={}
        for name in ('versions','photos'):
            path=raw/name
            if path.resolve()!=path:raise DocumentError('文件库不能使用链接目录。')
            path.mkdir(exist_ok=True)
        db=raw/'files.sqlite3'
        if db.resolve()!=db:raise DocumentError('文件库数据库不能使用链接。')
        self.db=sqlite3.connect(db,timeout=5,isolation_level=None,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,name TEXT NOT NULL,ext TEXT NOT NULL,mime TEXT NOT NULL,version INTEGER NOT NULL,revision INTEGER NOT NULL,template_id TEXT,template_version TEXT,deleted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS versions(id TEXT NOT NULL,version INTEGER NOT NULL,path TEXT NOT NULL,size INTEGER NOT NULL,digest TEXT NOT NULL,fields TEXT NOT NULL,photo_path TEXT,PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS operations(token TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS leases(id TEXT NOT NULL,token TEXT NOT NULL,purpose TEXT NOT NULL,expires REAL NOT NULL,PRIMARY KEY(id,token));''')

    def close(self):
        with self.lock:self.db.close()

    @contextmanager
    def _transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def _path(self,relative):
        path=self.root/relative
        if not path.is_relative_to(self.root) or path.resolve()!=path:
            raise DocumentError('文件存储位置无效。')
        return path

    def _write_content(self,folder,data):
        relative=f'{folder}/{uuid4().hex}.bin';path=self._path(relative)
        with path.open('xb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        return relative

    def _row(self,ref):
        if not isinstance(ref,FileRef) or not re.fullmatch(r'[a-f0-9]{32}',ref.id):raise DocumentError('无效文件编号。')
        row=self.db.execute('SELECT f.*,v.path,v.size,v.digest,v.fields,v.photo_path FROM files f JOIN versions v ON f.id=v.id AND f.version=v.version WHERE f.id=?',(ref.id,)).fetchone()
        if row is None or row['deleted']:raise DocumentError('文件不存在或已删除。')
        if row['version']!=ref.version or row['revision']!=ref.revision:raise DocumentConflict('文件已被修改，请刷新后重试。')
        return row

    def _record(self,row):
        return FileRecord(FileRef(row['id'],row['version'],row['revision']),row['name'],row['mime'],row['size'],row['digest'],row['template_id'],row['template_version'],bool(row['deleted']))

    def get(self,ref):
        with self.lock:return self._record(self._row(ref))

    def list_files(self,query=''):
        if not isinstance(query,str) or len(query)>200:raise DocumentError('搜索内容无效。')
        with self.lock:
            rows=self.db.execute('SELECT f.*,v.size,v.digest FROM files f JOIN versions v ON f.id=v.id AND f.version=v.version WHERE f.deleted=0 ORDER BY f.rowid DESC').fetchall()
            return tuple(self._record(r) for r in rows if query.casefold() in r['name'].casefold())

    def read(self,ref):
        with self.lock:
            row=self._row(ref);data=self._path(row['path']).read_bytes()
            if len(data)!=row['size'] or sha256(data).hexdigest()!=row['digest']:raise DocumentError('文件内容校验失败，未提供下载或发信。')
            return data

    def form_data(self,ref):
        with self.lock:
            row=self._row(ref)
            return {'fields':json.loads(row['fields']),'photo':self._path(row['photo_path']).read_bytes() if row['photo_path'] else None}

    def _operation(self,token,digest):
        if not isinstance(token,str) or not 1<=len(token)<=200:raise DocumentError('请刷新后重新操作。')
        row=self.db.execute('SELECT fingerprint,result FROM operations WHERE token=?',(token,)).fetchone()
        if row is None:return None
        if row['fingerprint']!=digest:raise DocumentConflict('重复操作的内容已改变，请重新确认。')
        return tuple(self.get(FileRef(**r)) for r in json.loads(row['result']))

    def _unique(self,name,ext,exclude=None):
        taken={r[0].casefold() for r in self.db.execute('SELECT name FROM files WHERE deleted=0 AND id!=?',(exclude or '',))}
        base=name[:-len(ext)];i=1;candidate=name
        while candidate.casefold() in taken:
            i+=1;candidate=f'{base} ({i}){ext}'
        return candidate

    def create(self,name,content,*,nonce,template_id=None,template_version=None,fields=None,photo=None):
        return self.create_batch((dict(name=name,content=content,template_id=template_id,template_version=template_version,fields=fields or {},photo=photo),),nonce)[0]

    def create_batch(self,items,nonce):
        if not 1<=len(items)<=10:raise DocumentError('每次可上传1—10个文件。')
        prepared=[]
        for item in items:
            ext,mime=validate_upload(item['name'],item['content']);name=display_name(item['name'],ext)
            prepared.append(dict(item,name=name,ext=ext,mime=mime))
        digest=fingerprint([dict(name=i['name'],digest=sha256(i['content']).hexdigest(),template=i.get('template_id'),template_version=i.get('template_version'),fields=i.get('fields',{}),photo=sha256(i['photo']).hexdigest() if i.get('photo') else None) for i in prepared])
        with self._transaction():
            previous=self._operation('create:'+nonce,digest)
            if previous is not None:return previous
            records=[]
            for item in prepared:
                ref=FileRef(uuid4().hex,1,1);path=self._write_content('versions',item['content'])
                photo_path=self._write_content('photos',item['photo']) if item.get('photo') else None
                name=self._unique(item['name'],item['ext'])
                self.db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,0)',(ref.id,name,item['ext'],item['mime'],1,1,item.get('template_id'),item.get('template_version')))
                self.db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?)',(ref.id,1,path,len(item['content']),sha256(item['content']).hexdigest(),json.dumps(item.get('fields',{}),ensure_ascii=False),photo_path))
                records.append(self.get(ref))
            self.db.execute('INSERT INTO operations VALUES(?,?,?)',('create:'+nonce,digest,json.dumps([asdict(r.ref) for r in records])))
            return tuple(records)

    def _unleased(self,ref,writer_token=None):
        self._row(ref)
        if any(token!=writer_token for token in self._active_mail_leases.get(ref.id,())):
            raise DocumentConflict('文件正在发送，请等待发送结束。')
        rows=self.db.execute('SELECT token FROM leases WHERE id=? AND expires>?',(ref.id,self.clock())).fetchall()
        if any(r['token']!=writer_token for r in rows):raise DocumentConflict('文件正在填写或发送，请稍后操作。')

    def save(self,ref,content,*,fields,photo,operation_id,writer_token=None):
        digest=fingerprint(dict(ref=asdict(ref),digest=sha256(content).hexdigest(),fields=fields,photo=sha256(photo).hexdigest() if photo else None))
        with self._transaction():
            previous=self._operation('save:'+operation_id,digest)
            if previous is not None:return previous[0]
            current=self.get(ref);validate_upload(current.name,content)
            self._unleased(ref,writer_token)
            path=self._write_content('versions',content);photo_path=self._write_content('photos',photo) if photo else None
            next_ref=FileRef(ref.id,ref.version+1,ref.revision+1)
            self.db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?)',(ref.id,next_ref.version,path,len(content),sha256(content).hexdigest(),json.dumps(fields,ensure_ascii=False),photo_path))
            self.db.execute('UPDATE files SET version=?,revision=? WHERE id=?',(next_ref.version,next_ref.revision,ref.id))
            self.db.execute('INSERT INTO operations VALUES(?,?,?)',('save:'+operation_id,digest,json.dumps([asdict(next_ref)])))
            return self.get(next_ref)

    def rename(self,ref,name):
        with self._transaction():
            row=self._row(ref);self._unleased(ref)
            name=self._unique(display_name(name,row['ext']),row['ext'],ref.id)
            self.db.execute('UPDATE files SET name=?,revision=revision+1 WHERE id=?',(name,ref.id))
            return self.get(FileRef(ref.id,ref.version,ref.revision+1))

    def delete(self,refs):
        if not refs or len(set(r.id for r in refs))!=len(refs):raise DocumentError('请选择不重复的文件。')
        with self._transaction():
            for ref in refs:self._unleased(ref)
            self.db.executemany('UPDATE files SET deleted=1,revision=revision+1 WHERE id=?',[(r.id,) for r in refs])

    def acquire_lease(self,refs,purpose,token=None,ttl=120):
        token=token or uuid4().hex
        with self._transaction():
            for ref in refs:self._unleased(ref,token)
            for ref in refs:self.db.execute('INSERT OR REPLACE INTO leases VALUES(?,?,?,?)',(ref.id,token,purpose,self.clock()+ttl))
        return token

    def renew_lease(self,ref,token,ttl=120):
        with self._transaction():
            self._row(ref)
            row=self.db.execute('SELECT expires FROM leases WHERE id=? AND token=?',(ref.id,token)).fetchone()
            if row is None or row['expires']<=self.clock():raise DocumentConflict('填写会话已过期，请重新打开文件。')
            self.db.execute('UPDATE leases SET expires=? WHERE id=? AND token=?',(self.clock()+ttl,ref.id,token))

    def release_lease(self,token):
        with self._transaction():self.db.execute('DELETE FROM leases WHERE token=?',(token,))

    @contextmanager
    def lease(self,refs,purpose):
        stop=Event();heartbeat=None
        with self.lock:
            token=self.acquire_lease(refs,purpose)
            if purpose=='mail':
                for ref in refs:self._active_mail_leases.setdefault(ref.id,set()).add(token)
        if purpose=='mail':
            def renew_sending():
                while not stop.wait(30):
                    try:
                        with self._transaction():
                            self.db.execute('UPDATE leases SET expires=? WHERE token=?',(self.clock()+120,token))
                    except sqlite3.Error:
                        # 本进程占用仍持续，不能因数据库暂时失败放开正在发送的文件。
                        continue
            heartbeat=Thread(target=renew_sending,daemon=True,name='attachment-lease')
            heartbeat.start()
        try:yield token
        finally:
            stop.set()
            if heartbeat:heartbeat.join()
            with self.lock:
                try:self.release_lease(token)
                finally:
                    for ref in refs:
                        tokens=self._active_mail_leases.get(ref.id,set());tokens.discard(token)
                        if not tokens:self._active_mail_leases.pop(ref.id,None)
