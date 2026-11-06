"""从实际发送的系统资料捕获来源快照；不从用户或模型文字创造来源。"""
from copy import deepcopy
from html import escape
import json
import re
from urllib.parse import urlsplit

ID = r'(?:CK|LR|C|T|P|S|E|G|D|R|A|W)\d{2,}'
BLOCK = re.compile(r'^\[(' + ID + r')\]\s*([^\n]*)(.*?)(?=^\[(?:' + ID + r')\]|^## |\Z)', re.M | re.S)
URL = re.compile(r'https?://[^\s<>"，。；）)]+')


def safe_url(value: str) -> bool:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        return False
    try:
        p = urlsplit(value)
        return p.scheme in ('http', 'https') and bool(p.hostname) and not p.username and not p.password and '\\' not in value
    except ValueError:
        return False


def validate_evidence(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError('来源记录数量无效。')
    seen = set()
    for row in value:
        fields = {'id', 'title', 'links', 'version', 'checked_on'}
        if not isinstance(row, dict) or set(row) not in (fields, fields | {'provenance'}, fields | {'web_snapshot'}):
            raise ValueError('来源记录结构无效。')
        if not isinstance(row['id'], str) or not re.fullmatch(ID, row['id']) or row['id'] in seen:
            raise ValueError('来源编号无效。')
        seen.add(row['id'])
        for key, limit in (('title', 600), ('version', 200), ('checked_on', 40)):
            if not isinstance(row[key], str) or len(row[key]) > limit:
                raise ValueError('来源文字无效。')
        if not isinstance(row['links'], list) or len(row['links']) > 10 or any(not safe_url(v) for v in row['links']):
            raise ValueError('来源链接无效。')
        provenance = row.get('provenance', [])
        if (not isinstance(provenance, list) or len(provenance) > 10
                or any(not isinstance(v, str) or not v.strip() or len(v) > 600 for v in provenance)):
            raise ValueError('来源取得方式无效。')
        if row['id'].startswith('W'):
            from web_evidence import validate_web_row
            validate_web_row(row)
        elif 'web_snapshot' in row:
            raise ValueError('本地来源不应包含联网快照。')
    return deepcopy(value)


def registry_from_system(text: str) -> dict:
    registry = {}
    for match in BLOCK.finditer(text):
        identifier, headline, body = match.groups()
        checked, version = '', ''
        links = []
        provenance = []
        title = URL.sub('', headline).strip()
        if identifier.startswith('W'):
            try:
                data=json.loads(headline)
                if data.get('id')!=identifier:continue
                registry[identifier]=validate_evidence([data])[0]
            except (ValueError,KeyError,TypeError):pass
            continue
        if identifier.startswith('CK'):
            try:
                data = json.loads(headline)
                title = data['title']
                links = [s['url'] for s in data['sources']]
                checked = data.get('checked_on', '')
                version = data.get('published_on') or ''
                provenance = list(dict.fromkeys(s.get('provenance', '') for s in data['sources']
                                                if isinstance(s.get('provenance'), str)
                                                and s['provenance'].strip()))[:10]
            except (ValueError, KeyError, TypeError):
                continue
        else:
            links = URL.findall(headline + body)
            date_match = re.search(r'(?:核查日期|核对日期|checked_on)[：: ]+(\d{4}-\d{2}-\d{2})', body)
            if date_match: checked = date_match.group(1)
        if title:
            registry[identifier] = dict(id=identifier, title=title[:600],
                links=list(dict.fromkeys(u for u in links if safe_url(u)))[:10],
                version=version, checked_on=checked)
            if provenance:
                registry[identifier]['provenance'] = [v[:600] for v in provenance]
    return registry


def capture_evidence(messages: list[dict], registry: dict | None = None) -> list[dict]:
    system = '\n'.join(m['content'] for m in messages if m.get('role') == 'system')
    registry = registry_from_system(system) if registry is None else registry
    ids = list(dict.fromkeys(re.findall(r'^\[(' + ID + r')\]', system, re.M)))
    return validate_evidence([registry[key] for key in ids if key in registry][:100])


def classify_evidence(answer: str, evidence: list[dict]) -> dict:
    rows = validate_evidence(evidence)
    cited = citation_ids(answer)
    return {'cited': [r for r in rows if r['id'] in cited],
            'provided': [r for r in rows if r['id'] not in cited]}


def citation_ids(answer: str) -> set[str]:
    # 检查未知前缀和组合引用，而不仅是目录里已经存在的前缀。
    token = r'[A-Z]{1,6}\d{2,}'
    groups = re.findall(r'[〔\[]\s*(' + token + r'(?:\s*[,，、;；/]\s*' + token + r')*)\s*[〕\]]', answer)
    # 圆括号常用于课程代码；只识别已定义的来源前缀，避免把（BDT308）当引用。
    groups += re.findall(r'[（(]\s*(' + ID + r'(?:\s*[,，、;；/]\s*(?:' + ID + r'))*)\s*[）)]', answer)
    return {key for group in groups for key in re.findall(token, group)}


def citation_issues(answer: str, evidence: list[dict]) -> list[str]:
    """机械校验实际引用及同段学习链接，不代替逐句语义验收。"""
    registry = {row['id']: row for row in validate_evidence(evidence)}
    cited = citation_ids(answer)
    issues = ['引用编号未在本轮提供：' + key for key in sorted(cited - registry.keys())]
    normalize = lambda url: url.rstrip('.,;:。；，/]*')
    for paragraph in re.split(r'\n\s*\n', answer):
        paragraph_ids = citation_ids(paragraph).intersection(registry)
        resources = {key for key in paragraph_ids if key.startswith(('LR','W'))}
        if not resources:
            continue
        allowed = {normalize(url) for key in paragraph_ids for url in registry[key]['links']}
        for url in URL.findall(paragraph):
            if normalize(url) not in allowed:
                issues.append(('网页链接' if any(k.startswith('W') for k in resources) else '学习链接')+'与同段引用编号不匹配：' + '/'.join(sorted(resources)))
    return list(dict.fromkeys(issues))


def render_evidence(answer: str, evidence: list[dict]) -> str:
    groups = classify_evidence(answer, evidence)
    if not evidence: return ''
    parts = ['<details class="answer-evidence"><summary>查看依据</summary>']
    for key, label in (('cited', '回答引用'), ('provided', '提供给模型的参考资料')):
        if not groups[key]: continue
        parts.append(('<details class="provided-evidence"><summary>' + label + f'（{len(groups[key])}项）</summary><ul>')
                     if key == 'provided' else '<section><strong>' + label + '</strong><ul>')
        for row in groups[key]:
            parts.append('<li><span>' + escape(row['id'] + ' · ' + row['title']) + '</span>')
            for index, url in enumerate(row['links']):
                parts.append(f' <a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">来源{index+1}</a>')
            for provenance in row.get('provenance', []):
                parts.append('<small>' + escape(provenance) + '</small>')
            if row['version']: parts.append('<small>版本：' + escape(row['version']) + '</small>')
            if row['checked_on']: parts.append('<small>核对：' + escape(row['checked_on']) + '</small>')
            parts.append('</li>')
        parts.append('</ul></details>' if key == 'provided' else '</ul></section>')
    parts.append('<small>引用编号不代表已逐句核验；参考资料不等于回答全部采用。</small></details>')
    return ''.join(parts)
