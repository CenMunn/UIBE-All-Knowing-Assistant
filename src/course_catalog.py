"""培养计划逐课展示与公开大纲补充；不合并近似课程名。"""
import json
import re
from project_paths import PROJECT_DIR


def outlines():
    return json.loads((PROJECT_DIR / 'knowledge/course_outlines.json').read_text(encoding='utf-8'))


def outline_text(course):
    return (f'[{course["id"]}] {course["name"]}公开教学大纲（{course["code"]}）\n'
            + '\n'.join(course[k] for k in ('overview', 'content', 'textbooks', 'limitations'))
            + '\n来源：' + course['url'] + '\n核对日期：' + outlines()['checked_on'] + '\n')


def outline_sections():
    data = outlines()
    return [dict(id=c['id'], title=c['name'] + ' · 公开大纲参考',
                 paragraphs=[c['overview'], c['content']], note=c['limitations'],
                 checked_on=data['checked_on'], links=[dict(title='官方教学大纲', url=c['url'])],
                 original=[c['textbooks']], source_id=c['id'],
                 textbook_summary=[c['textbooks'], '按课程名称和版本检索馆藏；未查询实时库存，也不保证提供免费电子版。'])
            for c in data['courses']]


def course_sections(selected):
    """只解析已经核对的2024摘要；不从邻届、专业介绍或课名猜测计划。"""
    result = []
    catalog = outlines()['courses']
    for source in ('C2402', 'C2403'):
        if source not in selected:
            continue
        source_title, text = selected[source]
        groups = (
            ('学科基础必修', '学科基础必修'), ('专业方向必修', '专业方向必修'),
            ('专业入门数学要求', '专业入门'), ('学科基础选修', '学科基础选修'),
            ('其余选修', '学科基础选修'), ('BDT305', '学科基础选修'))
        for paragraph in text.splitlines():
            group = next((label for prefix, label in groups if paragraph.startswith(prefix)), None)
            if group is None:
                continue
            matches = list(re.finditer(r'([A-Z]{3,4}\d{3})([^；：。]+?)(\d+)学分(?:、(\d+)学时)?/第(\d)学期', paragraph))
            for match in matches:
                code, name, credits, hours, semester = match.groups()
                result.append(_course(source, source_title, code, name.strip(), credits, hours, semester, group, catalog))
        if source == 'C2403':
            # 原摘要的这一行采用不同句式，保留逐字可核对的计划数值。
            match = re.search(r'(AIT304)《([^》]+)》.*?(\d+)学时、(\d+)学分、第(\d)学期', text)
            if match:
                code, name, hours, credits, semester = match.groups()
                result.append(_course(source, source_title, code, name, credits, hours, semester, '学科基础选修', catalog))
    return sorted(result, key=lambda row: (int(row['semester']), row['course_code']))


def _course(source, source_title, code, name, credits, hours, semester, group, catalog):
    public = next((c for c in catalog if c['code'] == code and c['name'] == name), None)
    paragraph = f'{group} · {credits}学分 · 计划第{semester}学期' + (f' · {hours}学时' if hours else '')
    note = '2024级普通专业教学计划，不是个人实际课表；选修课程不保证当期开放。'
    if code == 'BDT305':
        note += '原件教学计划印刷491页为第5学期，实验课程列表印刷490页为第6学期，需向学院核实；冲突不能解释为两个学期都开课。'
    if code == 'BDT308':
        note += '原件实验列表代码写BDT3087，教学计划与公开大纲为BDT308，差异须核实。'
    if code == 'CMP143':
        note += '实验列表用“信息检索与问答系统”，教学计划用“信息检索和问答系统”。'
    if public:
        note += public['limitations']
    else:
        note += '尚未收录可匹配的公开大纲，不补造教师、教材或考核比例。'
    return dict(id=source + '-' + code, source_id=source, source_title=source_title,
                course_code=code, semester=semester, title=name + ' · ' + code,
                paragraphs=[paragraph, public['content'] if public else '可向贸小通询问该课程的通用知识与学习方法；学校具体教学安排以正式资料为准。'],
                note=note, checked_on='2026-10-17',
                links=[dict(title='官方教学大纲', url=public['url'])] if public else [],
                original=['来源：2024级原件印刷488、490—491页；resources/curricula/2024-information-college-plan.pdf',
                          public['textbooks'] if public else '学分和学期取自已核对培养方案。'],
                outline_id=public['id'] if public else '')


def relevant_outlines(question, history=(), limit=2200):
    data = outlines()['courses']
    def matches(text):
        return [c for c in data if c['name'] in text or re.search(r'(?<![A-Za-z0-9])' + c['code'] + r'(?![A-Za-z0-9])', text, re.I)
                or f'〔{c["id"]}〕' in text
                or any(alias in text for alias in c.get('topic_aliases', []))]
    selected = matches(question)
    if not selected and re.match(r'^(那|这门|它|继续|教材呢|考核呢)', question.strip()):
        # 只承接最近一条用户问题；话题转换后不向更旧历史寻找课程。
        recent = next((m['content'] for m in reversed(history) if m.get('role') == 'user'), '')
        selected = matches(recent)
    if not selected:
        return ''
    result = '\n\n## 本轮公开课程大纲\n公开大纲用于内容与教材参考，不覆盖已选培养方案的学分、必选修或学期；不代表当前班级安排。\n'
    if len(result) > limit:
        if any(f'〔{c["id"]}〕' in question for c in selected):
            raise ValueError('所选课程大纲超过应用字符预算，请增大预算或减少同时询问的资料。')
        return ''
    for course in selected:
        block = outline_text(course)
        if len(result) + len(block) > limit:
            if f'〔{course["id"]}〕' in question:
                raise ValueError('所选课程大纲超过应用字符预算，请增大预算或减少同时询问的资料。')
            continue
        result += block
    return result if any(f'[{c["id"]}]' in result for c in selected) else ''
