"""Explicit official source registry, never an arbitrary-URL fetch permission."""
from copy import deepcopy
from functools import lru_cache
import json
from urllib.parse import urlsplit,parse_qs
from project_paths import PROJECT_DIR


@lru_cache(maxsize=1)
def _sources():
    data=json.loads((PROJECT_DIR/'knowledge/faculty_sources.json').read_text(encoding='utf-8'))
    if data.get('schema_version')!=1 or not isinstance(data.get('colleges'),list):raise ValueError('教师官网登记无效。')
    ids=[c['id'] for c in data['colleges']]
    if len(ids)!=len(set(ids)):raise ValueError('学院官网编号重复。')
    return data


def load_sources():return deepcopy(_sources())


def official_url(url,*,college_id=None,sources=None):
    if not isinstance(url,str) or len(url)>2048 or any(ord(c)<33 for c in url) or '\\' in url:return False
    try:
        p=urlsplit(url)
        if p.username or p.password or p.port or p.fragment:return False
        registry=sources or _sources()
        for c in registry['colleges']:
            if college_id is not None and c['id']!=college_id:continue
            if p.hostname in c['hosts'] and p.scheme in c['schemes']:
                return set(parse_qs(p.query,keep_blank_values=True)).issubset(c['query_keys'])
        return False
    except (ValueError,TypeError,KeyError):return False
