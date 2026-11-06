"""页面与模型请求共用的届别筛选；大几和学号不参与推断。"""
import re
from project_paths import TEXT_DIR
from evidence_view import BLOCK

MAJORS = ('数据科学与大数据技术', '信息管理与信息系统', '智能科学与技术', '人工智能')
PLAN_IDS = {('2024', MAJORS[0], '普通'): ('C2401', 'C2402', 'C2403', 'C2404'),
            ('2025', MAJORS[0], '普通'): ('C05', 'C06'),
            ('2025', MAJORS[0], '数字贸易实验班'): ('C05', 'C08'),
            ('2025', MAJORS[0], '中外合作'): ('C05', 'C09')}
for _major in MAJORS[1:]:
    PLAN_IDS[('2025', _major, '普通')] = ('C05', 'C07')


def catalog():
    text = '\n'.join((TEXT_DIR / f'curriculum_{year}.txt').read_text(encoding='utf-8')
                     for year in ('2024', '2025'))
    return {m[1]: (m[2], m[3].strip()) for m in BLOCK.finditer(text)}


def scope_fields(text, profile=False):
    # 排除明确否定的分句；不把“不是实验班”识别成实验班。
    text = re.sub(r'(?:并非|不是|不属于)[^，,。；;]*?(?=而是|而为|，|,|。|；|;|$)', '', text)
    years = set(re.findall(r'(?<!\d)(20\d{2})\s*(?:级|年入学|入学)', text))
    majors = [m for m in MAJORS if m in text and
              (profile or m not in ('人工智能', '智能科学与技术') or re.search(re.escape(m)+r'(?:普通)?专业', text))]
    if not majors:
        candidates = re.findall(r'([\u4e00-\u9fff]{2,20})专业', text)
        generic = ('我的', '这个', '该', '本', '普通', '其他', '所学', '对应', '本人的')
        if any(not any(c.endswith(word) for word in generic) for c in candidates):
            majors = ['未识别的指定专业']
    track = ('中外合作' if re.search(r'中外合作|乔治梅森', text) else
             '数字贸易实验班' if '数字贸易' in text else
             '其他实验班' if re.search(r'实验班|拔尖班', text) else
             '双学士项目' if re.search(r'双学士|双学位|复合培养|法学[—－–-]|金融学[—－–-]|经济学[—－–-]', text) else
             '普通' if re.search(r'普通专业|普通班', text) else '')
    return years, majors, track


def resolve_scope(question='', history=(), background=None):
    background = background or {}
    year = background.get('admission_year', '')
    _, known_majors, track = scope_fields(background.get('major', ''), profile=True)
    major = known_majors[0] if len(known_majors) == 1 else ''
    level = '研究生' if re.search(r'研究生|硕士|博士', background.get('year', '')) else '本科'
    initial = year, major, track, level
    contextual = [m['content'] for m in history if m.get('role') == 'user']
    for text in [*contextual, question]:
        if re.search(r'我自己|我的情况|按我的资料', text):
            year, major, track, level = initial
        if re.search(r'本科(?:生|方案|培养方案|专业)', text): level = '本科'
        elif re.search(r'研究生|硕士|博士', text): level = '研究生'
        years, majors, chosen_track = scope_fields(text)
        if len(years) > 1 or len(majors) > 1:
            if text == question:
                return '', '', '', level, True
            continue
        if years: year = next(iter(years))
        if majors:
            if major != majors[0]: track = ''
            major = majors[0]
        if chosen_track: track = chosen_track
    return year, major, track or '普通', level, False


def select_curriculum(question='', history=(), background=None):
    year, major, track, level, ambiguous = resolve_scope(question, history, background)
    if ambiguous:
        return (), '涉及多个届别或专业，请明确本次要查的入学年份、专业及培养路径；暂不混合方案。', ''
    # 卡片引用仅解析真实目录编号，不把浏览器提供的正文当资料。
    available = catalog()
    # 培养方案与公开课程大纲分别解析；C12等补充编号不能绕过方案的冲突校验。
    references = [key for key in re.findall(r'〔(C\d+)〕', question) if key in available]
    if references:
        if re.search(r'研究生|硕士|博士', question) and '本科' not in question:
            return (), '本科资料编号与问题明确指定的研究生层次冲突，请选择对应培养方案。', ''
        explicit_years, explicit_majors, explicit_track = scope_fields(question)
        for key in references:
            source_years, source_majors, source_track = scope_fields(available[key][0], profile=True)
            if key == 'C07':
                if explicit_majors and not set(explicit_majors).issubset(MAJORS[1:]):
                    return (), '资料编号与问题中的专业冲突，该条只收录信息管理、人工智能和智能科学普通专业。', ''
                source_majors = explicit_majors
            if ((explicit_years and source_years and explicit_years != source_years)
                    or (explicit_majors and source_majors and set(explicit_majors) != set(source_majors))
                    or (explicit_track and source_track and explicit_track != source_track)):
                return (), '资料编号与问题中的届别、专业或培养路径冲突，请删除旧编号或重新选择对应资料后提问。', ''
        keys = tuple(dict.fromkeys(references))
        return keys, '按所选资料解释，适用届别及培养路径见条目标题，不代表用户身份。', major
    if level == '研究生':
        return (), '未收录对应研究生培养方案，不能套用本科方案；请补充适用文件。', major
    if not year or not major:
        return (), '请在个人资料中填写入学年份和专业（或在问题中说明）；不从“大三”或学号推断。实验班、合作及双学士项目请在专业中注明。', major
    keys = PLAN_IDS.get((year, major, track or '普通'), ())
    label = f'{year}级 · {major} · {track or "普通"}'
    if not keys:
        return (), label + '：未收录已核对摘要，不使用其他届别或培养路径代替；可提供对应方案继续核对。', major
    return keys, label + '。培养方案不等于个人实际课表；特殊培养路径请在专业中注明。', major


def selected_sections(question='', history=(), background=None):
    keys, notice, major = select_curriculum(question, history, background)
    data = catalog()
    sections = {}
    for key in keys:
        title, body = data[key]
        if key == 'C07' and major in MAJORS[1:]:
            body = '\n'.join(p for p in body.splitlines() if p.startswith(major+'：') or p.startswith('上述三个'))
            title = major + '普通专业（2025级）'
        sections[key] = (title, body)
    return sections, notice


def curriculum_context(question, history, background):
    sections, notice = selected_sections(question, history, background)
    # 学习讲解、教材查询不需要整份培养方案；明确计划要求或资料编号时仍保留。
    plan_requested = re.search(r'学分|学时|课时|学期|培养|方案|必修|选修|毕业|学位|绩点|积点|〔C\d+〕', question)
    study_requested = re.search(r'教材|网站|练习|讲解|解释|教程|自学|学习|python|sql', question, re.I)
    curriculum_requested = re.search(r'专业|优势|课程|我自己|我的情况|按我的资料', question)
    if re.fullmatch(r'\s*(?:那|那么)?\s*20\d{2}级(?:呢)?[？?。]*\s*', question):
        curriculum_requested = True
    followup = r'^(?:还有|那还有|具体呢|继续|再详细)'
    if re.search(followup, question):
        for message in reversed((history or [])[-12:]):
            if message.get('role') != 'user':
                continue
            previous = message.get('content', '')
            if re.search(r'学分|毕业|学位|培养|专业|课程', previous):
                curriculum_requested = True
                break
            if not re.search(followup, previous):
                break
    standalone_policy = re.search(r'慕课|MOOC\d+|国家安全教育|劳动教育|学分替代|学分替换|课程替代|成绩替代', question, re.I)
    whole_plan_requested = re.search(r'毕业|学位|培养(?:方案|计划)|〔C\d+〕', question)
    campus_card = re.search(r'〔CK\d+〕', question) and not re.search(r'〔C\d+〕', question)
    # 背景用于选届别，不意味着每个医疗、奖助或独立慕课问题都须发送整份培养方案。
    # 明确毕业/方案问题仍保留完整资料，课程政策按本轮独立证据解释。
    substantive_plan = re.search(r'学分|学时|课时|培养|方案|必修|选修|毕业|学位|绩点|积点|〔C\d+〕', question)
    if ((study_requested and not substantive_plan)
            or not (plan_requested or curriculum_requested)
            or (standalone_policy and not whole_plan_requested) or campus_card):
        sections = {}
    text = '\n\n## 本轮培养方案选择\n' + notice
    text += '\n涉及个人毕业要求或课程计划时，只能使用本轮选中的方案；未选中时先澄清，不沿用历史回答或通用公开大纲的学分。通用学科讲解不受此限制。\n'
    for key, (title, body) in sections.items():
        text += f'\n[{key}] {title}\n{body}\n'
    # 与页面共用逐课解析结果，避免模型把长列表中相邻课程的数值串行。
    from course_catalog import course_sections
    focused = [row for row in course_sections(sections)
               if row['title'].split(' · ')[0] in question or
               re.search(r'(?<![A-Za-z0-9])' + row['course_code'] + r'(?![A-Za-z0-9])', question, re.I)]
    if focused:
        text += '\n## 本次指定课程逐项核对\n以下是上述方案的逐课解析，不是新制度；不要套用相邻课程数值。\n'
        for row in focused:
            text += f"{row['title']}：{row['paragraphs'][0]}。{row['note']}（来源：{row['source_id']}）\n"
    return text
