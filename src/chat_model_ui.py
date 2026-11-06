"""模型选择与目录刷新；不拥有凭据，不自动发送问题。"""
from html import escape
import gradio as gr
from app_session import ensure_chat_mutation
from profile_ui import guarded_event
from response_delivery import delivery_event
from web_session import request_model,snapshot


def model_updates(frame,catalog):
    mid=frame['settings']['model'];s=catalog.snapshot()
    choices=[(m['name'],m['id']) for m in s['models']]
    missing=mid not in {v for _,v in choices}
    hint=''
    if missing:
        choices.append((('当前不可用：' if s['verified'] else '配置模型：')+mid,mid))
        if s['verified']:hint='当前会话的模型暂不可用，请重新选择；原对话保留。'
    if s['stale']:hint=(hint+' '+s['status']).strip()
    enabled=not (frame['busy'] or frame['pending'] or frame.get('edit_view',{}).get('editing') or frame.get('archive_error'))
    return dict(model=gr.update(choices=choices,value=mid,interactive=enabled and s['verified'] and bool(s['models'])),
                model_refresh=gr.update(interactive=enabled),model_hint='<span>'+escape(hint)+'</span>')


def mount_model_controls(catalog):
    from web_session import new_session
    updates=model_updates(snapshot(new_session(catalog.configured_model,{})),catalog)
    control=updates['model']
    selector=gr.Dropdown(control['choices'],value=control['value'],interactive=control['interactive'],
        label='回答模型',show_label=False,container=False,allow_custom_value=False,filterable=False,
        min_width=180,scale=0,elem_id='model-select')
    refresh=gr.Button('刷新模型',size='sm',scale=0,min_width=64,elem_id='model-refresh')
    return dict(selector=selector,refresh=refresh)


def bind_model_controls(controls,session,revision,outputs,to_outputs,catalog):
    def change_model(model_id,current):
        with current.lock:
            try:
                ensure_chat_mutation(current)
                message=request_model(current.chat,model_id,catalog)
            except ValueError as error:message=str(error)
            return to_outputs(snapshot(current.chat,message))

    def refresh_models(current):
        with current.lock:
            try:
                ensure_chat_mutation(current)
                if current.chat.edit_node or current.chat.pending_action:raise ValueError('请先完成或取消当前修改。')
            except ValueError as error:return to_outputs(snapshot(current.chat,str(error)))
        result=catalog.refresh()
        with current.lock:
            try:request_model(current.chat,current.chat.state['model'],catalog)
            except ValueError:pass
            frame=snapshot(current.chat,result['status']);frame['refresh_model_choices']=True
            return to_outputs(frame)

    delivery_event(controls['selector'].input,guarded_event(change_model),[controls['selector'],session,revision],outputs,
        queue=False,api_name=False,show_progress='hidden',reservation='model-controls',
        skip_when=lambda model_id,current,version:model_id==current.chat.state['model'],
        pending_response=lambda model_id,current,version:to_outputs(snapshot(current.chat,'当前结果正在交付，请稍后切换；原模型保持不变。')))
    delivery_event(controls['refresh'].click,guarded_event(refresh_models),[session,revision],outputs,
        queue=False,api_name=False,show_progress='hidden',reservation='model-controls',
        pending_response=lambda current,version:to_outputs(snapshot(current.chat,'当前结果正在交付，请稍后刷新模型。')))
