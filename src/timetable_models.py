"""Immutable personal timetable records, separate from shared campus knowledge."""
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ScheduleRef:
    id: str
    revision: int


@dataclass(frozen=True)
class Course:
    id: str
    title: str
    weekday: int
    start_period: int
    end_period: int
    weeks: tuple[int, ...]
    code: str = ''
    section: str = ''
    teacher: str = ''
    location: str = ''
    source_ids: tuple[str, ...] = ()
    source_text: str = ''


@dataclass(frozen=True)
class Period:
    number: int
    start: str | None
    end: str | None
    group: int


@dataclass(frozen=True)
class ClockPlan:
    periods: tuple[Period, ...] = ()
    confirmed: bool = False
    version: int = 1


def default_clock():
    from timetable_clock import standard_clock
    return standard_clock()


@dataclass(frozen=True)
class TimetableEvent:
    id: str
    version: int
    title: str
    date: str
    start: str
    end: str
    location: str = ''
    notes: str = ''


@dataclass(frozen=True)
class ScheduleSnapshot:
    ref: ScheduleRef
    name: str
    term: str
    total_weeks: int = 16
    active: bool = False
    first_monday: str | None = None
    clock: ClockPlan = field(default_factory=default_clock)
    courses: tuple[Course, ...] = ()
    events: tuple[TimetableEvent, ...] = ()
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecognitionDraft:
    name: str | None
    term: str | None
    records: tuple[dict, ...] = ()
    coverage: tuple[dict, ...] = ()
    issues: tuple[dict, ...] = ()


def snapshot_to_dict(value):
    return asdict(value)


def snapshot_from_dict(value):
    try:
        v = dict(value)
        v['ref'] = ScheduleRef(**v['ref'])
        clock = dict(v['clock'])
        clock['periods'] = tuple(Period(**p) for p in clock['periods'])
        v['clock'] = ClockPlan(**clock)
        courses = []
        for c in v.get('courses', ()):
            c = dict(c)
            c['weeks'] = tuple(c['weeks'])
            c['source_ids'] = tuple(c.get('source_ids', ()))
            courses.append(Course(**c))
        v['courses'] = tuple(courses)
        v['events'] = tuple(TimetableEvent(**e) for e in v.get('events', ()))
        v['source_ids'] = tuple(v.get('source_ids', ()))
        result = ScheduleSnapshot(**v)
        from timetable_rules import validate_schedule
        validate_schedule(result)
        return result
    except (TypeError, KeyError, AttributeError) as exc:
        raise ValueError('课表数据结构无效。') from exc
