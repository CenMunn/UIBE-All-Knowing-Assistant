"""导航展示桥接；浏览目录与切换工作区使用不同事件。"""
from dataclasses import asdict
from uuid import uuid4
import gradio as gr
from campus_navigation import NavigationState, categories, TOOLS, apply_navigation
from project_paths import RESOURCE_DIR


def navigation_payload(current=None, error=''):
    state = current.navigation if current is not None else NavigationState()
    return dict(asdict(state), categories=categories(), action=None, error=error, delivery_id=uuid4().hex)


def mount_navigation() -> dict:
    chat = gr.Button('与贸小通对话', variant='primary', elem_id='nav-chat')
    tools = {key: gr.Button(label, elem_id='nav-'+key) for key, label in TOOLS.items() if key != 'chat'}
    bridge = gr.HTML(navigation_payload(), html_template=
        '<p class="nav-error" role="status"></p>',
        js_on_load=(RESOURCE_DIR/'navigation-icons.js').read_text(encoding='utf-8')+'\n'+(RESOURCE_DIR/'campus-navigation.js').read_text(encoding='utf-8'),
        apply_default_css=False, elem_id='campus-navigation')
    return dict(chat=chat, bridge=bridge, tools=tools)


def browse_menu(value, current):
    if current is None:
        return navigation_payload(error='页面正在初始化，请稍后再试。')
    try:
        action = value.get('action') if isinstance(value, dict) else None
        if not isinstance(action, dict) or action.get('kind') != 'browse':
            raise ValueError('无效的目录操作。')
        apply_navigation(current, 'browse', action.get('target'), value.get('revision'))
        return navigation_payload(current)
    except ValueError as error:
        return navigation_payload(current, str(error))
