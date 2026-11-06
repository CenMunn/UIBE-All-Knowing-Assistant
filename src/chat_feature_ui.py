"""输入区功能开关及独立的首次联网授权。"""
import gradio as gr
from html import escape
from app_session import ensure_chat_mutation
from profile_ui import guarded_event
from response_delivery import delivery_event
from web_session import request_features, resolve_features, snapshot

def mount_feature_controls(capabilities):
    with gr.Row(elem_id='chat-features'):
        web=gr.Checkbox(False,label='联网补充',interactive=capabilities['web_available'],elem_id='web-search-setting',container=False)
        thinking=gr.Checkbox(False,label='深度思考',interactive=capabilities['thinking_available'],elem_id='deep-thinking-setting',container=False)
        reason='；'.join(capabilities.get(k,'') for k in ('web_reason','thinking_reason') if capabilities.get(k))
        hint=gr.HTML('<span>'+escape(reason)+'</span>',elem_id='feature-hint')
    with gr.Column(visible=False,elem_id='web-search-consent',min_width=0) as confirmation:
        gr.Markdown('**允许本次会话联网补充？**\n\n仅从公共主题词组织查询，交由 DeepSeek Search/Fetch 检索贸大公开官网；'
                    '不发送学校登录信息或整段原始提问。联网可能产生额外用量，开启不代表每轮必定搜索。')
        with gr.Row():
            accept=gr.Button('同意并开启',variant='primary',elem_id='web-search-accept')
            cancel=gr.Button('暂不开启',elem_id='web-search-cancel')
    return dict(web=web,thinking=thinking,confirmation=confirmation,accept=accept,cancel=cancel,hint=hint)

def bind_feature_controls(controls,session,revision,outputs,to_outputs,capabilities,*,model_catalog=None):
    def change_features(web,thinking,current):
        with current.lock:
            try:
                ensure_chat_mutation(current)
                caps=model_catalog.capabilities(current.chat.state['model']) if model_catalog is not None else capabilities
                if web and not caps['web_available']:raise ValueError(caps['web_reason'])
                if thinking and not caps['thinking_available']:raise ValueError(caps['thinking_reason'])
                result=request_features(current.chat,dict(web_enabled=web,deep_thinking=thinking))
                text='请确认联网授权，原对话和草稿保留。' if result=='confirm' else '功能设置已更新，原对话保留。'
            except ValueError as e:text=str(e)
            return to_outputs(snapshot(current.chat,text))
    def confirm_features(current,accepted):
        with current.lock:
            try:
                ensure_chat_mutation(current)
                resolve_features(current.chat,accepted)
                text='联网补充已开启。请发送问题。' if accepted else '未开启联网，草稿保留。'
            except ValueError as e:text=str(e)
            return to_outputs(snapshot(current.chat,text))
    for c in (controls['web'],controls['thinking']):
        delivery_event(c.input,guarded_event(change_features),[controls['web'],controls['thinking'],session,revision],outputs,
                       queue=False,api_name=False,show_progress='hidden')
    for name,accepted in (('accept',True),('cancel',False)):
        delivery_event(controls[name].click,guarded_event(lambda current,a=accepted:confirm_features(current,a)),
                       [session,revision],outputs,queue=False,api_name=False,show_progress='hidden')
