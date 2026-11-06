"""Personal timetable business operations and request ownership checks."""
from dataclasses import replace
from datetime import timedelta
import json
from threading import RLock
from uuid import uuid4
from timetable_models import Course, ScheduleRef, ScheduleSnapshot, TimetableEvent
from timetable_rules import (integer, text, validate_schedule, validate_event, validate_clock,
    calibrate_week, conflicts, parse_date)
from timetable_session import TimetableSession
from timetable_vision import recognize_timetable


def courses_from_rows(rows,*,allowed_sources,total_weeks):
    if not isinstance(rows,list) or len(rows)>300:raise ValueError('课程条目数量无效。')
    courses,skipped=[],[]
    allowed={'id','title','code','section','teacher','location','weekday','start_period','end_period',
             'weeks','source_ids','source_text','skip'}
    for index,row in enumerate(rows):
        if not isinstance(row,dict) or set(row)-allowed:raise ValueError('课程字段无效。')
        if row.get('skip') is True:skipped.append(index);continue
        if not row.get('title') or any(row.get(k) is None for k in ('weekday','start_period','end_period','weeks')):
            raise ValueError(f'第{index+1}条缺少课程名、星期、节次或周次，请校对或明确跳过。')
        ids=row.get('source_ids',[])
        if not isinstance(ids,(list,tuple)) or any(k not in allowed_sources for k in ids):raise ValueError('图片来源不在当前课表中。')
        weeks=row['weeks']
        if not isinstance(weeks,(list,tuple)) or not weeks:raise ValueError('请确认每条课程的周次。')
        for week in weeks:integer(week,1,total_weeks)
        courses.append(Course(id=row.get('id') or uuid4().hex,title=row['title'],
            weekday=row['weekday'],start_period=row['start_period'],end_period=row['end_period'],
            weeks=tuple(sorted(set(weeks))),code=row.get('code') or '',section=row.get('section') or '',
            teacher=row.get('teacher') or '',location=row.get('location') or '',
            source_ids=tuple(ids),source_text=row.get('source_text') or ''))
    return tuple(courses),tuple(skipped)


class TimetableService:
    def __init__(self,store,assets,now,reminder_bridge=None,coordinator=None):
        self.store,self.assets,self.now=store,assets,now
        self.reminder_bridge=reminder_bridge
        self.coordinator=coordinator if coordinator is not None else RLock()

    def current(self,ref):
        saved=self.store.load(ref.id)
        if saved.ref!=ref:raise ValueError('课表已更新，请重新打开再操作。')
        return saved

    def create_empty(self,name,term,total_weeks,token):
        with self.coordinator:
            return self.store.create(ScheduleSnapshot(ScheduleRef(token,0),name,term,total_weeks),token)

    def _save(self,s,expected,ack=()):
        validate_schedule(s)
        problems=conflicts(s)
        if any(p['kind']!='course-course' for p in problems):
            raise ValueError('课程或事务时间重叠，请先处理已有事务，未覆盖原安排。')
        missing={p['id'] for p in problems}-set(ack)
        if missing:raise ValueError('课程安排有重叠，请逐项确认保留冲突：'+','.join(sorted(missing)))
        return self.store.save(s,expected)

    def save_courses(self,ref,courses,confirmed_conflicts=()):
        with self.coordinator:
            s=self.current(ref)
            return self._save(replace(s,courses=tuple(courses)),ref,confirmed_conflicts)

    def _guard_event(self,event,acknowledged_sending):
        if self.reminder_bridge:
            self.reminder_bridge.guard_mutation(event.id,event.version,acknowledged_sending=acknowledged_sending)
        elif any(i['status'] in {'reserved','bound'} for i in self.store.intents_for_event(event.id)):
            raise ValueError('事务有已绑定提醒，当前无法核对，请先恢复提醒服务。')

    def save_event(self,ref,event,*,acknowledged_sending=False):
        validate_event(event)
        with self.coordinator:
            s=self.current(ref)
            if not s.first_monday:raise ValueError('请先校准该学期的教学周。')
            if not s.clock.confirmed and s.courses:raise ValueError('作息未确认，不能保证这段时间空闲，请先核对作息。')
            day=parse_date(event.date)
            if not 0 <= (day-parse_date(s.first_monday)).days < s.total_weeks*7:
                raise ValueError('事务日期不在当前课表学期范围内。')
            old=next((e for e in s.events if e.id==event.id),None)
            if old:
                if old.version!=event.version:raise ValueError('事务已变化，请重新打开。')
                self._guard_event(old,acknowledged_sending)
                event=replace(event,version=old.version+1)
            events=tuple(e for e in s.events if e.id!=event.id)+(event,)
            ack=tuple(p['id'] for p in conflicts(s) if p['kind']=='course-course')
            return self._save(replace(s,events=events),ref,ack)

    def delete_event(self,ref,event_id,version,*,acknowledged_sending=False):
        with self.coordinator:
            s=self.current(ref)
            old=next((e for e in s.events if e.id==event_id and e.version==version),None)
            if old is None:raise ValueError('事务已变化，请重新打开。')
            self._guard_event(old,acknowledged_sending)
            return self.store.save(replace(s,events=tuple(e for e in s.events if e.id!=event_id)),ref)

    def delete_schedule(self,ref,*,acknowledged_sending=False):
        with self.coordinator:
            snapshot=self.current(ref)
            if self.reminder_bridge:
                tokens=self.reminder_bridge.check_schedule_delete(snapshot,acknowledged_sending=acknowledged_sending)
            else:
                if any(i['status'] in {'reserved','bound'} for e in snapshot.events for i in self.store.intents_for_event(e.id)):
                    raise ValueError('无法核对已有提醒，请恢复提醒服务后再删除课表。')
                tokens=()
            self.store.delete(ref,finished_tokens=tokens)
            self.assets.revoke_schedule(ref.id)

    def set_calendar(self,ref,week,now,*,activate):
        with self.coordinator:
            s=self.current(ref);integer(week,1,s.total_weeks)
            return self.store.save(replace(s,first_monday=calibrate_week(now,week).isoformat(),active=activate),ref)

    def set_clock(self,ref,clock):
        validate_clock(clock)
        with self.coordinator:
            s=self.current(ref)
            ack=tuple(p['id'] for p in conflicts(s) if p['kind']=='course-course')
            return self._save(replace(s,clock=replace(clock,version=s.clock.version+1)),ref,ack)


class TimetableController:
    def __init__(self,service,vision_settings,vision_factory):
        self.service,self.vision_settings,self.vision_factory=service,vision_settings,vision_factory
        self.renderer=None

    def payload(self,app,message=''):
        t=app.timetable
        from timetable_store import ScheduleDeleted
        try:s=self.service.store.load(t.selected_ref.id) if t.selected_ref else None
        except ScheduleDeleted:
            t.clear();t.selected_ref=None;t.selected_event_id=None;t.return_context=None;t.source_reminder=None
            t.source_ids=();t.modal='catalog';s=None;message='课表已删除，请重新选择。'
        ids=tuple(dict.fromkeys((s.source_ids if s else ())+t.source_ids))
        self.service.assets.authorize(ids,session_id=t.session_id,view_token=t.view_token,schedule_ref=s.ref if s else None)
        t._assets=self.service.assets
        t.catalog=self.service.store.list_schedules()
        t.previews=tuple(dict(id=k,url='/timetable-images/'+k+'?token='+self.service.assets.preview_ticket(
            k,session_id=t.session_id,view_token=t.view_token)) for k in ids)
        html=self.renderer(s,t,self.service.now()) if self.renderer else ''
        if self.renderer:
            from timetable_ui import render_modal
            modal=render_modal(s,t)
        else:modal=''
        return dict(html=html,message=message,modal=modal,dirty=t.dirty,busy=t.busy,
            view_token=t.view_token,profile_revision=app.profile_revision,delivery_id=uuid4().hex,navigate=None)

    def stage_upload(self,app,paths,upload_root):
        with app.lock:
            t=app.timetable
            if t.busy or t.dirty:raise ValueError('请先完成或放弃当前编辑。')
            staged=self.service.assets.stage(tuple(paths),upload_root=upload_root,session_id=t.session_id)
            self.service.assets.discard(t.source_ids,session_id=t.session_id)
            t.source_ids=tuple(x['id'] for x in staged);t._assets=self.service.assets
            t.draft={'mode':'upload','records':[]};t.dirty=True;t.modal='import'
            return self.payload(app,'图片已在本机预览，点击识别才发送到模型服务。')

    def begin_recognition(self,app,request_id):
        with app.lock:
            t=app.timetable
            if t.busy:raise ValueError('正在识别，请等待或取消。')
            if not t.source_ids:raise ValueError('请先上传课表图片。')
            raw=(t.draft or {}).get('fields',{}).get('total_weeks',16)
            total_weeks=int(raw) if isinstance(raw,str) and raw.isascii() and raw.isdecimal() else raw
            integer(total_weeks,1,30)
            t.busy=True;t.recognition_id=request_id
            t.recognition_claim=(app.profile_revision,t.session_id,t.source_ids,total_weeks)

    def recognize(self,app,request_id,*,claimed=False):
        if not claimed:self.begin_recognition(app,request_id)
        with app.lock:
            t=app.timetable
            if t.recognition_id!=request_id or not t.busy or not t.recognition_claim:return None
            profile,session,source_ids,total_weeks=t.recognition_claim
            if profile!=app.profile_revision or source_ids!=t.source_ids:return None
        def current():return app.timetable is t and t.recognition_id==request_id and app.profile_revision==profile
        try:
            images=tuple((k,self.service.assets.read(k,session_id=session)) for k in source_ids)
            draft=recognize_timetable(self.vision_factory,self.vision_settings,images,total_weeks=total_weeks)
            from timetable_recognition import order_recognition
            draft=order_recognition(draft)
            with app.lock:
                if not current():return None
                from copy import deepcopy
                from teacher_matching import teacher_candidates
                original=deepcopy([dict(r) for r in draft.records])
                t.draft={'mode':'import','records':[dict(r) for r in draft.records],
                    'original_records':original,'recognition_id':request_id,'teacher_candidate_generation':uuid4().hex,
                    'teacher_candidates':{str(i):teacher_candidates(r.get('teacher','')) for i,r in enumerate(original)},
                    'coverage':list(draft.coverage),'issues':list(draft.issues),
                    'name':draft.name or '我的课表','term':draft.term or '', 'fields':{'total_weeks':total_weeks}}
                t.dirty=True;t.modal='import'
                return draft
        except ValueError:
            with app.lock:
                if not current():return None
            raise
        finally:
            with app.lock:
                if current():t.busy=False;t.recognition_claim=None

    def cancel_recognition(self,app,request_id):
        with app.lock:
            t=app.timetable
            if t.recognition_id==request_id:
                t.recognition_id=None;t.recognition_claim=None;t.busy=False;t.view_token=uuid4().hex

    def handle(self,app,action):
        with app.lock:
            t=app.timetable
            if not isinstance(action,dict) or len(json.dumps(action,ensure_ascii=False))>200000:
                raise ValueError('课表操作无效。')
            if action.get('view_token')!=t.view_token or action.get('profile_revision')!=app.profile_revision:
                raise ValueError('课表页面已变化，请重试当前页面上的操作。')
            if t.busy:raise ValueError('请先等待识别结束或取消识别。')
            kind=action.get('kind')
            if kind=='open':
                if t.dirty:raise ValueError('请先保存或放弃编辑。')
                s=self.service.store.load(action['id']);t.selected_ref=s.ref;t.view_week=1;t.modal=''
            elif kind in {'new','commit-new'}:
                if kind=='commit-new':
                    if (not t.draft or t.draft.get('mode')!='new' or
                        action.get('create_token')!=t.create_token or t.draft.get('create_token')!=t.create_token):
                        raise ValueError('新建课表确认已变化，请重新打开窗口。')
                elif t.dirty:raise ValueError('请先保存或放弃编辑。')
                s=self.service.create_empty(action['name'],action['term'],action.get('total_weeks',16),t.create_token)
                t.selected_ref=s.ref;t.view_week=1;t.create_token=uuid4().hex;t.modal='';t.dirty=False;t.draft=None
            elif kind=='week':
                s=self.service.current(t.selected_ref);integer(action['week'],1,s.total_weeks);t.view_week=action['week']
            elif kind=='discard':
                t.clear();t.source_ids=();t.modal=''
            elif kind=='clock':
                from timetable_models import ClockPlan,Period
                clock=ClockPlan(tuple(Period(**p) for p in action['periods']),True,1)
                s=self.service.set_clock(t.selected_ref,clock);t.selected_ref=s.ref;t.dirty=False;t.draft=None;t.modal=''
            elif kind=='calendar':
                s=self.service.set_calendar(t.selected_ref,action['week'],self.service.now(),activate=True)
                t.selected_ref=s.ref;t.view_week=action['week'];t.dirty=False;t.draft=None;t.modal=''
            elif kind=='save-event':
                s=self.service.save_event(t.selected_ref,TimetableEvent(**action['event']),
                    acknowledged_sending=action.get('acknowledged_sending') is True)
                t.selected_ref=s.ref;t.selected_event_id=action['event']['id'];t.modal='event-detail';t.dirty=False;t.draft=None
            elif kind=='delete-event':
                s=self.service.delete_event(t.selected_ref,action['id'],action['version'],
                    acknowledged_sending=action.get('acknowledged_sending') is True)
                t.selected_ref=s.ref;t.selected_event_id=None;t.modal='';t.dirty=False;t.draft=None
            elif kind=='save-courses':
                s=self.service.current(t.selected_ref)
                courses,_=courses_from_rows(action['rows'],allowed_sources=s.source_ids,total_weeks=s.total_weeks)
                s=self.service.save_courses(s.ref,courses,action.get('conflicts',()))
                t.selected_ref=s.ref;t.modal='';t.dirty=False;t.draft=None
            elif kind=='save-import':
                courses,skipped=courses_from_rows(action['rows'],allowed_sources=t.source_ids,total_weeks=action.get('total_weeks',16))
                if not courses and action.get('confirm_empty') is not True:raise ValueError('没有可导入课程；如需空课表请明确确认。')
                if action.get('replace'):
                    s=self.service.current(t.selected_ref)
                    candidate=replace(s,courses=courses,source_ids=t.source_ids)
                    self.service.assets.commit(t.source_ids,s.ref)
                    with self.service.coordinator:s=self.service._save(candidate,s.ref,action.get('conflicts',()))
                else:
                    candidate=ScheduleSnapshot(ScheduleRef(t.create_token,0),action['name'],action['term'],
                        action.get('total_weeks',16),courses=courses,source_ids=t.source_ids)
                    validate_schedule(candidate)
                    missing={p['id'] for p in conflicts(candidate)}-set(action.get('conflicts',()))
                    if missing:raise ValueError('请先逐项确认课程冲突。')
                    self.service.assets.commit(t.source_ids,candidate.ref)
                    s=self.service.store.create(candidate,t.create_token)
                t.view_week=min(t.view_week,s.total_weeks) if action.get('replace') else 1
                t.selected_ref=s.ref;t.create_token=uuid4().hex;t.dirty=False;t.draft=None;t.modal=''
                t.source_ids=()
                t.view_token=uuid4().hex
                return self.payload(app,f'课表已保存；明确跳过{len(skipped)}条。')
            elif kind=='refresh':pass
            else:
                from timetable_actions import editor_action
                return editor_action(self,app,action)
            t.view_token=uuid4().hex
            return self.payload(app,'已更新。' if kind!='refresh' else '')
