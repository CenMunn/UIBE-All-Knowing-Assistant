"""学院职务资料按明确对象提供；不从专业或学号猜学院。"""
import json
import re
from project_paths import PROJECT_DIR

def _data():
    return json.loads((PROJECT_DIR/'knowledge/college_leadership.json').read_text(encoding='utf-8'))

SCHOOLS = ('对外经济贸易大学', '对外经贸大学', '贸大', 'UIBE')

def _scope(text, aliases):
    found = any(a in text for a in aliases)
    for a in (*SCHOOLS, *aliases, '我们学院', '我院', '本学院', '学院', '我们大学'):
        # 未指定名称的“学院”只用于检测；其他学院不能先被删掉。
        if a != '学院': text = text.replace(a, '')
    if re.search(r'[\u4e00-\u9fff]{1,20}(?:大学|学院)', text):
        return False
    return True if found else None

def leadership_context(question, history, background, *, limit=1400):
    if not re.search(r'院长|学院领导|院领导', question): return ''
    if re.search(r'20\d{2}年|以前|曾任|历任|前任|去年|前年', question): return ''
    row = _data()
    scope = _scope(question, row['aliases'])
    if scope is False: return ''
    school = background.get('school', '')
    if school and school.casefold() not in {s.casefold() for s in SCHOOLS} and not any(s in question for s in SCHOOLS):
        return ''
    if scope is not True:
        scope = _scope(background.get('college', ''), row['aliases'])
        if scope is False: return ''
    if scope is not True:
        for message in reversed((history or [])[-12:]):
            if message.get('role') != 'user': continue
            scope = _scope(message.get('content', ''), row['aliases'])
            if scope is not None: break
            if not re.match(r'^(那|这|他|她)', message.get('content', '')): break
    if scope is not True: return ''
    block = (f"\n\n[{row['id']}] {row['title']}\n核对日期：{row['checked_on']}\n"
             + '\n'.join(row['facts'])+'\n'+row['limitations']+'\n来源：'+'\n'.join(row['sources'])+'\n')
    return block if len(block) <= limit else ''

def leadership_readings():
    row = _data()
    return {row['id']: dict(title=row['title'], paragraphs=[*row['facts'], row['limitations']],
        links=row['sources'], checked_on=row['checked_on'], labels={u:'学院官网 · 学院领导' for u in row['sources']})}
