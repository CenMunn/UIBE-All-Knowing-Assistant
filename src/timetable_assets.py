"""Private, normalized schedule images and short-lived, view-bound preview tickets."""
import base64
import hashlib
import hmac
from io import BytesIO
import json
from pathlib import Path
import re
from threading import RLock
import time
from uuid import uuid4
import warnings
from PIL import Image, ImageOps

MAX_IMAGE = 10*1024*1024
MAX_TOTAL = 20*1024*1024


class TimetableAssets:
    def __init__(self, root, secret, *, schedule_is_live=None):
        self.root = Path(root)
        self.staging = self.root/'staging'; self.images = self.root/'images'
        self.staging.mkdir(parents=True, exist_ok=True); self.images.mkdir(parents=True, exist_ok=True)
        self.secret = secret
        self.entries = {}; self.access = {}; self.lock = RLock()
        self.schedule_scopes={};self.schedule_is_live=schedule_is_live

    def stage(self, upload_paths, *, upload_root, session_id):
        if not 1 <= len(upload_paths) <= 4: raise ValueError('一次请选择1—4张PNG/JPEG课表图片。')
        root = Path(upload_root).resolve(strict=True)
        inputs = []
        for raw in upload_paths:
            path = Path(raw).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in {'.png','.jpg','.jpeg'}:
                raise ValueError('上传图片路径或格式无效。')
            before = path.stat()
            if not 0 < before.st_size <= MAX_IMAGE: raise ValueError('每张图片须不超过10MiB。')
            content = path.read_bytes()
            after = path.stat()
            if before.st_ino != after.st_ino or before.st_mtime_ns != after.st_mtime_ns or len(content) != before.st_size:
                raise ValueError('上传文件在读取时改变，请重新选择。')
            inputs.append(content)
        if sum(map(len, inputs)) > MAX_TOTAL: raise ValueError('图片总大小须不超过20MiB。')
        normalized = []
        try:
            for content in inputs:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(BytesIO(content)) as im:
                        if im.format not in {'PNG','JPEG'} or max(im.size) > 8192 or im.width*im.height > 16_000_000:
                            raise ValueError('图片格式或像素超出限制。')
                        im.load(); image = ImageOps.exif_transpose(im).convert('RGB')
                        image.info.clear()
                        out = BytesIO(); image.save(out, format='PNG')
                        data = out.getvalue()
                        if len(data) > MAX_IMAGE: raise ValueError('规范化后的图片超过10MiB，请裁切后再试。')
                        normalized.append((data, image.size))
        except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError('图片损坏或无法安全解码。') from exc
        if sum(len(data) for data, _ in normalized) > MAX_TOTAL: raise ValueError('规范化图片总量超过20MiB。')
        result = []
        try:
            with self.lock:
                for data, size in normalized:
                    key = uuid4().hex
                    (self.staging/f'{key}.png').write_bytes(data)
                    self.entries[key] = dict(id=key, owner=session_id, committed=False)
                    result.append(dict(id=key, width=size[0], height=size[1], size=len(data)))
        except OSError:
            self.discard(tuple(r['id'] for r in result), session_id=session_id)
            raise
        return tuple(result)

    def _path(self, key):
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{32}', key): raise ValueError('图片编号无效。')
        committed = self.images/f'{key}.png'
        return committed if committed.is_file() else self.staging/f'{key}.png'

    def authorize(self, ids, *, session_id, view_token, schedule_ref=None):
        with self.lock:
            if schedule_ref is not None and self.schedule_is_live and not self.schedule_is_live(schedule_ref.id):
                raise ValueError('课表已删除，请重新选择。')
            for key in ids:
                entry = self.entries.get(key)
                if not self._path(key).is_file() or (entry and not entry['committed'] and entry['owner'] != session_id):
                    raise ValueError('图片不属于当前课表。')
            self.access[session_id] = (view_token, frozenset(ids))
            self.schedule_scopes[session_id]=schedule_ref.id if schedule_ref else None

    def revoke(self, session_id):
        with self.lock:
            self.access.pop(session_id, None);self.schedule_scopes.pop(session_id,None)

    def revoke_schedule(self, schedule_id):
        with self.lock:
            for session in list(self.schedule_scopes):
                if self.schedule_scopes[session]==schedule_id:self.revoke(session)

    def read(self, key, *, session_id):
        with self.lock:
            entry = self.entries.get(key)
            grant = self.access.get(session_id, ('', frozenset()))
            if not (entry and entry['owner'] == session_id) and key not in grant[1]:
                raise ValueError('图片不属于当前会话。')
            try: return self._path(key).read_bytes()
            except OSError: raise ValueError('预览图片已失效，请重新上传。') from None

    def commit(self, source_ids, schedule):
        with self.lock:
            for key in source_ids:
                path = self._path(key)
                if path.parent == self.staging: path.replace(self.images/path.name)
                if key in self.entries: self.entries[key]['committed'] = True

    def discard(self, source_ids, *, session_id):
        with self.lock:
            for key in source_ids:
                entry = self.entries.get(key)
                if entry and entry['owner'] == session_id and not entry['committed']:
                    self._path(key).unlink(missing_ok=True); self.entries.pop(key, None)

    def cleanup_staging(self):
        # Caller must hold the runtime's exclusive project lock.
        for path in self.staging.iterdir():
            if path.is_file() and re.fullmatch('[0-9a-f]{32}\\.png', path.name): path.unlink()

    def preview_ticket(self, key, *, session_id, view_token):
        self.resolve_access(key, session_id, view_token)
        payload = json.dumps([key, session_id, view_token, int(time.time())+600], separators=(',', ':')).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip('=')
        signature = hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded+'.'+signature

    def resolve_access(self, key, session_id, view_token):
        with self.lock:
            grant = self.access.get(session_id)
            scope=self.schedule_scopes.get(session_id)
            if (not grant or grant[0] != view_token or key not in grant[1] or
                (scope and self.schedule_is_live and not self.schedule_is_live(scope))):
                raise ValueError('图片预览授权已失效。')
            return self.read(key, session_id=session_id)

    def verify_ticket(self, key, token, resolve_access):
        try:
            if not isinstance(token, str) or len(token)>2048: raise ValueError()
            encoded, signature = token.split('.')
            expected = hmac.new(self.secret, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature): raise ValueError()
            data = json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
            if len(data)!=4 or data[0]!=key or type(data[3]) is not int or not time.time() < data[3] <= time.time()+601:
                raise ValueError()
            return resolve_access(key, data[1], data[2])
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise ValueError('图片预览授权无效或已过期。') from exc
