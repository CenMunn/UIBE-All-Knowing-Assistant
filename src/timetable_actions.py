"""Editor actions, separate from storage and long-running vision requests."""
from dataclasses import asdict,replace
from datetime import timedelta
from uuid import uuid4
from copy import deepcopy
from timetable_models import ScheduleSnapshot,ScheduleRef
from timetable_rules import parse_weeks,integer,project_week,calibrate_week,conflicts,parse_date
from timetable_session import request_timetable_leave,resolve_timetable_leave


def _number(value):
    if type(value) is int:return value
    if isinstance(value,str) and value.isascii() and value.isdecimal():return int(value)
    raise ValueError('请选择有效的数字。')


def _commit(controller,app,pending,candidate):
    t=app.timetable
    overlap=[p for p in conflicts(candidate) if p['kind']=='course-course']
    if overlap:
        labels={c.id:c.title for c in candidate.courses}
        for p in overlap:p['label']=labels[p['left']]+' / '+labels[p['right']]+'（重叠周次：'+','.join(map(str,p['weeks']))+'）'
        t.draft=dict(mode='conflicts',conflict_rows=overlap,previous=t.draft,pending_action=pending)
        t.modal='conflicts';t.dirty=True
        return controller.payload(app)
    return controller.handle(app,pending|dict(view_token=t.view_token,profile_revision=app.profile_revision))


def editor_action(controller,app,action):
    t=app.timetable;kind=action['kind'];service=controller.service
    values=action.get('fields',{})
    if not isinstance(values,dict) or len(values)>100 or any(not isinstance(k,str) or
            not isinstance(v,(str,bool,int)) or len(str(v))>2500 for k,v in values.items()):raise ValueError('编辑字段无效或过长。')
    if kind=='adopt-teacher':
        from teacher_matching import teacher_candidates
        draft=t.draft or {};index=action.get('row_index')
        if (draft.get('mode')!='import' or action.get('recognition_id')!=draft.get('recognition_id') or
            action.get('teacher_candidate_generation')!=draft.get('teacher_candidate_generation') or
            type(index) is not int or not 0<=index<len(draft.get('original_records',[]))):
            raise ValueError('识别结果已变化，请重新打开当前行的候选。')
        matches=teacher_candidates(draft['original_records'][index].get('teacher',''))['candidates']
        match=next((r for r in matches if r['teacher_id']==action.get('teacher_id')),None)
        if match is None:raise ValueError('该教师不在当前行候选内，请手动核对。')
        edits=deepcopy(action.get('rows') or draft.get('row_edits') or [{} for _ in draft['records']])
        allowed={'title','weekday','start_period','end_period','weeks','teacher','location','skip'}
        if (not isinstance(edits,list) or len(edits)!=len(draft['records']) or
            any(not isinstance(r,dict) or set(r)-allowed or any(not isinstance(v,(str,bool,int)) or len(str(v))>2500 for v in r.values()) for r in edits)):
            raise ValueError('校对字段无效，未采用候选。')
        edits[index]['teacher']=match['name'];draft['row_edits']=edits;draft.setdefault('fields',{}).update(values)
        t.dirty=True;t.view_token=uuid4().hex
        return controller.payload(app,'已采用教师候选；请继续核对课程，确认保存后才写入课表。')
    if t.draft is not None:
        if t.draft.get('mode')=='new' and values:
            previous={'name':'我的课表','term':'','total_weeks':16}|t.draft.get('fields',{})
            if kind=='new-submit' or any(str(v)!=str(previous.get(k,'')) for k,v in values.items()):t.dirty=True
        t.draft.setdefault('fields',{}).update(values)
    if t.draft is not None and action.get('rows'):
        rows=action['rows']
        if not isinstance(rows,list) or len(rows)>300:raise ValueError('校对行数量无效。')
        t.draft['row_edits']=rows
    f=t.draft.get('fields',{}) if t.draft else values
    def result(message=''):return controller.payload(app,message)
    def call(kind,**kw):return controller.handle(app,dict(kind=kind,view_token=t.view_token,profile_revision=app.profile_revision,**kw))
    def selected():
        if t.selected_ref is None:raise ValueError('请先打开课表。')
        return service.current(t.selected_ref)
    if kind=='stage':
        if t.draft is None:raise ValueError('请先打开编辑窗口。')
        if t.draft.get('mode')!='delete-schedule':t.dirty=True
        return result()|{'no_redraw':True}
    if kind=='free':
        if t.dirty:raise ValueError('请先保存或放弃当前编辑。')
        s=selected();day=integer(_number(action['day']),1,7);period=integer(_number(action['period']),1,len(s.clock.periods))
        t.draft={'mode':'free','free_day':day,'free_period':period};t.modal='free'
        return result()
    if kind in {'new-editor','calendar-editor','clock-editor','new-event','edit-event','edit-course'}:
        if t.dirty:raise ValueError('请先保存或放弃当前编辑。')
        s=selected() if kind!='new-editor' else None
        if kind=='new-editor':t.draft={'mode':'new','create_token':t.create_token};t.modal='new'
        elif kind=='calendar-editor':t.draft={'mode':'calendar'};t.modal='calendar'
        elif kind=='clock-editor':t.draft={'mode':'clock'};t.modal='clock'
        elif kind=='edit-course':
            c=next((c for c in s.courses if c.id==action['id']),None)
            if c is None:raise ValueError('课程已变化。')
            t.draft={'mode':'course','course':asdict(c)};t.modal='course'
        else:
            if kind=='edit-event':
                e=next((e for e in s.events if e.id==action['id']),None)
                if e is None:raise ValueError('事务已变化。')
                row=asdict(e)
            else:
                week=project_week(s,t.view_week)
                if not week['monday']:raise ValueError('请先校准该学期的教学周。')
                day=_number(action.get('day',service.now().isoweekday()));integer(day,1,7)
                p=next((p for p in s.clock.periods if p.number==_number(action.get('period',1))),None)
                row=dict(id=uuid4().hex,version=1,title='',date=(week['monday']+timedelta(days=day-1)).isoformat(),start=(p.start or '') if p else '',end=(p.end or '') if p else '',location='',notes='')
            t.draft={'mode':'event','event':row};t.modal='event'
    elif kind=='free-course':
        day=t.draft['free_day'];period=t.draft['free_period']
        t.draft={'mode':'course','course':dict(title='',weekday=day,start_period=period,end_period=period,weeks=(t.view_week,))};t.modal='course'
    elif kind=='free-event':
        s=selected();week=project_week(s,t.view_week)
        if not week['monday']:raise ValueError('请先关闭窗口并校准该学期的教学周，再安排具体日期的事务。')
        p=s.clock.periods[t.draft['free_period']-1]
        row=dict(id=uuid4().hex,version=1,title='',date=(week['monday']+timedelta(days=t.draft['free_day']-1)).isoformat(),start=p.start or '',end=p.end or '',location='',notes='')
        t.draft={'mode':'event','event':row};t.modal='event'
    elif kind=='course-submit':
        from timetable_service import courses_from_rows
        s=selected();draft=t.draft or {}
        if draft.get('mode')!='course':raise ValueError('请重新打开课程编辑。')
        old=draft['course'];row=old|{k:v for k,v in f.items() if k in {'title','code','section','teacher','location'}}
        for k in ('weekday','start_period','end_period'):row[k]=_number(f.get(k,old.get(k,1)))
        raw=f.get('weeks',old.get('weeks',()));row['weeks']=list(parse_weeks(raw,s.total_weeks)) if isinstance(raw,str) else list(raw)
        row.setdefault('id',uuid4().hex)
        rows=[asdict(c) for c in s.courses if c.id!=old.get('id')]
        if f.get('scope')=='week' and old.get('id'):
            if t.view_week not in old['weeks']:raise ValueError('该安排本周没有课程。')
            keep=[w for w in old['weeks'] if w!=t.view_week]
            if keep:rows.append(old|{'weeks':keep})
            row['id']=uuid4().hex;row['weeks']=[t.view_week]
        rows.append(row)
        courses,_=courses_from_rows(rows,allowed_sources=s.source_ids,total_weeks=s.total_weeks)
        return _commit(controller,app,dict(kind='save-courses',rows=rows),replace(s,courses=courses))
    elif kind=='course-delete-preview':t.modal='course-delete'
    elif kind=='course-delete-back':t.modal='course'
    elif kind=='course-delete-confirm':
        from timetable_service import courses_from_rows
        s=selected();old=t.draft['course'];rows=[]
        for c in s.courses:
            if c.id!=old['id']:rows.append(asdict(c))
            elif f.get('scope')=='week':
                keep=[w for w in c.weeks if w!=t.view_week]
                if keep:rows.append(asdict(c)|{'weeks':keep})
        courses,_=courses_from_rows(rows,allowed_sources=s.source_ids,total_weeks=s.total_weeks)
        return _commit(controller,app,dict(kind='save-courses',rows=rows),replace(s,courses=courses))
    elif kind=='event-submit':
        if not t.draft or t.draft.get('mode')!='event':raise ValueError('请先打开事务编辑。')
        row=t.draft['event']|{k:v for k,v in f.items() if k in {'title','date','start','end','location','notes'}}
        return call('save-event',event=row,acknowledged_sending=f.get('acknowledged_sending') is True)
    elif kind=='event-detail':
        if not any(e.id==action['id'] for e in selected().events):raise ValueError('事务不存在。')
        t.selected_event_id=action['id'];t.modal='event-detail';t.draft=None
    elif kind=='event-delete-preview':t.modal='event-delete';t.draft={'mode':'delete'}
    elif kind=='event-delete-confirm':
        e=next((e for e in selected().events if e.id==t.selected_event_id),None)
        if e is None:raise ValueError('事务已变化。')
        return call('delete-event',id=e.id,version=e.version,acknowledged_sending=f.get('acknowledged_sending') is True)
    elif kind=='preview-calendar':
        s=selected();week=_number(f.get('week',t.view_week));integer(week,1,s.total_weeks)
        t.draft={'mode':'calendar','week':week,'first_monday':calibrate_week(service.now(),week).isoformat(),'fields':{'week':str(week)}};t.modal='calendar'
    elif kind=='calendar-submit':
        if not t.draft or not t.draft.get('first_monday'):raise ValueError('请先预览日期范围。')
        week=_number(f.get('week',t.draft['week']))
        if week!=t.draft['week']:raise ValueError('周次已修改，请重新预览日期。')
        return call('calendar',week=week)
    elif kind=='clock-standard-preview':
        if not t.draft or t.draft.get('mode')!='clock':raise ValueError('请先打开作息设置。')
        from timetable_clock import standard_clock
        t.draft['standard_preview']=True
        for p in standard_clock().periods:
            f.update({f'start_{p.number}':p.start,f'end_{p.number}':p.end,f'group_{p.number}':p.group})
        t.draft['fields']=f;t.dirty=True
        return result('已预览贸大标准作息，确认保存后才会替换；旧事务与提醒不移动。')
    elif kind=='clock-submit':
        from timetable_clock import standard_clock
        base=standard_clock() if (t.draft or {}).get('standard_preview') else selected().clock
        periods=[dict(number=p.number,start=f.get(f'start_{p.number}',p.start),end=f.get(f'end_{p.number}',p.end),group=_number(f.get(f'group_{p.number}',p.group))) for p in base.periods]
        return call('clock',periods=periods)
    elif kind=='new-submit':
        if not t.draft or t.draft.get('mode')!='new':raise ValueError('请先打开新建课表窗口。')
        return call('commit-new',create_token=t.draft.get('create_token'),name=f.get('name',''),term=f.get('term',''),total_weeks=_number(f.get('total_weeks',16)))
    elif kind=='import-submit':
        from timetable_service import courses_from_rows
        draft=t.draft or {};edits=action.get('rows') or draft.get('row_edits',[])
        if draft.get('mode')!='import' or not isinstance(edits,list) or len(edits)!=len(draft['records']):raise ValueError('请先识别并校对图片，记录数量不得变化。')
        rows=[]
        for base,changes in zip(draft['records'],edits):
            if not isinstance(changes,dict) or set(changes)-{'title','weekday','start_period','end_period','weeks','teacher','location','skip'}:raise ValueError('校对字段无效。')
            row=base|changes;row.setdefault('id',uuid4().hex)
            if row.get('skip') is not True:
                for k in ('weekday','start_period','end_period'):row[k]=_number(row.get(k))
                row['weeks']=list(parse_weeks(row.get('weeks',''),_number(f.get('total_weeks',16))))
            rows.append(row)
        pending=dict(kind='save-import',rows=rows,name=f.get('name',''),term=f.get('term',''),total_weeks=_number(f.get('total_weeks',16)),replace=f.get('replace')=='replace',confirm_empty=f.get('confirm_empty') is True)
        courses,_=courses_from_rows(rows,allowed_sources=t.source_ids,total_weeks=pending['total_weeks'])
        candidate=replace(selected(),courses=courses) if pending['replace'] else ScheduleSnapshot(ScheduleRef(t.create_token,0),pending['name'],pending['term'],pending['total_weeks'],courses=courses)
        return _commit(controller,app,pending,candidate)
    elif kind=='conflicts-back':t.draft=t.draft['previous'];t.modal=t.draft['mode']
    elif kind=='conflicts-confirm':return call(**(t.draft['pending_action']|{'conflicts':action.get('conflicts',[])}))
    elif kind=='this-week':
        s=selected()
        if not s.first_monday or not s.active:raise ValueError('请先校准并设为当前学期。')
        return call('week',week=(service.now().date()-parse_date(s.first_monday)).days//7+1)
    elif kind=='delete-schedule-preview':
        if t.dirty:raise ValueError('请先保存或放弃当前编辑。')
        target=service.store.load(action['id'])
        t.draft={'mode':'delete-schedule','target_ref':target.ref,'name':target.name,'term':target.term,
                 'courses_count':len(target.courses),'events_count':len(target.events)}
        t.modal='delete-schedule';t.view_token=uuid4().hex
    elif kind=='delete-schedule-confirm':
        if not t.draft or t.draft.get('mode')!='delete-schedule':raise ValueError('请先预览要删除的课表。')
        ref=t.draft['target_ref']
        service.delete_schedule(ref,acknowledged_sending=f.get('acknowledged_sending') is True)
        if t.selected_ref and t.selected_ref.id==ref.id:
            t.clear();t.selected_ref=None;t.selected_event_id=None;t.source_ids=()
        if (t.return_context or {}).get('schedule_id')==ref.id:t.return_context=None
        if (t.source_reminder or {}).get('schedule_id')==ref.id:t.source_reminder=None;t.source_view_token=uuid4().hex
        t.draft=None;t.dirty=False;t.modal='catalog';t.view_token=uuid4().hex
        return result('课表已删除，其他课表不受影响。')
    elif kind=='catalog':
        if t.dirty:raise ValueError('请先保存或放弃当前编辑。')
        t.modal='catalog'
    elif kind=='close-editor':
        if t.dirty:request_timetable_leave(t,'timetable');t.modal='leave'
        else:t.modal='catalog' if (t.draft or {}).get('mode')=='delete-schedule' else '';t.draft=None
        t.view_token=uuid4().hex
    elif kind=='leave':
        if f:t.dirty=True
        request_timetable_leave(t,action['target']);t.modal='leave'
    elif kind=='leave-stay':resolve_timetable_leave(t,'stay');t.modal=(t.draft or {}).get('mode','')
    elif kind in {'leave-discard','leave-save'}:
        target=t.pending_leave
        if kind=='leave-save':
            submit={'course':'course-submit','event':'event-submit','clock':'clock-submit','import':'import-submit','new':'new-submit','calendar':'calendar-submit'}.get((t.draft or {}).get('mode'))
            if not submit:raise ValueError('请先完成草稿校对，或选择放弃。')
            saved=call(submit,fields=f,rows=action.get('rows',[]))
            if t.dirty:return saved
            t.pending_leave=None
        else:resolve_timetable_leave(t,'discard');t.source_ids=();t.modal=''
        return result()|{'navigate':target}
    elif kind=='event-reminder':
        if service.reminder_bridge is None:raise ValueError('提醒服务不可用，请检查邮箱配置。')
        service.reminder_bridge.open_source(app,action['id']);t.modal=''
        return result()|{'navigate':'reminders'}
    else:raise ValueError('未知课表编辑操作。')
    return result()
