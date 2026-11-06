"""提问建议只填草稿；替换前确认，不调用模型。"""
from campus_navigation import navigation_blocked

SHORTCUTS = (
    ('学习与考试', '我想了解课程选择和考试规则，应该先看哪些资料？'),
    ('宿舍与生活', '请介绍目前有依据的宿舍管理与生活服务信息。'),
    ('社团与活动', '有哪些社团和活动资料？请区分历史通知与当前可参加的活动。'),
    ('网络与平台', '校园网和课程学习平台应该从哪里进入？'),
)


def prepare_question(existing: str, suggested: str, confirmed: bool = False) -> dict:
    if not isinstance(existing, str) or not isinstance(suggested, str) or not suggested.strip():
        raise ValueError('问题内容无效。')
    confirm = bool(existing.strip() and existing != suggested and not confirmed)
    return {'text': existing if confirm else suggested, 'confirm': confirm}


def prepare_suggestion(app, existing, suggested='', pending=None, decision='stage'):
    """调用者持锁至界面更新构建结束；快照失效时绝不覆盖新草稿。"""
    if app is None:
        raise ValueError('页面正在初始化，请稍后再试。')
    with app.lock, app.chat.lock:
        if decision == 'cancel':
            return dict(text=existing, pending=None, apply=False)
        if navigation_blocked(app):
            raise ValueError('请先完成当前操作或取消编辑，再填入问题。')
        signature = [app.revision, app.chat.view_token, app.navigation.revision]
        if decision == 'confirm':
            if not isinstance(pending, dict) or pending.get('signature') != signature or pending.get('existing') != existing:
                raise ValueError('草稿或页面已改变，请重新选择问题。')
            suggested = pending['suggested']
        elif decision != 'stage':
            raise ValueError('未知草稿操作。')
        result = prepare_question(existing, suggested, decision == 'confirm')
        staged = dict(existing=existing, suggested=suggested, signature=signature) if result['confirm'] else None
        return dict(text=result['text'], pending=staged, apply=not result['confirm'])
