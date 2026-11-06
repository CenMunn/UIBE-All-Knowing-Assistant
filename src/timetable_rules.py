"""Pure teaching-week, clock and occupancy rules (Beijing time)."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
import re
from uuid import uuid4

BEIJING = timezone(timedelta(hours=8))


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'数值须为{low}—{high}之间的整数。')
    return value


def text(value, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (required and not value.strip()):
        raise ValueError('课表字段为空、过长或含无效字符。')
    return value


def parse_weeks(value, total_weeks):
    integer(total_weeks, 1, 30)
    text(value, 200, required=True)
    s = value.strip().replace('（', '(').replace('）', ')')
    parity = None
    match = re.search(r'\((单|双)(?:周)?\)$', s)
    if match:
        parity = 1 if match[1] == '单' else 0
        s = s[:match.start()]
    s = s.replace('第', '').replace('周', '').replace(' ', '')
    s = re.sub('[—–~～至]', '-', s)
    if not re.fullmatch(r'\d+(?:-\d+)?(?:[、,，]\d+(?:-\d+)?)*', s):
        raise ValueError('请明确填写周次，例如1—16周、第11周、1—16周(单)。')
    weeks = set()
    for part in re.split('[、,，]', s):
        ends = part.split('-')
        start, end = int(ends[0]), int(ends[-1])
        integer(start, 1, total_weeks); integer(end, start, total_weeks)
        weeks.update(range(start, end + 1))
    result = tuple(w for w in sorted(weeks) if parity is None or w % 2 == parity)
    if not result: raise ValueError('没有匹配的教学周。')
    return result


def clock_minute(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('时间格式须为HH:MM。')
    h, m = map(int, value.split(':'))
    return h * 60 + m


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期格式须为YYYY-MM-DD。')
    try: return date.fromisoformat(value)
    except ValueError: raise ValueError('日期无效。') from None


def local_now(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('当前时间必须包含时区。')
    return now.astimezone(BEIJING)


def validate_clock(clock):
    if type(clock.confirmed) is not bool: raise ValueError('作息确认状态无效。')
    integer(clock.version, 1, 2**31-1)
    if not 1 <= len(clock.periods) <= 20: raise ValueError('每天支持1—20个小节。')
    previous, group = -1, 0
    for index, p in enumerate(clock.periods, 1):
        integer(p.number, index, index); integer(p.group, 1, 5)
        if p.group < group: raise ValueError('大节分组须按顺序排列。')
        group = p.group
        if p.start is None and p.end is None and not clock.confirmed: continue
        start, end = clock_minute(p.start), clock_minute(p.end)
        if start >= end or start < previous: raise ValueError('小节起止无效或与前一节重叠。')
        previous = end
    return clock


def validate_event(e):
    text(e.id, 80, required=True); integer(e.version, 1, 2**31-1)
    text(e.title, 120, required=True); text(e.location, 120); text(e.notes, 2000)
    parse_date(e.date)
    if clock_minute(e.start) >= clock_minute(e.end):
        raise ValueError('事务结束时间须晚于开始时间，首版不支持跨夜。')
    return e


def validate_schedule(s):
    text(s.ref.id, 80, required=True); integer(s.ref.revision, 0, 2**31-1)
    text(s.name, 80, required=True); text(s.term, 80, required=True)
    integer(s.total_weeks, 1, 30)
    if type(s.active) is not bool: raise ValueError('学期状态无效。')
    if s.first_monday is not None and parse_date(s.first_monday).weekday() != 0:
        raise ValueError('日历锚点必须为星期一。')
    validate_clock(s.clock)
    if len(s.courses) > 300 or len(s.events) > 1000: raise ValueError('课表记录过多。')
    ids = set()
    for c in s.courses:
        for key, limit in [('id', 80), ('title', 120), ('code', 40), ('section', 40),
                           ('teacher', 80), ('location', 120), ('source_text', 1000)]:
            text(getattr(c, key), limit, required=key in ('id', 'title'))
        integer(c.weekday, 1, 7)
        integer(c.start_period, 1, len(s.clock.periods)); integer(c.end_period, c.start_period, len(s.clock.periods))
        if not c.weeks or len(c.weeks) > s.total_weeks: raise ValueError('请确认课程适用周次。')
        for w in c.weeks: integer(w, 1, s.total_weeks)
        if tuple(sorted(set(c.weeks))) != c.weeks: raise ValueError('周次须按顺序且不重复。')
        if c.id in ids: raise ValueError('课程编号重复。')
        ids.add(c.id)
        for source in c.source_ids: text(source, 80, required=True)
    for e in s.events:
        validate_event(e)
        if e.id in ids: raise ValueError('事务编号重复。')
        ids.add(e.id)
    for source in s.source_ids: text(source, 80, required=True)
    return s


def calibrate_week(now, week):
    integer(week, 1, 30)
    today = local_now(now).date()
    return today - timedelta(days=today.weekday(), weeks=week-1)


def project_week(s, week):
    integer(week, 1, s.total_weeks)
    monday = parse_date(s.first_monday) + timedelta(weeks=week-1) if s.first_monday else None
    return dict(week=week, monday=monday, courses=tuple(c for c in s.courses if week in c.weeks),
        events=tuple(e for e in s.events if monday and monday <= parse_date(e.date) < monday + timedelta(days=7)))


def locate_now(s, view_week, now):
    now = local_now(now)
    empty = dict(week=None, weekday=None, period=None)
    if not s.active or not s.first_monday: return empty
    current = (now.date() - parse_date(s.first_monday)).days // 7 + 1
    if not 1 <= current <= s.total_weeks or current != view_week: return empty
    period = None
    if s.clock.confirmed:
        minute = now.hour * 60 + now.minute
        period = next((p.number for p in s.clock.periods
            if clock_minute(p.start) <= minute < clock_minute(p.end)), None)
    return dict(week=current, weekday=now.isoweekday(), period=period)


def split_course_week(c, week, replacement):
    if week not in c.weeks: raise ValueError('该课程本周没有安排。')
    keep = tuple(w for w in c.weeks if w != week)
    result = [replace(c, weeks=keep)] if keep else []
    if replacement is not None: result.append(replace(replacement, id=uuid4().hex, weeks=(week,)))
    return tuple(result)


def overlaps(a, b, x, y):
    return a < y and x < b


def conflicts(s):
    result = []
    for a, b in combinations(s.courses, 2):
        common = tuple(sorted(set(a.weeks).intersection(b.weeks)))
        if a.weekday == b.weekday and common and overlaps(a.start_period, a.end_period+1, b.start_period, b.end_period+1):
            result.append(dict(id=':'.join(sorted((a.id, b.id))), kind='course-course',
                               left=a.id, right=b.id, weeks=common))
    for a, b in combinations(s.events, 2):
        if a.date == b.date and overlaps(clock_minute(a.start), clock_minute(a.end), clock_minute(b.start), clock_minute(b.end)):
            result.append(dict(id=':'.join(sorted((a.id, b.id))), kind='event-event', left=a.id, right=b.id, weeks=()))
    if s.first_monday and s.clock.confirmed:
        for e in s.events:
            day = parse_date(e.date)
            week = (day - parse_date(s.first_monday)).days // 7 + 1
            for c in s.courses:
                if c.weekday != day.isoweekday() or week not in c.weeks: continue
                # Check each period, not the whole big block or intervening breaks.
                if any(overlaps(clock_minute(e.start), clock_minute(e.end), clock_minute(p.start), clock_minute(p.end))
                       for p in s.clock.periods if c.start_period <= p.number <= c.end_period):
                    result.append(dict(id=f'{c.id}:{e.id}', kind='course-event', left=c.id, right=e.id, weeks=(week,)))
    return result
