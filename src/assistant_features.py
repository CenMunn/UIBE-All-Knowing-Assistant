"""对话功能设置、端点能力与安全的运行记录。此处不保存凭据。"""
from urllib.parse import urlsplit
import math
import re

FEATURE_KEYS = ('web_enabled', 'deep_thinking')

def sdk_parameters(temperature, extra_body, max_tokens, thinking):
    body=dict(extra_body or {})
    for key in ('temperature','presence_penalty','frequency_penalty','reasoning_effort'):
        body.pop(key,None)
    if thinking:
        body['thinking']={'type':'enabled'}
        return dict(extra_body=body,reasoning_effort='high',max_tokens=8192)
    if body.get('thinking') is not None:body['thinking']={'type':'disabled'}
    return dict(extra_body=body,temperature=temperature,max_tokens=max_tokens)

def feature_settings(values):
    result = {key: values.get(key, False) for key in FEATURE_KEYS}
    if any(type(v) is not bool for v in result.values()):
        raise ValueError('功能开关须为布尔值。')
    return result

def archive_usage(value):
    if value is None:return None
    if not isinstance(value,dict):raise ValueError('用量记录无效。')
    result={}
    def valid(v):
        if v is not None and (type(v) is not int or v<0):raise ValueError('用量记录无效。')
        return v
    for k in ('prompt_tokens','completion_tokens','total_tokens'):
        if k in value:result[k]=valid(value[k])
    if value.get('completion_tokens_details') is not None:
        details=value['completion_tokens_details']
        if not isinstance(details,dict):raise ValueError('用量明细无效。')
        if 'reasoning_tokens' in details:result['completion_tokens_details']={'reasoning_tokens':valid(details['reasoning_tokens'])}
    return result

def validate_feature_run(v):
    fields={'thinking','search_status','search_requests','fetch_requests','web_ids','search_seconds','error_code'}
    if (not isinstance(v,dict) or set(v)!=fields or type(v['thinking']) is not bool or
        v['search_status'] not in {'disabled','local_only','used','empty','error','blocked'} or
        any(type(v[k]) is not int or not 0<=v[k]<=2 for k in ('search_requests','fetch_requests')) or
        not isinstance(v['web_ids'],list) or len(v['web_ids'])>2 or len(set(v['web_ids']))!=len(v['web_ids']) or
        any(x not in ('W01','W02') for x in v['web_ids']) or
        (v['search_seconds'] is not None and (type(v['search_seconds']) not in (int,float) or not math.isfinite(v['search_seconds']) or v['search_seconds']<0)) or
        (v['error_code'] is not None and (not isinstance(v['error_code'],str) or not re.fullmatch(r'[a-z0-9_]{1,80}',v['error_code'])))):
        raise ValueError('功能运行记录无效。')

def feature_capabilities(base_url, model, model_metadata=None):
    try:
        u = urlsplit(str(base_url))
        official = (u.scheme == 'https' and u.hostname == 'api.deepseek.com' and
                    u.port in (None, 443) and not u.username and not u.password and
                    not u.query and not u.fragment and u.path in ('','/','/v1','/v1/'))
    except ValueError:
        official = False
    thinking = official and model in ('deepseek-flash', 'deepseek-v4-pro')
    if model_metadata is not None:
        thinking=official and model_metadata.get('thinking_available') is True
    return dict(web_available=official, thinking_available=thinking,
                web_reason='' if official else '联网补充仅支持已配置的 DeepSeek 官方端点。',
                thinking_reason='' if thinking else '当前端点或模型不支持已核对的深度思考参数。')
