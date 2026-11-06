"""Explicit one-event reminder handoff; same local scheduler, independent source binding."""
from datetime import datetime,timedelta
from uuid import uuid4
from mail_service import Draft,validate_address
from reminder_service import preview_reminder,clear_reminder_preview,creation_admission
from timetable_rules import BEIJING,integer


class TaskReminderBridge:
    def __init__(self,service,reminders,coordinator):
        self.service,self.reminders,self.coordinator=service,reminders,coordinator

    def _event(self,app):
        source=app.timetable.source_reminder
        if not source:raise ValueError('请从课表中已保存的事务进入提醒。')
        schedule,event=self.service.store.event(source['event_id'])
        if schedule.ref.id!=source['schedule_id'] or event.version!=source['version']:
            raise ValueError('来源事务已修改，请返回课表重新设置。')
        return source,schedule,event

    def open_source(self,app,event_id):
        with self.coordinator:
            s=self.service.current(app.timetable.selected_ref)
            event=next((e for e in s.events if e.id==event_id),None)
            if event is None:raise ValueError('该事务不在当前课表中。')
            clear_reminder_preview(app.reminder)
            t=app.timetable
            t.return_context=dict(schedule_id=s.ref.id,week=t.view_week,event_id=event_id)
            t.source_reminder=dict(schedule_id=s.ref.id,event_id=event_id,version=event.version)
            t.source_view_token=uuid4().hex
            return dict(title=event.title,start=f'{event.date} {event.start}',location=event.location,
                        navigate='reminders')

    def preview(self,app,lead_minutes):
        integer(lead_minutes,1,525600)
        if self.reminders is None or not self.reminders.ready:raise ValueError('提醒服务不可用，请检查本机邮箱配置。')
        with self.coordinator:
            source,s,e=self._event(app)
            recipient=validate_address(app.profile.reminder_email)
            start=datetime.strptime(e.date+' '+e.start,'%Y-%m-%d %H:%M').replace(tzinfo=BEIJING)
            due=start-timedelta(minutes=lead_minutes)
            item=f'{e.title}｜开始：{e.date} {e.start}｜地点：{e.location or "未填写"}｜提前{lead_minutes}分钟'
            draft=preview_reminder(app.reminder,self.reminders.owner,item,due.strftime('%Y-%m-%d'),
                due.strftime('%H:%M'),self.reminders.now(),recipient=recipient)
            token='timetable:'+uuid4().hex
            app.reminder.token=token
            result=dict(token=token,event_id=e.id,event_version=e.version,schedule_id=s.ref.id,
                owner=self.reminders.owner,recipient=recipient,item=item,due_at=draft.due_at,
                start_at=int(start.timestamp()),event_title=e.title,location=e.location,
                lead_minutes=lead_minutes,status='reserved')
            source['preview']=result
            return result

    def confirm(self,app,token):
        if self.reminders is None or not self.reminders.ready:raise ValueError('提醒服务未启动。')
        with self.coordinator:
            source,s,e=self._event(app)
            data=source.get('preview')
            if not data or token!=data['token'] or app.reminder.token!=token:
                raise ValueError('提醒未预览或确认已变化。')
            if data['recipient']!=app.profile.reminder_email or data['event_version']!=e.version:
                raise ValueError('邮箱或事务已改变，请重新预览。')
            draft=app.reminder.pending
            if (draft is None or draft.recipient!=data['recipient'] or draft.item!=data['item']
                    or draft.due_at!=data['due_at'] or draft.owner!=data['owner']):
                raise ValueError('提醒内容已改变，请重新预览。')
            existing=self.reminders.store.get_by_token(self.reminders.owner,token)
            if existing:
                self.service.store.bind_task(token,existing['id'])
                return existing
            for intent in self.service.store.intents_for_event(e.id):
                if intent['status'] not in {'reserved','bound'}:continue
                old=self.reminders.store.get_by_token(intent['owner'],intent['token'])
                if old and old['status'] in {'cancelled','failed','expired'}:
                    self.service.store.update_intent(intent['token'],status='finished')
            self.service.store.reserve_intent(data)
            try:
                task=self.reminders.store.create(draft,token,int(self.reminders.now().timestamp()),
                    admission=lambda:creation_admission(self.reminders))
            except ValueError:
                if not self.reminders.store.get_by_token(self.reminders.owner,token):
                    self.service.store.update_intent(token,status='abandoned')
                raise
            self.service.store.bind_task(token,task['id'])
            return task

    def recover(self):
        if self.reminders is None:return
        with self.coordinator:
            for intent in self.service.store.unresolved_intents():
                task=self.reminders.store.get_by_token(intent['owner'],intent['token'])
                if task:self.service.store.bind_task(intent['token'],task['id'])
                else:self.service.store.update_intent(intent['token'],status='abandoned')

    def guard_mutation(self,event_id,version,*,acknowledged_sending):
        with self.coordinator:
            for intent in self.service.store.intents_for_event(event_id):
                if intent['status'] not in {'reserved','bound'}:continue
                if self.reminders is None:raise ValueError('无法核对已有提醒，请恢复提醒服务后再修改。')
                task=self.reminders.store.get_by_token(intent['owner'],intent['token'])
                if task is None:raise ValueError('提醒创建结果待核对，请重新启动服务核对后再修改。')
                if task['status']=='pending':raise ValueError('该事务有待执行提醒，请先在定时提醒中确认取消，再修改事务。')
                if task['status'] in {'sending','unknown','accepted'} and not acknowledged_sending:
                    raise ValueError('旧提醒可能正在发送或已经发送，无法撤回；确认知晓后才能修改事务。')
                self.service.store.update_intent(intent['token'],status='finished')

    def check_schedule_delete(self,snapshot,*,acknowledged_sending=False):
        tokens=[]
        for event in snapshot.events:
            for intent in self.service.store.intents_for_event(event.id):
                if intent['status'] not in {'reserved','bound'}:continue
                if self.reminders is None:raise ValueError('无法核对已有提醒，请恢复提醒服务后再删除课表。')
                task=self.reminders.store.get_by_token(intent['owner'],intent['token'])
                if task is None:raise ValueError('提醒创建结果待核对，请先重新启动服务核对记录。')
                status=task['status']
                if status=='pending':raise ValueError('该课表有待执行提醒，请先在定时提醒中确认取消，再删除课表。')
                if status not in {'sending','unknown','accepted','cancelled','failed','expired','missed'}:
                    raise ValueError('提醒状态无法核对，未删除课表。')
                if status in {'sending','unknown','accepted'} and not acknowledged_sending:
                    raise ValueError('旧提醒可能正在发送或已经发送，无法撤回；确认知晓后才能删除课表。')
                tokens.append(intent['token'])
        return tuple(tokens)

    def prepare_delivery(self,task,now):
        with self.coordinator:
            intent=self.service.store.get_intent(task.get('token',''))
            if intent is None or intent['status'] not in {'reserved','bound'}:return None
            try:
                schedule,event=self.service.store.event(intent['event_id'])
                if (event.version!=intent['event_version'] or schedule.ref.id!=intent['schedule_id']
                    or task['owner']!=intent['owner'] or task['due_at']!=intent['due_at']
                    or (task.get('recipient') or task['owner'])!=intent['recipient'] or task['item']!=intent['item']):return None
                start=datetime.fromtimestamp(intent['start_at'],BEIJING).strftime('%Y-%m-%d %H:%M')
                due=datetime.fromtimestamp(intent['due_at'],BEIJING).strftime('%Y-%m-%d %H:%M')
                return Draft(task['owner'],intent['recipient'],'[贸大百事通助理] 事务提醒：'+event.title,
                    f'事务：{event.title}\n开始时间：{start}（北京时间）\n地点：{event.location or "未填写"}\n'
                    f'本次提醒：提前{intent["lead_minutes"]}分钟\n计划提醒时间：{due}（北京时间）\n任务编号：{task["id"]}\n'
                    '这是你在本地网页确认创建的一次性事务提醒。')
            except (ValueError,KeyError):return None

    def to_ordinary(self,app):
        app.timetable.source_reminder=None;clear_reminder_preview(app.reminder)
        app.timetable.source_view_token=uuid4().hex
        return dict(navigate='reminders')

    def return_to_timetable(self,app):
        t=app.timetable;context=t.return_context
        clear_reminder_preview(app.reminder);t.source_reminder=None
        t.source_view_token=uuid4().hex
        if context:
            from timetable_store import ScheduleDeleted
            try:s=self.service.store.load(context['schedule_id'])
            except ScheduleDeleted:
                t.clear();t.selected_ref=None;t.selected_event_id=None;t.return_context=None;t.modal='catalog'
                return dict(navigate='timetable',message='课表已删除，请重新选择。')
            t.selected_ref=s.ref;t.view_week=context['week'];t.selected_event_id=context['event_id']
            t.modal='event-detail'
        t.return_context=None
        return dict(navigate='timetable')
