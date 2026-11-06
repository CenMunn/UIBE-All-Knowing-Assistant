"""每轮网页摘录与消息绑定，不以全局W编号重建旧网页。"""
import json
import re
from datetime import datetime
from web_search import public_url

def validate_web_row(row):
    s=row.get('web_snapshot')
    if (row['id'] not in ('W01','W02') or len(row['links'])!=1 or not public_url(row['links'][0]) or
        not isinstance(s,dict) or set(s)!={'kind','excerpt','fetched_at','published_on'} or
        s['kind']!='web_excerpt' or not isinstance(s['excerpt'],str) or not s['excerpt'].strip() or len(s['excerpt'])>1500 or
        not isinstance(s['fetched_at'],str) or len(s['fetched_at'])>40 or
        (s['published_on'] is not None and (not isinstance(s['published_on'],str) or len(s['published_on'])>40))):
        raise ValueError('联网来源快照无效。')
    try:
        datetime.fromisoformat(s['fetched_at'])
        if s['published_on']:datetime.fromisoformat(s['published_on'])
    except ValueError:raise ValueError('联网来源日期无效。') from None

def render_web_evidence(rows):
    return ''.join('\n['+r['id']+'] '+json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows)

def select_web_evidence(pages,*,char_budget):
    rows=[];urls=set()
    for page in pages:
        if len(rows)>=2:break
        if not public_url(page.url) or page.url in urls:continue
        paragraphs=[];size=0
        for p in re.split(r'\n\s*\n',page.content):
            p=p.strip()
            if not p or len(p)>1500:continue
            if size+len(p)+(2 if paragraphs else 0)>1500:break
            paragraphs.append(p);size+=len(p)+(2 if len(paragraphs)>1 else 0)
        if not paragraphs:continue
        row=dict(id='W'+str(len(rows)+1).zfill(2),title=page.title,links=[page.url],
            version=page.published_on or '',checked_on=page.fetched_at[:10],
            web_snapshot=dict(kind='web_excerpt',excerpt='\n\n'.join(paragraphs),fetched_at=page.fetched_at,published_on=page.published_on))
        try:validate_web_row(row)
        except ValueError:continue
        if len(render_web_evidence(rows+[row]))>char_budget:continue
        rows.append(row);urls.add(page.url)
    return rows
