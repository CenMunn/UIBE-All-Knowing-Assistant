"""School-shaped weekly timetable and compact, explicit editing dialogs."""
from dataclasses import asdict
from datetime import timedelta
from html import escape as esc
import json
from uuid import uuid4
import gradio as gr
from project_paths import RESOURCE_DIR
from timetable_rules import locate_now,project_week,parse_date,clock_minute,overlaps
from timetable_clock import course_time_label,group_time_label,standard_clock

DAYS=('星期一','星期二','星期三','星期四','星期五','星期六','星期日')


def button(label,kind,**data):
    attrs=' '.join(f'data-{k.replace("_","-")}="{esc(str(v),quote=True)}"' for k,v in data.items())
    return f'<button type="button" class="tt-button" data-tt-action="{esc(kind)}" {attrs}>{esc(label)}</button>'


def field(label,key,value='',kind='text'):
    if kind=='textarea':control=f'<textarea data-tt-field="{key}" rows="3">{esc(str(value or ""))}</textarea>'
    else:control=f'<input type="{kind}" data-tt-field="{key}" value="{esc(str(value or ""),quote=True)}">'
    return f'<label class="tt-field"><span>{esc(label)}</span>{control}</label>'


def select(label,key,choices,value):
    options=''.join(f'<option value="{esc(str(v),quote=True)}"'+(' selected' if str(v)==str(value) else '')+f'>{esc(str(l))}</option>' for v,l in choices)
    return f'<label class="tt-field"><span>{esc(label)}</span><select data-tt-field="{key}">{options}</select></label>'


def dialog(title,body):
    return '<dialog class="tt-dialog"><header><h3>'+esc(title)+'</h3>'+button('关闭','close-editor')+'</header><div class="tt-dialog-body">'+body+'</div></dialog>'


def acknowledgement(label,checked=False):
    return '<label class="tt-checkbox-label"><input type="checkbox" data-tt-field="acknowledged_sending"'+(' checked' if checked is True else '')+'><span>'+esc(label)+'</span></label>'


def render_modal(s,t):
    draft=t.draft or {};mode=t.modal
    saved_fields=draft.get('fields',{})
    if not mode:return ''
    if mode=='leave':
        return dialog('离开前处理草稿','<p>当前内容尚未保存。</p><div class="tt-actions">'+button('保存后离开','leave-save')+button('放弃并离开','leave-discard')+button('继续编辑','leave-stay')+'</div>')
    if mode=='new':
        return dialog('新建课表',field('课表名称','name',saved_fields.get('name','我的课表'))+field('学期','term',saved_fields.get('term',''))+
            select('总教学周数','total_weeks',[(i,f'{i}周') for i in range(1,31)],saved_fields.get('total_weeks',16))+button('创建课表','new-submit'))
    if mode=='calendar':
        body='<p>按今天的北京时间校准，后续按自然周递进；节假日调课需手动调整。</p>'+select('今天是第几周','week',[(i,f'第{i}周') for i in range(1,s.total_weeks+1)],draft.get('week',t.view_week))
        if draft.get('first_monday'):
            body+='<p class="tt-note">第一周周一：'+esc(draft['first_monday'])+'。已安排事务和提醒不会随校准移动。</p>'+button('确认校准并设为当前学期','calendar-submit')
        else:body+=button('预览日期范围','preview-calendar')
        return dialog('校准当前教学周',body)
    if mode=='delete-schedule':
        body='<p>将删除 <b>'+esc(draft['name'])+'</b>（'+esc(draft['term'])+'），共'+str(draft['courses_count'])+'门课程、'+str(draft['events_count'])+'项事务。</p>'
        body+='<p>其他课表不受影响。有待执行提醒时，请先到定时提醒中确认取消。已发出的邮件无法撤回。</p>'
        body+=acknowledgement('我知晓旧提醒可能正在发送或已经发送，无法撤回',saved_fields.get('acknowledged_sending'))
        return dialog('确认删除课表',body+'<div class="tt-actions">'+button('返回','close-editor')+button('确认删除课表','delete-schedule-confirm')+'</div>')
    if mode=='clock':
        body='<p class="tt-note">贸大标准作息 · 用户确认2026-10-21。旧课表仅在确认保存后更新，事务和提醒不移动。</p>'+button('采用贸大标准作息','clock-standard-preview')+'<div class="tt-clock-list">'
        clock=standard_clock() if draft.get('standard_preview') else s.clock
        for p in clock.periods:
            body+=f'<div class="tt-clock-row" data-clock-row="{p.number}"><b>第{p.number}节</b>'+field('开始',f'start_{p.number}',saved_fields.get(f'start_{p.number}',p.start),'time')+field('结束',f'end_{p.number}',saved_fields.get(f'end_{p.number}',p.end),'time')+select('大节',f'group_{p.number}',[(g,f'第{g}大节') for g in range(1,6)],saved_fields.get(f'group_{p.number}',p.group))+'</div>'
        return dialog('学校作息设置',body+'</div>'+button('确认并保存作息','clock-submit'))
    if mode=='free':
        return dialog('安排这段空闲时间','<p>添加课程，或安排一件具体日期的事务。</p>'+button('添加课程','free-course')+button('安排事务','free-event'))
    if mode=='course':
        row=draft.get('course',{})|saved_fields
        body='<div class="tt-form-grid">'+field('课程名称','title',row.get('title'))+field('课程代码','code',row.get('code'))+field('课序号','section',row.get('section'))+field('教师','teacher',row.get('teacher'))+field('教室','location',row.get('location'))
        body+=select('星期','weekday',list(enumerate(DAYS,1)),row.get('weekday',1))
        body+=select('起始小节','start_period',[(p.number,f'第{p.number}节') for p in s.clock.periods],row.get('start_period',1))
        body+=select('结束小节','end_period',[(p.number,f'第{p.number}节') for p in s.clock.periods],row.get('end_period',1))
        weeks=row.get('weeks',(t.view_week,));weeks=','.join(map(str,weeks)) if not isinstance(weeks,str) else weeks
        body+=field('周次，例如1—16周(单)','weeks',weeks)
        if row.get('id'):body+=select('修改范围','scope',[('all','该安排全部适用周'),('week','仅当前周')],saved_fields.get('scope','all'))
        body+='</div>'+button('保存课程','course-submit')
        if row.get('id'):body+=button('预览删除','course-delete-preview')
        return dialog('编辑课程' if row.get('id') else '添加课程',body)
    if mode=='course-delete':
        return dialog('确认删除课程','<p>按刚才选择的范围删除，不影响其他课程。</p>'+button('确认删除','course-delete-confirm')+button('返回编辑','course-delete-back'))
    if mode=='event':
        row=draft.get('event',{})|saved_fields
        body='<div class="tt-form-grid">'+field('事务名称','title',row.get('title'))+field('日期','date',row.get('date'),'date')+field('开始时间','start',row.get('start'),'time')+field('结束时间','end',row.get('end'),'time')+field('地点','location',row.get('location'))+field('备注','notes',row.get('notes'),'textarea')+'</div>'
        body+='<details><summary>已有提醒的处理</summary><p>待执行提醒须先取消。已发出或发送中的邮件无法撤回。</p>'+acknowledgement('我知晓旧提醒可能已经发出，仍修改事务',saved_fields.get('acknowledged_sending'))+'</details>'
        return dialog('安排事务',body+button('保存事务','event-submit'))
    if mode=='event-detail':
        e=next((e for e in s.events if e.id==t.selected_event_id),None)
        if e is None:return ''
        body=f'<h4>{esc(e.title)}</h4><p>{esc(e.date)} {esc(e.start)}—{esc(e.end)}</p><p>{esc(e.location or "地点未填写")}</p><p>{esc(e.notes)}</p>'
        return dialog('事务详情',body+'<div class="tt-actions">'+button('事务提醒','event-reminder',id=e.id)+button('编辑事务','edit-event',id=e.id)+button('预览删除','event-delete-preview',id=e.id)+'</div>')
    if mode=='event-delete':
        return dialog('确认删除事务','<p>已绑定的待执行提醒须先取消，删除不会撤回已发邮件。</p>'+acknowledgement('我知晓旧提醒可能已经发出',saved_fields.get('acknowledged_sending'))+button('确认删除事务','event-delete-confirm'))
    if mode=='conflicts':
        body='<p>以下课程实际节次重叠。请核对后逐项确认是否保留，不会自动覆盖。</p>'
        for p in draft.get('conflict_rows',[]):
            body+=f'<label class="tt-check"><input type="checkbox" data-conflict="{esc(p["id"],quote=True)}">{esc(p.get("label",p["id"]))}</label>'
        return dialog('核对课程冲突',body+button('确认保留并保存','conflicts-confirm')+button('返回修改','conflicts-back'))
    if mode=='import':
        body='<div class="tt-import-layout"><div class="tt-image-side"><div class="tt-actions">'+button('缩小','zoom-out')+button('放大','zoom-in')+button('适应宽度','zoom-fit')+'</div><div class="tt-image-scroll">'
        for item in getattr(t,'previews',()):body+=f'<img class="tt-source-image" src="{esc(item["url"],quote=True)}" alt="上传课表图片">'
        body+='</div></div><div class="tt-import-fields">'
        if draft.get('mode')=='upload':
            body+='<h4>图片已准备好</h4><p>点击识别后，所选图片会发送到已配置的DeepSeek图片识别服务。识别不会自动保存或创建提醒。</p>'+select('本学期总教学周数','total_weeks',[(i,str(i)) for i in range(1,31)],saved_fields.get('total_weeks',16))+button('识别课表','recognize')
        else:
            body+='<p class="tt-warning">AI可能误读星期、教师或周次。请对照原图逐项核对，尤其注意合并表头、周六并列课程和教师姓名；确认后才会保存。</p>'
            body+=field('课表名称','name',saved_fields.get('name',draft.get('name','我的课表')))+field('学期','term',saved_fields.get('term',draft.get('term','')))+select('总周数','total_weeks',[(i,str(i)) for i in range(1,31)],saved_fields.get('total_weeks',16))
            if s:body+=select('导入方式','replace',[('new','新建另一份课表'),('replace','替换当前课表的课程')],saved_fields.get('replace','new'))
            for coverage in draft.get('coverage',[]):body+='<p class="tt-note">'+esc(coverage['description'])+'</p>'
            for issue in draft.get('issues',[]):body+='<p class="tt-warning">'+esc(issue['message'])+'</p>'
            for index,row in enumerate(draft.get('records',[])):
                edits=draft.get('row_edits',[])
                if index<len(edits):row=row|edits[index]
                body+=f'<fieldset class="tt-import-record" data-record-index="{index}"><legend>第{index+1}条</legend>'
                body+=field('课程名称','title',row.get('title'))+select('星期','weekday',[(0,'待确认')]+list(enumerate(DAYS,1)),row.get('weekday') or 0)
                body+=field('起始小节','start_period',row.get('start_period'),'number')+field('结束小节','end_period',row.get('end_period'),'number')
                weeks=row.get('weeks') or []
                body+=field('周次','weeks',weeks if isinstance(weeks,str) else ','.join(map(str,weeks)))+field('教师','teacher',row.get('teacher'))+field('教室','location',row.get('location'))
                original=draft.get('original_records',draft['records'])[index].get('teacher','')
                match=draft.get('teacher_candidates',{}).get(str(index),{})
                body+='<div class="tt-teacher-candidates"><small>原识别：'+esc(original or '未识别')+'</small>'
                if match.get('status')=='unavailable':body+='<p>教师目录暂不可用，可继续手动核对。</p>'
                for candidate in match.get('candidates',[]):
                    body+='<div class="tt-teacher-candidate"><strong>'+esc(candidate['name'])+'</strong><p>'+esc(' / '.join(candidate['affiliations']))+'</p><small>'+esc(candidate['reason'])+'</small>'
                    body+='<a target="_blank" rel="noopener noreferrer" href="'+esc(candidate['source_url'],quote=True)+'">官网资料</a>'
                    body+=f'<button type="button" class="tt-button" data-tt-action="adopt-teacher" data-row-index="{index}" data-teacher-id="{esc(candidate["teacher_id"])}" data-recognition-id="{esc(draft["recognition_id"])}" data-teacher-candidate-generation="{esc(draft["teacher_candidate_generation"])}">采用此教师</button></div>'
                if match.get('truncated'):body+='<p>同名候选较多，请在教师资料继续查找。</p>'
                body+='</div><label><input type="checkbox" data-tt-field="skip"'+(' checked' if row.get('skip') is True else '')+'>暂不导入此条</label></fieldset>'
            body+='<label><input type="checkbox" data-tt-field="confirm_empty">如全部跳过，确认保存空课表</label>'+button('确认保存课表','import-submit')
        return dialog('识别与校对课表',body+'</div></div>')
    return ''


def render_timetable(s,t,now):
    toolbar='<div class="tt-actions">'+button('上传课表','upload')+button('新建课表','new-editor')+button('本机课表','catalog')+'</div>'
    out=['<section class="tt-shell"><header class="tt-heading"><div><h2>课表查询</h2><p>课程与事务，按周安排。</p></div>'+toolbar+'</header>']
    if s is None or t.modal=='catalog':
        out.append('<div class="tt-catalog">')
        for row in getattr(t,'catalog',()):out.append('<div class="tt-catalog-item">'+button(row['name']+' · '+row['term'],'open',id=row['id'])+button('删除课表','delete-schedule-preview',id=row['id'])+'</div>')
        if s is None:out.append('<div class="tt-empty-state"><h3>把这一学期安排清楚</h3><p>上传课表图片，校对后生成可编辑课表。</p>'+button('上传课表','upload')+'</div>')
        out.append('</div>')
    if s:
        week=project_week(s,t.view_week);loc=locate_now(s,t.view_week,now)
        date_label=week['monday'].strftime('%m.%d')+' — '+(week['monday']+timedelta(days=6)).strftime('%m.%d') if week['monday'] else '尚未校准日期'
        out.append('<div class="tt-week-toolbar"><div><h3>'+esc(s.name)+'</h3><span>'+esc(s.term)+' / '+esc(date_label)+'</span></div><div class="tt-actions">'+button('上一周','week',week=max(1,t.view_week-1))+select('查看周次','view_week',[(i,f'第{i}周') for i in range(1,s.total_weeks+1)],t.view_week)+button('下一周','week',week=min(s.total_weeks,t.view_week+1))+button('回到本周','this-week')+'</div></div>')
        out.append('<div class="tt-subtoolbar">'+button('校准教学周','calendar-editor')+button('学校作息','clock-editor')+'<span class="tt-status">'+('作息已核对' if s.clock.confirmed else '作息待核对')+'</span></div>')
        rows=len(s.clock.periods)
        out.append(f'<div class="tt-grid-scroll"><div class="tt-grid" style="grid-template-rows:44px repeat({rows},48px)"><div class="tt-corner">节次 / 星期</div>')
        for day,label in enumerate(DAYS,1):out.append(f'<div class="tt-day-heading" data-weekday-heading="{day}" style="grid-column:{day+1};grid-row:1">{label}</div>')
        for group in range(1,6):
            periods=[p for p in s.clock.periods if p.group==group]
            if not periods:continue
            start,end=periods[0],periods[-1]
            times=('<br>'+esc(group_time_label(s.clock,group)))
            out.append(f'<div class="tt-group" data-period-group="{group}" style="grid-column:1;grid-row:{start.number+1}/{end.number+2}"><b>第{group}大节</b><span>第{start.number}—{end.number}节{times}</span></div>')
        for p in s.clock.periods:
            for day in range(1,8):
                occupied=any(c.weekday==day and c.start_period<=p.number<=c.end_period for c in week['courses'])
                style=f'grid-column:{day+1};grid-row:{p.number+1}'
                out.append(f'<div class="tt-grid-cell" style="{style}"></div>')
                if not occupied:out.append(f'<button class="tt-free" style="{style}" data-tt-action="free" data-day="{day}" data-period="{p.number}" aria-label="{DAYS[day-1]}第{p.number}节安排">＋</button>')
        lanes={}
        for day in range(1,8):
            cluster=[];end=0
            for c in sorted((c for c in week['courses'] if c.weekday==day),key=lambda c:(c.start_period,c.end_period,c.id)):
                if cluster and c.start_period>end:
                    for lane,item in enumerate(cluster):lanes[item.id]=(lane,len(cluster))
                    cluster=[];end=0
                cluster.append(c);end=max(end,c.end_period)
            for lane,item in enumerate(cluster):lanes[item.id]=(lane,len(cluster))
        for c in week['courses']:
            style=f'grid-column:{c.weekday+1};grid-row:{c.start_period+1}/{c.end_period+2}'
            lane,total=lanes[c.id]
            if total>1:style+=f';width:calc((100% - 8px)/{total});margin-left:calc(4px + (100% - 8px)*{lane}/{total})'
            out.append(f'<button class="tt-course" data-course-id="{esc(c.id,quote=True)}" data-tt-action="edit-course" data-id="{esc(c.id,quote=True)}" style="{style}"><strong>{esc(c.title)}</strong><span>{esc(c.location or "教室未提供")}</span><small>第{c.start_period}—{c.end_period}节 · {esc(course_time_label(s.clock,c.start_period,c.end_period))}</small></button>')
        if s.clock.confirmed:
            for e in week['events']:
                periods=[p.number for p in s.clock.periods if overlaps(clock_minute(e.start),clock_minute(e.end),clock_minute(p.start),clock_minute(p.end))]
                if periods:
                    style=f'grid-column:{parse_date(e.date).isoweekday()+1};grid-row:{min(periods)+1}/{max(periods)+2}'
                    out.append(f'<button class="tt-course tt-event" data-event-id="{esc(e.id,quote=True)}" data-tt-action="event-detail" data-id="{esc(e.id,quote=True)}" style="{style}"><strong>{esc(e.title)}</strong><small>{esc(e.start)}—{esc(e.end)}</small><span>{esc(e.location)}</span></button>')
        if loc['weekday']:
            out.append(f'<div class="tt-today" style="grid-column:{loc["weekday"]+1};grid-row:1/{rows+2}" aria-label="今天所在列"></div>')
            if loc['period']:out.append(f'<div class="tt-current" style="grid-column:{loc["weekday"]+1};grid-row:{loc["period"]+1}" aria-label="当前时段"><span>当前时段</span></div>')
        out.append('</div></div><div class="tt-event-heading"><h3>本周事务</h3>'+button('安排事务','new-event')+'</div><div class="tt-event-list">')
        for e in week['events']:out.append(button(e.date[5:]+' '+e.start+' '+e.title,'event-detail',id=e.id))
        if not week['events']:out.append('<p class="tt-note">本周还没有安排事务，点击课表空白位置即可添加。</p>')
        out.append('</div>')
    out.append('<details class="tt-help"><summary>课表与隐私说明</summary><p>本机共用列表，非认证账号空间。图片仅在点击识别后发送至模型服务；保存后不会自动创建邮件提醒。校准按自然周递进，调课需手动修改。</p></details></section>')
    return ''.join(out)


def mount_timetable_panel(controller,app_state,revision):
    initial=dict(html='<p>课表暂不可用，请检查本机存储后重启。</p>',delivery_id=uuid4().hex)
    bridge=gr.HTML(initial,html_template='<div class="tt-surface"></div><div class="tt-feedback" role="status" aria-live="polite"></div><div class="tt-overlay"></div>',
        js_on_load=(RESOURCE_DIR/'timetable.js').read_text(encoding='utf-8'),elem_id='timetable-view',apply_default_css=False)
    uploads=gr.File(label='上传本人课表图片（选择后先在本机预览）',file_count='multiple',type='filepath',file_types=['.png','.jpg','.jpeg'],elem_id='timetable-upload')
    cancel=gr.Button('取消本次识别',visible=False,elem_id='timetable-cancel')
    if controller:controller.renderer=render_timetable
    return dict(bridge=bridge,uploads=uploads,cancel=cancel,outputs=[bridge,uploads],
                reset_values=lambda:[initial,gr.update(value=None)])


def task_source_payload(bridge,app,message=''):
    from datetime import datetime
    from timetable_rules import BEIJING
    t=app.timetable
    if bridge is None or not (t.source_reminder or t.return_context):
        return dict(html='',source_view_token=t.source_view_token,profile_revision=app.profile_revision,delivery_id=uuid4().hex)
    if not t.source_reminder:
        body='<div class="tt-source-box"><p>已切换普通提醒，可继续编辑或返回原课表。</p><button data-source-action="return" type="button" class="tt-button">返回课表</button></div>'
    else:
        try:
            source,s,e=bridge._event(app)
            body='<div class="tt-source-box"><h3>事务提醒</h3><p><b>'+esc(e.title)+'</b></p><p>开始：'+esc(e.date+' '+e.start)+'（北京时间）</p><p>地点：'+esc(e.location or '未填写')+'</p>'
            lead=source.get('preview',{}).get('lead_minutes',30)
            lead_select=select('提前多久提醒','source_lead',[(30,'30分钟'),(15,'15分钟'),(60,'1小时'),(1440,'1天'),(0,'自定义分钟数')],lead if lead in {15,30,60,1440} else 0).replace('data-tt-field="source_lead"','data-source-lead')
            body+=lead_select+'<label class="tt-field">自定义提前分钟数<input type="number" min="1" max="525600" data-source-custom value="'+('' if lead in {15,30,60,1440} else str(lead))+'"></label><div class="tt-actions"><button class="tt-button" type="button" data-source-action="preview">预览事务提醒</button><button class="tt-button" type="button" data-source-action="ordinary">改为普通提醒</button><button class="tt-button" type="button" data-source-action="return">返回课表</button></div>'
            if source.get('preview'):
                p=source['preview'];due=datetime.fromtimestamp(p['due_at'],BEIJING).strftime('%Y-%m-%d %H:%M')
                body+='<div class="tt-warning"><p>收件邮箱：'+esc(p['recipient'])+'</p><p>计划提醒：'+esc(due)+'（北京时间），提前'+str(p['lead_minutes'])+'分钟</p><button class="tt-button" type="button" data-source-action="confirm">确认创建事务提醒</button></div>'
            body+='</div>'
        except ValueError as exc:
            body='<div class="tt-source-box"><p>'+esc(str(exc))+'</p><button class="tt-button" type="button" data-source-action="return">返回课表</button></div>'
    return dict(html=body,message=message,view_token=t.view_token,source_view_token=t.source_view_token,
        profile_revision=app.profile_revision,delivery_id=uuid4().hex)


def mount_task_source():
    return gr.HTML(dict(html='',delivery_id=uuid4().hex),html_template='<div class="tt-source-surface"></div><p class="tt-source-status" role="status"></p>',
        js_on_load=(RESOURCE_DIR/'timetable-reminders.js').read_text(encoding='utf-8'),elem_id='timetable-reminder-source',apply_default_css=False)


def bind_task_source(component,controller,app_state,revision,navigate,navigation_outputs):
    from app_session import operation
    from response_delivery import delivery_event
    index=navigation_outputs.index(component)
    def run(value,current,version):
        nav=[gr.skip()]*len(navigation_outputs)
        bridge=controller.service.reminder_bridge if controller else None
        action=value.get('action') or {}
        reply=dict(reply_for_source=action.get('source_view_token'),reply_to=action.get('request_id'))
        try:
            if bridge is None:raise ValueError('事务提醒服务不可用。')
            with operation(current,version),current.lock:
                if (action.get('view_token')!=current.timetable.view_token
                        or action.get('source_view_token')!=current.timetable.source_view_token
                        or action.get('profile_revision')!=current.profile_revision):
                    raise ValueError('来源事务页面已变化。')
                kind=action.get('kind');message='';target=None
                if kind=='preview':
                    lead=action.get('lead')
                    if type(lead) is not int:raise ValueError('提前量须为正整数分钟。')
                    bridge.preview(current,lead)
                elif kind=='confirm':
                    p=current.timetable.source_reminder.get('preview',{})
                    task=bridge.confirm(current,p.get('token',''))
                    message='提醒已创建，编号：'+task['id']+'。原课表可直接返回。'
                elif kind=='return':
                    returned=bridge.return_to_timetable(current)
                    target=returned['navigate'];message=returned.get('message','')
                elif kind=='ordinary':bridge.to_ordinary(current);target='reminders'
                elif kind=='invalidate':
                    if current.timetable.source_reminder:current.timetable.source_reminder.pop('preview',None)
                    from reminder_service import clear_reminder_preview
                    clear_reminder_preview(current.reminder)
                else:raise ValueError('未知事务提醒操作。')
                if target:nav=navigate(target,current)
                if message and target=='timetable':
                    timetable_index=next(i for i,c in enumerate(navigation_outputs) if c.elem_id=='timetable-view')
                    nav[timetable_index]=controller.payload(current,message)
                nav[index]=task_source_payload(bridge,current,message)|reply
                if kind=='invalidate':nav[index]['no_redraw']=True
                return nav
        except (ValueError,OSError) as exc:
            nav[index]=task_source_payload(bridge,current,str(exc) if isinstance(exc,ValueError) else '创建结果待核对，请先查看提醒记录，勿重复创建。')|reply
            return nav
    delivery_event(component.submit,run,[component,app_state,revision],navigation_outputs,api_name=False,show_progress='hidden',concurrency_limit=1)


def bind_timetable_events(parts,controller,app_state,revision,navigate,navigation_outputs,initialized):
    from pathlib import Path
    from app_session import operation
    from timetable_rules import locate_now
    count=len(navigation_outputs)
    bridge=parts['bridge'];cancel=parts['cancel'];uploads=parts['uploads']
    bridge_index=navigation_outputs.index(bridge)
    outputs=[uploads,cancel,*navigation_outputs]
    def pack(payload,current):
        target=payload.get('navigate')
        navigation=navigate(target,current) if target and target not in {'timetable','profile'} else [gr.skip()]*count
        if target=='timetable':navigation=navigate(target,current)
        navigation[bridge_index]=payload
        return [gr.skip(),gr.update(visible=payload.get('busy',False)),*navigation]
    def ready(current):
        if controller is None:return gr.skip()
        return controller.payload(current)
    initialized.then(ready,[app_state],bridge,queue=False,api_name=False,show_progress='hidden')
    def dispatch(value,current,version):
        if controller is None:raise gr.Error('课表服务未初始化，请检查运行说明。')
        action=value.get('action') if isinstance(value,dict) else None
        if not action:raise gr.Error('课表操作无效。')
        expected=action.get('view_token')
        if version!=current.revision:raise gr.Error('个人资料已改变，请重试。')
        try:
            if action.get('kind')=='recognize':
                if expected!=current.timetable.view_token:raise ValueError('课表页面已变化。')
                if current.timetable.draft is not None:
                    raw=action.get('fields',{}).get('total_weeks','16')
                    from timetable_actions import _number
                    from timetable_rules import integer
                    total=integer(_number(raw),1,30)
                    current.timetable.draft.setdefault('fields',{})['total_weeks']=total
                request_id=uuid4().hex
                controller.begin_recognition(current,request_id)
                payload=controller.payload(current,'正在识别图片，请稍候…')|{'busy':True,'reply_for':expected}
                try:
                    yield pack(payload,current)
                    draft=controller.recognize(current,request_id,claimed=True)
                    if draft is not None:yield pack(controller.payload(current,'识别完成，请逐项校对后保存。')|{'reply_for':expected},current)
                finally:
                    if current.timetable.busy:controller.cancel_recognition(current,request_id)
                return
            with operation(current,version):
                payload=controller.handle(current,action)|{'reply_for':expected}
                yield pack(payload,current)
        except (ValueError,OSError,KeyError,TypeError) as exc:
            message=str(exc) if isinstance(exc,ValueError) else '课表操作未完成，原记录保留，请刷新后核对。'
            yield pack(controller.payload(current,message)|{'reply_for':expected},current)
    bridge.submit(dispatch,[bridge,app_state,revision],outputs,api_name=False,show_progress='hidden',concurrency_limit=None)
    def clock(value,current,version):
        if controller is None:return gr.skip()
        action=value.get('action') or {}
        t=current.timetable
        if (action.get('kind')!='tick' or action.get('view_token')!=t.view_token or version!=current.revision):return gr.skip()
        from timetable_store import ScheduleDeleted
        try:s=controller.service.store.load(t.selected_ref.id) if t.selected_ref else None
        except ScheduleDeleted:
            previous=t.view_token
            return controller.payload(current)|{'reply_for':previous}
        return dict(no_redraw=True,clock=locate_now(s,t.view_week,controller.service.now()) if s else {},
            view_token=t.view_token,profile_revision=current.profile_revision,dirty=t.dirty,
            delivery_id=uuid4().hex,reply_for=t.view_token)
    bridge.input(clock,[bridge,app_state,revision],bridge,queue=False,api_name=False,show_progress='hidden')
    def upload(paths,current,version):
        try:
            if controller is None:raise ValueError('课表功能暂不可用。')
            with operation(current,version):return controller.stage_upload(current,tuple(Path(p) for p in (paths or [])),Path(uploads.GRADIO_CACHE))
        except (ValueError,OSError) as exc:
            if controller:return controller.payload(current,str(exc) if isinstance(exc,ValueError) else '图片读取失败，未修改课表。')
            return dict(message=str(exc),delivery_id=uuid4().hex)
    uploads.upload(upload,[uploads,app_state,revision],bridge,api_name=False,show_progress='hidden')
    def cancel_request(current,version):
        if controller is None:return gr.skip(),gr.update(visible=False)
        controller.cancel_recognition(current,current.timetable.recognition_id)
        return controller.payload(current,'本次识别已取消；模型服务可能仍在处理，未保存结果。'),gr.update(visible=False)
    cancel.click(cancel_request,[app_state,revision],[bridge,cancel],queue=False,api_name=False,show_progress='hidden')
