"""校园目录与独立导航状态；不执行模型、数据库或邮件操作。"""
from copy import deepcopy
from dataclasses import dataclass, replace, asdict

_DIRECTORY = (
    ('learning', '学习相关', (
        ('course-arrangements', '课程选择与安排', ('L01',)),
        ('credits', '学分制度及毕业要求', ('L02',)),
        ('textbooks', '教材获取途径', ('L03',)),
        ('exam-rules', '考试规则与成绩查询系统', ('L04',)),
        ('retakes', '补考或重修政策', ('L05',)))),
    ('living', '生活相关', (
        ('dormitory', '宿舍管理规定', ('V01',)),
        ('dining', '餐饮服务详情', ('V02',)),
        ('facilities', '健身娱乐设施使用方法', ('V03',)),
        ('safety', '校园安全措施及紧急情况处理流程', ('V04',)))),
    ('clubs', '社团活动', (
        ('recruitment', '学生组织招募信息', ('A01',)),
        ('volunteering', '志愿者活动机会', ('A02',)),
        ('events', '文体赛事信息', ('A03',)))),
    ('health', '健康与福利', (
        ('clinic', '校医院基本医疗服务', ('H01',)),
        ('counselling', '心理咨询支持', ('H02',)),
        ('insurance', '医疗保险政策', ('H03',)))),
    ('finance', '财务事项', (
        ('tuition', '学费缴纳方式', ('F01',)),
        ('scholarships', '助学金和奖学金申请流程', ('F02',)),
        ('budget', '个人预算规划建议', ('F03',)))),
    ('career', '职业发展', (
        ('internships', '实习就业指导', ('C01',)),
        ('career-talks', '职业规划讲座', ('C02',)),
        ('job-fairs', '校园招聘会信息', ('C03',)))),
    ('transport', '交通出行', (
        ('campus-transport', '校内公共交通系统', ('T01',)),
        ('bicycles', '自行车停放与租赁服务', ('T02',)),
        ('city', '城市探索建议', ('T03',)))),
    ('it', '信息技术支持', (
        ('network', '校园网络接入', ('I01',)),
        ('library', '图书馆电子资源使用', ('I02',)),
        ('platforms', '学习平台指南', ('I03',)))),
    ('international', '国际交流', (
        ('exchange', '国际学生交流项目', ('X01',)),
        ('study-abroad', '出国留学准备与申请指南', ('X02',)))),
)
TOOLS = {'chat':'与贸小通对话','materials':'资料卡片','timetable':'课表查询','teachers':'教师资料',
         'exams':'考试安排','reminders':'定时提醒','mail':'发送邮件','files':'文件管理'}


def categories() -> tuple[dict, ...]:
    return tuple({'id': key, 'label': label, 'children': tuple(
        {'id': cid, 'label': title, 'topics': topics} for cid, title, topics in children)}
        for key, label, children in _DIRECTORY)


def leaf(identifier: str) -> dict:
    for category in categories():
        for row in category['children']:
            if row['id'] == identifier:
                return dict(row, category=category['id'])
    raise ValueError('未知校园专题。')


@dataclass(frozen=True)
class NavigationState:
    category: str | None = None
    workspace: str = 'chat'
    revision: int = 0


def browse_category(state: NavigationState, category: str | None) -> NavigationState:
    if category is not None and category not in {r['id'] for r in categories()}:
        raise ValueError('未知校园类别。')
    return replace(state, category=category, revision=state.revision + 1)


def select_workspace(state: NavigationState, workspace: str, blocked: bool = False) -> NavigationState:
    if blocked:
        raise ValueError('请先等待当前操作结束，或完成／取消正在编辑和确认的内容。')
    if not isinstance(workspace, str):
        raise ValueError('未知功能。')
    category=state.category
    if workspace.startswith('topic:'):
        category=leaf(workspace[6:])['category']
    elif workspace.startswith('category:'):
        category=workspace[9:]
        browse_category(state,category)
    elif workspace=='materials':category=None
    elif workspace not in TOOLS:
        raise ValueError('未知功能。')
    return replace(state, category=category,workspace=workspace, revision=state.revision + 1)


def navigation_blocked(app) -> bool:
    from app_session import own_operation_count
    return bool(app.documents.dirty or app.timetable.dirty or app.timetable.busy
        or app.chat.busy or app.chat.edit_node or app.chat.pending_action
        or app.pending_edit or app.pending_profile or app.pending_conversation
        or app.active_operations > own_operation_count(app) or app.pending_deliveries or app.mail.busy
        or (app.mail.pending is not None and not app.mail.consumed))


def apply_navigation(app, action: str, target: str | None, expected_revision: int) -> dict:
    with app.lock, app.chat.lock:
        if expected_revision != app.navigation.revision:
            raise ValueError('导航已更新，请重新选择。')
        if action == 'browse':
            proposed = browse_category(app.navigation, target)
        elif action == 'open':
            proposed = select_workspace(app.navigation, target, navigation_blocked(app))
        else:
            raise ValueError('未知导航操作。')
        app.navigation = proposed
        return asdict(proposed)


def workspace_visibility(workspace: str) -> dict[str, bool]:
    select_workspace(NavigationState(), workspace)
    return {key: workspace == key for key in TOOLS} | {'materials':workspace=='materials' or workspace.startswith('category:'),
        'history': workspace == 'chat', 'topic': workspace.startswith('topic:')}
