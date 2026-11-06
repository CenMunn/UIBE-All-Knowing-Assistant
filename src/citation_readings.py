"""引用弹窗的离线阅读资料；原文与摘要分开，不更改历史来源快照。"""
from copy import deepcopy
from functools import lru_cache
import json
import re
from urllib.parse import urlsplit
from evidence_view import BLOCK, URL, safe_url
from project_paths import PROJECT_DIR


def _json(relative):
    return json.loads((PROJECT_DIR/relative).read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def load_web_readings():
    try:
        data=_json('knowledge/web_source_readings.json')
    except (OSError,ValueError):
        return {}
    result={}
    for page in data.get('pages',[]):
        url=page.get('url','')
        if (safe_url(url) and urlsplit(url).hostname=='it.uibe.edu.cn'
                and isinstance(page.get('paragraphs'),list)
                and page['paragraphs'] and all(isinstance(p,str) for p in page['paragraphs'])):
            result[url]=dict(kind='original',label='官网正文收录副本',title=page['title'],url=url,
                paragraphs=page['paragraphs'],captured_on=data.get('captured_on',''),
                note=page.get('note',''),publisher=page.get('publisher',''))
    return result


@lru_cache(maxsize=1)
def digest_index():
    rows={}
    def add(key,title,paragraphs,links,checked='',labels=None):
        rows[key]=dict(title=title,paragraphs=paragraphs,links=[u for u in links if safe_url(u)],
                       checked_on=checked,labels=labels or {})
    text='\n\n'.join((PROJECT_DIR/'texts'/name).read_text(encoding='utf-8')
                     for name in ('college_knowledge.txt','curriculum_2024.txt','curriculum_2025.txt'))
    from teacher_catalog import catalog_or_unavailable,teacher_digest
    text=text.replace('{{TEACHER_CATALOG}}','')
    for match in BLOCK.finditer(text):
        key,headline,body=match.groups();labels={};paragraphs=[]
        for line in body.strip().splitlines():
            line=line.strip()
            if not line:continue
            urls=[u for u in URL.findall(line) if safe_url(u)]
            if line.startswith(('来源：','目录日期来源：')):
                for url in urls:labels[url]='制度目录与日期来源' if line.startswith('目录日期来源：') else '官网资料 · '+URL.sub('',headline).strip()
            else:paragraphs.append(line)
        add(key,URL.sub('',headline).strip(),paragraphs or [URL.sub('',headline).strip()],
            URL.findall(headline+body),labels=labels)
    teachers=catalog_or_unavailable()
    from campus_officials import leadership_readings
    rows.update(leadership_readings())
    for teacher in teachers['teachers']:
        digest=teacher_digest(teachers,teacher['id'])
        add(teacher['id'],digest['title'],digest['paragraphs'],digest['links'],digest['checked_on'],digest['labels'])
    for entry in _json('knowledge/campus_entries.json')['entries']:
        if entry['status']=='unverified':continue
        add(entry['id'],entry['title'],[*entry['facts'],entry['limitations']],
            [s['url'] for s in entry['sources']],entry['checked_on'])
    resources=_json('texts/learning_resources.json')
    for row in resources['resources']:
        add(row['id'],row['name'],['语言：'+row['language'],'适合：'+row['audience'],
            '费用：'+row['cost'],'账号：'+row['account'],'设备：'+row['device'],'使用建议：'+row['use']],
            [row['url']],resources['checked_on'],{row['url']:'学习网站 · '+row['name']})
    outlines=_json('knowledge/course_outlines.json')
    for row in outlines['courses']:
        add(row['id'],f"{row['name']}公开教学大纲（{row['code']}）",
            [row[k] for k in ('overview','content','textbooks','limitations')],
            [row['url']],outlines['checked_on'],{row['url']:'官方教学大纲 · '+row['name']})
    supplements=_json('texts/curriculum_supplements.json')
    for row in supplements['entries']:
        add(row['id'],row['title'],[row['body']],[])
    return rows


def reading_details(row):
    if row['id'].startswith('W'):
        s=row['web_snapshot']
        return dict(readings=[dict(kind='digest',label='联网资料摘录 · 非全文',title=row['title'],
            paragraphs=s['excerpt'].split('\n\n'),url=row['links'][0],captured_on=s['fetched_at'],publisher='',
            note='该轮实际提供给模型的网页摘录。发布时间：'+(s['published_on'] or '未知')+'；内容不代表已逐句核实。')],
            source_links=[dict(url=row['links'][0],label='官网原网页 · '+row['title'])])
    known=digest_index().get(row['id'])
    # 同编号但标题或网址已改变时，不把当前资料嫁接到旧版本；无快照的旧回答也不会进入此函数。
    matches=bool(known and row['title']==known['title'] and set(row['links'])==set(known['links']))
    snapshots=load_web_readings() if matches else {}
    readings=[deepcopy(snapshots[url]) for url in row['links'] if url in snapshots]
    for reading in readings:
        reading['note']='当前收录的官网正文副本，仅供阅读；不代表这份正文曾发送给模型，也不是历史问答当时的资料正文快照。'+reading['note']
    if not readings and matches:
        readings=[dict(kind='digest',label='资料摘要 · 非全文',title=known['title'],
            paragraphs=list(known['paragraphs']),url='',captured_on=known['checked_on'],publisher='',
            note='当前收录版本，用于阅读来源；不代表这段摘要曾发送给模型，也不是历史问答的资料正文快照。')]
    links=[]
    for url in row['links']:
        saved=snapshots.get(url)
        label=('官网正文 · '+saved['title']) if saved else ((known['labels'].get(url) if matches else None)
               or '来源网页 · '+urlsplit(url).hostname+urlsplit(url).path)
        links.append(dict(url=url,label=label))
    return dict(readings=readings,source_links=links)
