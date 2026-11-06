"""专题页复用已核对资料；入口不是教务账号接口。"""
from datetime import datetime, date
from html import escape
import re
import json
from campus_navigation import leaf
from campus_knowledge import load_catalog, BEIJING, USABLE
from evidence_view import BLOCK, URL, safe_url
from project_paths import TEXT_DIR, PROJECT_DIR
from curriculum_cohorts import selected_sections
from course_catalog import course_sections, outline_sections

BASE_IDS = {
    'course-arrangements': ('C01', 'C02'),
    'credits': (),
    'textbooks': ('C01', 'C02'),
    'exam-rules': ('P04', 'P05', 'E03', 'E04'),
    'retakes': ('P01', 'P04', 'P05'),
    'network': ('S03', 'S04', 'S05', 'S06'),
}
TOOLS = {'course-arrangements': ['teachers'], 'exam-rules': ['exams', 'reminders'],
         'retakes': ['mail'], 'textbooks': ['teachers']}


def base_sections():
    text = (TEXT_DIR/'college_knowledge.txt').read_text(encoding='utf-8')
    text = text.replace('{{CURRICULUM_2025}}', (TEXT_DIR/'curriculum_2025.txt').read_text(encoding='utf-8'))
    return {m.group(1): (m.group(2), m.group(3).strip()) for m in BLOCK.finditer(text)}


def topic_page(leaf_id: str, today: date | None = None, profile=None) -> dict:
    item = leaf(leaf_id)
    catalog = load_catalog()
    today = today or datetime.now(BEIJING).date()
    sections, links = [], []
    for entry in catalog['entries']:
        if entry['status'] not in USABLE or not set(item['topics']).intersection(entry['topics']):
            continue
        note = entry['limitations']
        if entry['status'] == 'event':
            when = date.fromisoformat(entry['event_on'])
            note = ('历史预告，活动日期已过。' if today > when else
                    '活动当日，开放情况须确认。' if today == when else '预告，报名资格与开放情况须确认。') + note
        sources = [dict(title=s.get('provenance', '资料来源'), url=s['url']) for s in entry['sources']]
        sections.append(dict(id=entry['id'], title=entry['title'], paragraphs=entry['facts'],
            note=note, checked_on=entry['checked_on'], links=sources))
        links.extend(sources)
    base = base_sections()
    selected, notice = {}, ''
    if leaf_id in ('course-arrangements', 'credits'):
        background = {'major': profile.major, 'admission_year': profile.admission_year, 'year': profile.year} if profile else {}
        selected, notice = selected_sections(background=background)
        base.update(selected)
    for key in (*selected, *BASE_IDS.get(leaf_id, ())):
        if key not in base: continue
        title, body = base[key]
        sources = [dict(title='原始来源', url=u) for u in dict.fromkeys(URL.findall(body)) if safe_url(u)]
        sections.append(dict(id=key, title=title, paragraphs=body.split('\n'),
            note='', checked_on='', links=sources))
        links.extend(sources)
    gaps = [catalog['gaps'][t] for t in item['topics'] if t in catalog.get('gaps', {})]
    sections = topic_sections(leaf_id, sections)
    if leaf_id == 'course-arrangements':
        courses = course_sections(selected)
        sections.extend(courses)
        attached = {s.get('outline_id') for s in courses}
        sections.extend(s for s in outline_sections() if s['id'] not in attached)
    elif leaf_id == 'textbooks':
        for section in outline_sections():
            section['paragraphs'] = section.pop('textbook_summary')
            section['title'] = section['title'].replace('公开大纲参考', '大纲教材')
            sections.append(section)
    return dict(id=leaf_id, title=item['label'], sections=sections, official_links=links, curriculum_notice=notice,
        tools=TOOLS.get(leaf_id, []), question=f'请介绍{item["label"]}，说明适用对象、已知规则与需要确认的信息。',
        coverage='partial' if sections else 'missing', gaps=gaps)


def policy_question(app, action):
    """浏览器只传条目编号；标题与可用范围由服务器重新读取。"""
    if not isinstance(action, dict) or app.navigation.workspace != 'topic:' + str(action.get('leaf')):
        raise ValueError('专题已切换，请重新选择需要询问的政策。')
    page = topic_page(action['leaf'], profile=app.profile)
    section = next((s for s in page['sections'] if s['id'] == action.get('section')), None)
    if section is None:
        raise ValueError('这项资料不在当前专题中，请刷新后重试。')
    reference = section.get('source_id', section['id'])
    title = section.get('source_title', section['title'])
    extra = f'，并参考公开大纲〔{section["outline_id"]}〕' if section.get('outline_id') else ''
    focus = {
        'course-arrangements': '课程内容、必选修性质、计划学期与实际选课的区别',
        'credits': '学分结构、毕业与学位条件，不推断本人已经达标',
        'textbooks': '教材名称与版本、获取渠道，不保证库存或免费电子版',
        'exam-rules': '考试资格、考场纪律与成绩查询边界',
        'retakes': '补考、重修、缓考的区别和申请审核要求',
        'recruitment': '招募对象、加入条件和当前通知的适用性',
        'volunteering': '志愿项目资格、参与方式与尚需确认的学时规则',
        'events': '活动日期、参与对象以及观赛与参赛的区别',
        'platforms': '平台用途、入口区别与本人账号下的操作边界',
    }.get(page['id'], '与当前专题有关的规定和办理注意事项')
    return (f'在“{page["title"]}”板块，请结合“{title}”〔{reference}〕，'
            f'重点解释“{section["title"]}”{extra}。本板块关注：{focus}\n'
            '请围绕本板块解答，区分资料事实与一般建议；保留适用对象和限制，未明确的信息请指出。')


def topic_sections(leaf_id, sections):
    """核对后保存的AI辅助专题解读；原始摘要不随展示视角改写。"""
    views = json.loads((PROJECT_DIR / 'knowledge/topic_views.json').read_text(encoding='utf-8')).get(leaf_id, {})
    result = []
    for original in sections:
        key = original['id']
        if key in views and views[key] is None:
            continue
        section = dict(original)
        view = views.get(key)
        if key == 'C06':
            # 同一届别原件的课程段与毕业段分开呈现，数值仍来自同一已核对摘要。
            indices = (2, 3) if leaf_id == 'course-arrangements' else (0, 1, 4)
            view = [section['title'], *(section['paragraphs'][i] for i in indices)]
        if key == 'C07' and leaf_id == 'course-arrangements':
            view = [section['title'], '当前已核对摘要提供本专业毕业要求，但尚不足以列出完整课程计划。',
                    '不移用数据科学与大数据技术专业的课程；可提供本专业课程表或大纲继续补充。']
        if view:
            section['original'] = section['paragraphs']
            section['source_title'] = section['title']
            section['title'], section['paragraphs'] = view[0], view[1:]
            section['note'] = ' '.join(card_parts(original)[3])
        result.append(section)
    return result


CARD_SCOPE = {
    'C05': '仅适用2025级对应本科专业与培养路径，不自动适用于2024级或2026级；不得把本科方案套到研究生。',
    'C09': '两校学位分别须满足各自条件，不能承诺自动双学位；赴外、英语、论文与学费要求须核对本项目原文或主管部门。',
    'P04': '适用于本校本科课程考试；国家、社会考试另按要求。正文为2018年版本，随2025—2026学年附件提供，不代表2025年修订或已确认现行。个案处分及后续修订须向教务核实。',
    'P05': '随2025—2026学年资料提供，正文未见单独修订日期。这是缓考申请操作指引，不是独立的申请资格或成绩规定；未列截止时间、证明材料清单和考试安排。实际以当前教务系统及通知为准，助理不能代为提交或审批。',
}


def card_parts(section):
    body, provenance = [], []
    for paragraph in section['paragraphs']:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if re.match(r'^(?:来源|目录日期来源|原件存于|资料来源)', paragraph):
            provenance.append(paragraph)
        else:
            sentences = re.findall(r'[^。]+(?:。|$)', paragraph)
            file_notes = [s for s in sentences if s.startswith('原件') and re.search(r'(?:resources|texts)/', s)]
            provenance.extend(file_notes)
            cleaned = ''.join(s for s in sentences if s not in file_notes)
            if cleaned:
                body.append(cleaned)
    notes = [section['note']] if section['note'] else []
    if section['id'] in CARD_SCOPE:
        notes.append(CARD_SCOPE[section['id']])
    else:
        # 混合在来源段中的适用限制仍在卡面可见；只折叠文件位置等溯源信息。
        for text in provenance:
            for sentence in text.split('。'):
                if re.search(r'适用|不自动|不得|不能|未明确|未列|不等于|修订', sentence):
                    notes.append(sentence + '。')
    preview, more = body[:2], body[2:]
    if section['id'] not in CARD_SCOPE:
        for paragraph in more:
            if re.search(r'适用|不自动|不能套用|不能保证|口径不一致|未列|未明确', paragraph):
                notes.append(paragraph)
        more = [p for p in more if p not in notes]
    return preview, more, provenance, list(dict.fromkeys(notes))


def render_topic(page: dict) -> str:
    from form_catalog import load_form_catalog, forms_for_policy, template_display_name
    templates = load_form_catalog(PROJECT_DIR)
    parts = ['<article class="campus-topic"><header>',
        '<h2>' + escape(page['title']) + '</h2>',
        '<p>查阅相关规定，或选择一项向贸小通提问。</p></header>']
    if page.get('curriculum_notice'):
        parts.append('<aside class="topic-scope">' + escape(page['curriculum_notice']) + '</aside>')
    if page['coverage'] == 'missing':
        parts.append('<section class="topic-empty"><h3>资料待补充</h3><p>此专题尚未接入足够的已核对校方资料。不会根据示例补造学校规定。</p></section>')
    if page.get('gaps'):
        parts.append('<aside class="topic-scope">' + '<br>'.join(escape(t.removeprefix('资料缺口：')) for t in page['gaps']) + '</aside>')
    for section in page['sections']:
        preview, more, provenance, notes = card_parts(section)
        parts.append('<section class="topic-card"><div class="topic-card-body"><h3>' + escape(section['title']) + '</h3>')
        parts.extend('<p>' + escape(p) + '</p>' for p in preview)
        for note in notes:
            parts.append('<p class="topic-limitation">' + escape(note) + '</p>')
        if more:
            parts.append('<details class="topic-detail"><summary>展开详情</summary><div>')
            parts.extend('<p>' + escape(p) + '</p>' for p in more)
            parts.append('</div></details>')
        if section.get('original'):
            parts.append('<details class="topic-detail"><summary>原始资料摘要</summary><div>')
            parts.extend('<p>' + escape(p) + '</p>' for p in section['original'])
            parts.append('</div></details>')
        parts.append('</div><footer class="topic-card-footer"><details class="topic-evidence"><summary>查看依据</summary>')
        parts.extend('<p>' + escape(p) + '</p>' for p in provenance)
        if section['checked_on']:
            parts.append('<p>核对日期：' + escape(section['checked_on']) + '</p>')
        for source in section['links']:
            if safe_url(source['url']):
                parts.append('<p><a target="_blank" rel="noopener noreferrer" href="' +
                    escape(source['url'], quote=True) + '">' + escape(source['title']) + '</a></p>')
            else:
                parts.append('<p>' + escape(source['title']) + '</p>')
        if not section['links']: parts.append('<p>本地提供的资料摘要；适用范围以正文为准。</p>')
        parts.append('</details>')
        for template in forms_for_policy(templates, section.get('source_id', section['id'])):
            label = template_display_name(template)
            parts.append('<button type="button" class="topic-card-template" data-doc-action="preview" data-template="'+
                         escape(template['id'], quote=True)+'" title="'+escape(template['name'], quote=True)+
                         '">申请表模板示例 · '+escape(label)+'</button>')
        parts.append('<button type="button" class="topic-card-ask" data-leaf="' +
                     escape(page['id'], quote=True) + '" data-section="' + escape(section['id'], quote=True) +
                     '" aria-label="向贸小通提问：' + escape(section['title'], quote=True) + '">向贸小通提问</button></footer></section>')
    parts.append('</article>')
    return ''.join(parts)
