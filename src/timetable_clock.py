"""UIBE class times confirmed by the user on 2026-10-21."""
TIMES=(('08:00','08:45'),('08:45','09:30'),('09:50','10:35'),('10:35','11:20'),
       ('11:25','12:10'),('13:30','14:15'),('14:15','15:00'),('15:20','16:05'),
       ('16:05','16:50'),('16:55','17:40'),('18:30','19:15'),('19:15','20:00'),('20:05','20:50'))
GROUPS=(1,1,2,2,2,3,3,4,4,4,5,5,5)


def standard_clock(*,version=1):
    from timetable_models import ClockPlan,Period
    return ClockPlan(tuple(Period(i,start,end,GROUPS[i-1]) for i,(start,end) in enumerate(TIMES,1)),True,version)


def course_time_label(clock,start_period,end_period):
    from timetable_rules import integer
    integer(start_period,1,len(clock.periods));integer(end_period,start_period,len(clock.periods))
    start,end=clock.periods[start_period-1].start,clock.periods[end_period-1].end
    return f'{start}—{end}' if start and end else '时间待核对'


def group_time_label(clock,group):
    periods=[p for p in clock.periods if p.group==group]
    if not periods:return ''
    if any(not p.start or not p.end for p in periods):return '时间待核对'
    text=f'{periods[0].start}—{periods[-1].end}'
    if len(periods)==3:text=f'{periods[0].start}—{periods[1].end} / {periods[2].end}'
    return text
