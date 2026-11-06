"""服务端模型发现与只读缓存；不保存密钥或原始接口异常。"""
from copy import deepcopy
import re
from threading import Lock, RLock
from time import monotonic
from typing import Any
from assistant_features import feature_capabilities

ALIASES = {'deepseek-v4-flash':'deepseek-flash', 'deepseek-v4-flash-vision-exp':'deepseek-flash'}
KNOWN = {'deepseek-flash':'DeepSeek-V4.1-Flash', 'deepseek-v4-pro':'DeepSeek-V4-Pro'}


def valid_model_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}',value):
        raise ValueError('模型标识无效，请重新选择。')
    return value


def normalize_models(data):
    if not isinstance(data,list) or len(data)>200:raise ValueError('模型目录结构无效。')
    result={}
    ids={m.get('id') for m in data if isinstance(m,dict) and isinstance(m.get('id'),str)}
    for raw in data:
        if not isinstance(raw,dict):raise ValueError('模型目录结构无效。')
        mid=valid_model_id(raw.get('id'));known=ALIASES.get(mid,mid) in KNOWN
        if mid in ALIASES and ALIASES[mid] in ids:continue
        if any('text' not in raw.get(k, ['text'] if known else []) for k in ('input_modalities','output_modalities')):continue
        protocols=raw.get('api_capabilities') or {}
        if not isinstance(protocols,dict):continue
        if protocols.get('chat_completions') is False:continue
        effort=raw.get('effort')
        thinking=(isinstance(effort,dict) and 'high' in effort.get('supported_levels',[])) if effort is not None else known
        name=raw.get('name') or KNOWN.get(mid,mid)
        if not isinstance(name,str) or not 1<=len(name)<=120 or any(ord(c)<32 for c in name):name=mid
        result[mid]=dict(id=mid,name=name,thinking_available=thinking,web_available=True)
    return list(result.values())


class ModelCatalog:
    def __init__(self,client_factory,base_url,configured_model,*,clock=monotonic,ttl_seconds=600):
        self._factory=client_factory
        self._base_url=str(base_url)
        self.configured_model=valid_model_id(configured_model)
        self._clock=clock;self._ttl=ttl_seconds
        self._lock=RLock();self._refresh_lock=Lock()
        self._models=[];self._verified=False;self._at=None;self._failed=False
        self._status='尚未取得模型目录；暂用配置模型，可刷新重试。'

    def snapshot(self):
        with self._lock:
            expired=self._at is not None and self._clock()-self._at>=self._ttl
            status=self._status
            if expired and not self._failed:
                status='模型目录缓存已过期，暂保留上次结果；可点击“刷新模型”重新核对。'
            return dict(models=deepcopy(self._models),verified=self._verified,
                        stale=self._failed or self._at is None or expired,
                        status=status)

    def refresh(self):
        if not self._refresh_lock.acquire(blocking=False):return self.snapshot()
        try:
            if not feature_capabilities(self._base_url,'')['web_available']:
                raise ValueError('非官方端点')
            with self._factory() as client:
                if not feature_capabilities(str(client.base_url),'')['web_available']:raise ValueError('非官方客户端')
                data=client.with_options(timeout=10.0,max_retries=0).get('/models',cast_to=dict[str,Any],
                    options={'follow_redirects':False})
                models=normalize_models(data['data'])
            with self._lock:
                self._models=models;self._verified=True;self._at=self._clock();self._failed=False
                self._status='模型目录已更新。' if models else '当前目录没有兼容的文本聊天模型，请稍后刷新。'
        except Exception:
            with self._lock:
                self._failed=True
                self._status=('目录刷新失败，保留上次核对结果。' if self._verified else
                              '暂未取得模型目录，仅保留配置模型；请检查连接后刷新。')
        finally:self._refresh_lock.release()
        return self.snapshot()

    def require_available(self,model_id):
        mid=valid_model_id(model_id)
        s=self.snapshot()
        for item in s['models']:
            if item['id']==mid:return item
        if not s['verified'] and mid==self.configured_model:
            caps=feature_capabilities(self._base_url,mid)
            return dict(id=mid,name=KNOWN.get(mid,mid),thinking_available=caps['thinking_available'],web_available=caps['web_available'])
        raise ValueError('该模型当前不可用，请从模型列表重新选择或刷新目录；原对话保留。')

    def capabilities(self,model_id):
        base=feature_capabilities(self._base_url,model_id)
        try:item=self.require_available(model_id)
        except ValueError:
            return dict(web_available=False,thinking_available=False,web_reason='当前模型不可用，请重新选择。',thinking_reason='当前模型不可用，请重新选择。')
        thinking=base['web_available'] and item['thinking_available']
        return dict(web_available=base['web_available'] and item['web_available'],thinking_available=thinking,
                    web_reason=base['web_reason'],thinking_reason='' if thinking else '当前模型未确认支持深度思考。')
