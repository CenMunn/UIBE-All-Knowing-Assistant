"""将本轮来源快照关联到受控原件；不接受模型或请求提供的磁盘路径。"""
from functools import lru_cache
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from evidence_view import validate_evidence
from project_paths import PROJECT_DIR
from citation_readings import reading_details


@lru_cache(maxsize=1)
def source_catalog():
    rows = {}
    def add(key, path, page=1):
        rows[key] = dict(path=path, page=page)
    for key, page in [('C2401',5),('C2402',8),('C2403',10),('C2404',9)]:
        add(key, 'resources/curricula/2024-information-college-plan.pdf', page)
    for key, page in [('C05',1),('C06',428),('C07',420),('C08',437),('C09',450),('C10',417)]:
        add(key, 'resources/curricula/2025-undergraduate-plan.pdf', page)
    add('P04', 'resources/rules/20251218095902329.doc')
    add('P05', 'resources/rules/20251218095854317.docx')
    supplements = json.loads((PROJECT_DIR/'texts/curriculum_supplements.json').read_text(encoding='utf-8'))
    for row in supplements['entries']:
        page = re.match(r'\d+', row['pages'])
        add(row['id'], supplements['sources'][row['source']]['path'], int(page[0]) if page else 1)
    # 仅列入已知的制度、操作说明及空白申请模板，不暴露学生总表或整个资料目录。
    policy_ids = {'CK04','CK17','CK49','CK50','CK51','CK52','CK54','CK55','CK56','CK57','CK58',
                  *(f'CK{i}' for i in range(61,73)), 'CK76','CK77','CK78'}
    campus = json.loads((PROJECT_DIR/'knowledge/campus_entries.json').read_text(encoding='utf-8'))
    for entry in campus['entries']:
        if entry['id'] not in policy_ids: continue
        files = list(dict.fromkeys(s['local_file'] for s in entry['sources'] if s.get('local_file')
                     and PurePosixPath(s['local_file']).suffix.lower() in {'.pdf','.docx','.doc'}))
        if files:
            add(entry['id'], files[0])
            rows[entry['id']]['attachments'] = [dict(path=value, page=1) for value in files[1:]]
    return rows


def source_document(root, identifier, part=0):
    row = source_catalog().get(identifier) if isinstance(identifier, str) else None
    if row is None: raise ValueError('没有对应原件。')
    choices = [row, *row.get('attachments', [])]
    if type(part) is not int or not 0 <= part < len(choices): raise ValueError('原件序号无效。')
    row = choices[part]
    value = row['path']
    relative = PurePosixPath(value)
    if (relative.is_absolute() or '..' in relative.parts or '\\' in value or ':' in value
            or not value.startswith(('resources/curricula/', 'resources/rules/', 'resources/campus/'))):
        raise ValueError('原件位置无效。')
    root = Path(root).resolve()
    path = root.joinpath(*relative.parts)
    if path.resolve() != path or not path.is_relative_to(root) or not path.is_file():
        raise ValueError('原件缺失或不可用。')
    return path, row['page']


def citation_targets(evidence):
    result = []
    for row in validate_evidence(evidence):
        target = dict(id=row['id'], title=row['title'], links=row['links'], preview='', download='', page=None, documents=[])
        entry = source_catalog().get(row['id'])
        for index in range(1+len(entry.get('attachments', [])) if entry else 0):
            try:
                path, page = source_document(PROJECT_DIR, row['id'], index)
                base = '/sources/'+row['id']+'/content'+(f'?part={index}' if index else '')
                document = dict(name=path.name, preview='', download=base+('&' if index else '?')+'download=true', page=None)
                if path.suffix.lower() in {'.pdf', '.docx'}:
                    document['preview'] = base + (f'#page={page}' if path.suffix.lower()=='.pdf' else '')
                    document['page'] = page if path.suffix.lower()=='.pdf' else None
                target['documents'].append(document)
            except ValueError:
                continue
        if target['documents']:
            target.update({k:target['documents'][0][k] for k in ('preview','download','page')})
        target.update(reading_details(row))
        result.append(target)
    return result


def create_source_routes(root):
    router = APIRouter(prefix='/sources')
    @router.get('/{identifier}/content')
    def content(request: Request, identifier: str, download: bool=False, part: int=0):
        # 主应用中间件还会检查Host/Origin；此处阻止跨站嵌入和直接调用。
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, '请从系统中的资料引用访问。')
        try:
            path, _ = source_document(root, identifier, part)
            data = path.read_bytes()
        except (ValueError, OSError):
            raise HTTPException(404, '原件暂不可用，请关闭预览后重试或查看来源说明。') from None
        headers = {'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff',
                   'Referrer-Policy':'no-referrer'}
        suffix = path.suffix.lower()
        if suffix=='.docx' and not download:
            from document_preview import docx_preview_page
            html, csp = docx_preview_page(path.name, data)
            # 通用只读预览并非个人填写副本。
            html = html.replace('已保存副本'.encode(), '资料原文'.encode())
            return Response(html, media_type='text/html', headers=headers|{'Content-Security-Policy':csp})
        media = {'.pdf':'application/pdf', '.doc':'application/msword',
                 '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}.get(suffix, 'application/octet-stream')
        disposition = 'inline' if suffix=='.pdf' and not download else 'attachment'
        headers['Content-Disposition'] = disposition+"; filename*=UTF-8''"+quote(path.name, safe='')
        headers['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'self'; sandbox"
        return Response(data, media_type=media, headers=headers)
    return router
