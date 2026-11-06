"""本次会话的自填资料，不代表身份认证。"""

from dataclasses import dataclass, asdict
import re
import unicodedata

GRADE_CHOICES = ('本科一年级', '本科二年级', '本科三年级', '本科四年级',
                 '硕士一年级', '硕士二年级', '硕士三年级')
SEMESTER_CHOICES = ('第一学期', '第二学期')
GRADE_ALIASES = dict(zip(('大一','大二','大三','大四','研一','研二','研三'), GRADE_CHOICES))


@dataclass(frozen=True)
class Profile:
    display_name: str = ''
    school: str = ''
    college: str = ''
    major: str = ''
    year: str = ''
    student_id: str = ''
    reminder_email: str = ''
    admission_year: str = ''
    semester: str = ''
    allow_personalization: bool = False


LIMITS = {'display_name': 40, 'school': 80, 'college': 80, 'major': 80, 'year': 20, 'student_id': 32, 'reminder_email': 254, 'admission_year': 4, 'semester': 10}
LABELS = dict(zip(LIMITS, ['姓名或称呼', '学校', '学院', '专业', '年级', '学号', '提醒收件邮箱', '入学年份', '当前学期']))
BACKGROUND_FIELDS = ('school', 'college', 'major', 'year', 'admission_year', 'semester')


def validate_profile(values: dict) -> Profile:
    if set(values) - set(asdict(Profile())):
        raise ValueError('含有不支持的资料字段。')
    consent = values.get('allow_personalization', False)
    if type(consent) is not bool:
        raise ValueError('个性化开关值无效。')
    cleaned = {}
    for key, limit in LIMITS.items():
        value = values.get(key, '')
        if key in ('year', 'semester', 'admission_year') and value is None:
            value = ''
        if not isinstance(value, str) or any(unicodedata.category(c).startswith('C') or c in '\n\r\u2028\u2029' for c in value):
            raise ValueError(f'{LABELS[key]}须为单行文字，不能含控制字符。')
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f'{LABELS[key]}请控制在{limit}字符以内。')
        if key == 'year':
            value = GRADE_ALIASES.get(value, value)
            # 旧请求可能只有研究生层次；保留其“非本科”语义，不猜具体年级。
            if value and value not in (*GRADE_CHOICES, '研究生', '硕士', '博士'):
                raise ValueError('请从年级选项中选择，或留空。')
        if key == 'semester' and value and value not in SEMESTER_CHOICES:
            raise ValueError('当前学期请选择第一学期、第二学期，或留空。')
        if key == 'admission_year' and value and not re.fullmatch(r'20[0-9]{2}', value):
            raise ValueError('入学年份请填写四位年份，例如2024；不要填写大三或2024级。')
        if key == 'student_id' and value:
            # 考试解析模块复用 assistant 的时区，延后导入避免启动时循环依赖。
            from exam_import import student_id
            value = student_id(value)
        if key == 'reminder_email' and value:
            from mail_service import validate_address
            value = validate_address(value)
        cleaned[key] = value
    return Profile(**cleaned, allow_personalization=consent)


def model_background(profile: Profile) -> dict[str, str]:
    if not profile.allow_personalization:
        return {}
    return {key: getattr(profile, key) for key in BACKGROUND_FIELDS if getattr(profile, key)}


def _stated_stage(text, year, semester):
    """仅采纳本人明确的当前阶段；他人、假设与下一学期描述不改本人背景。"""
    if re.search(r'如果|假如|假设|比如|例如|要是',text):
        return year,semester,False
    grade = r'本科[一二三四]年级|大[一二三四]|硕士[一二三]年级|研[一二三]|研究生|硕士|博士'
    half = r'第[一二12]学期|[上下]学期|[上下]'
    half_values = {'第一学期':'第一学期','第1学期':'第一学期','上学期':'第一学期','上':'第一学期',
                   '第二学期':'第二学期','第2学期':'第二学期','下学期':'第二学期','下':'第二学期'}
    changed = False
    for clause in re.split(r'[，,。；;！？!?\n]', text):
        if re.search(r'如果|假如|假设|比如|例如|要是|是不是|是否|吗', clause):
            continue
        match = re.search(r'^\s*我(?:现在|目前|本学期)?(?:已经|已)?(?:是|在|读)?\s*'
                          r'(?P<grade>'+grade+r')(?:的)?\s*(?P<half>'+half+r')?', clause)
        if match:
            selected = GRADE_ALIASES.get(match['grade'],match['grade'])
            if selected != year:
                semester = ''
            year = selected
            if match['half']:
                semester = half_values[match['half']]
            changed = True
        else:
            match = re.search(r'^\s*(?:我(?:现在|目前)(?:已经|已)?(?:是|在)?|我(?:已经是|是|在))\s*('
                              +half+r')', clause)
            if match:
                semester = half_values[match[1]]
                changed = True
    return year,semester,changed


def study_stage_context(background, question='', history=()):
    """明确的本人阶段优先于授权资料；不按日期、学号或入学年份推断。"""
    year = GRADE_ALIASES.get(background.get('year', ''), background.get('year', ''))
    semester = background.get('semester', '')
    initial = year,semester
    source = '个人资料'
    for text in [*(m.get('content','') for m in history if m.get('role')=='user'), question]:
        reset = re.search(r'按我的(?:个人)?资料|回到我(?:自己)?的情况',text)
        if (reset and not re.search(r'不要|不用|不必|无需|不想|不能|不是|别|如果|假如|假设',text[:reset.start()])):
            year,semester = initial
            source = '个人资料'
        year,semester,changed = _stated_stage(text,year,semester)
        if changed:
            source = '本轮及最近对话中的明确说明'
    if not background and not year and not semester:
        return ''
    prefix = ('\n\n## 个人学习阶段\n以下只按已授权资料或实际传入的本人明确说明推算，不是教务课表。'
              '用户本轮及最近对话明确指定不同年级、学期或其他对象时，以明确对象为准；'
              '不能将本人阶段套用给他人，也不把假设情况当成本人现状。\n')
    if not year:
        return prefix+'尚未选择当前年级；涉及本人下一学期选课时先询问，不从入学年份或日期推断。\n'
    if re.search(r'研究生|硕士|博士', year):
        return prefix+'当前为研究生层次；未收录对应研究生培养方案，不套用本科第1—8学期课程。硕士年级选项不代表统一学制。\n'
    if not semester:
        return prefix+'尚未选择当前学期；涉及本人下一学期选课时先询问第一学期还是第二学期，不默认大三上，不从日期推断。\n'
    number = GRADE_CHOICES.index(year) * 2 + SEMESTER_CHOICES.index(semester) + 1
    text = f'按{source}：{year} · {semester}，当前第{number}学期；'
    if number == 8:
        text += '不自动生成第9学期；先确认毕业、延长学习或其他实际安排。'
    else:
        text += f'下一学期为第{number+1}学期。'
    return prefix+text+'仅按匹配届别、专业和培养路径的方案提出建议；计划学期不等于实际开课、选课资格或已修完成情况。\n'


def require_reminder_email(profile: Profile) -> str:
    if not profile.reminder_email:
        raise ValueError('请先在左侧“个人资料”中填写提醒收件邮箱，再创建邮件提醒。')
    from mail_service import validate_address
    return validate_address(profile.reminder_email)
