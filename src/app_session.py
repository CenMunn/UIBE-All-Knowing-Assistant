"""各工作区共用的会话资料与操作协调，不读写持久资料。"""
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from threading import RLock
from contextvars import ContextVar

from session_profile import Profile, validate_profile
from web_session import new_session
from mail_service import MailSession
from reminder_service import ReminderSession
from exam_session import ExamSession
from campus_navigation import NavigationState
from form_session import DocumentSession
from timetable_session import TimetableSession
_OPERATIONS = ContextVar('assistant_operations', default=())


@dataclass
class AppSession:
    chat: object
    navigation: NavigationState = field(default_factory=NavigationState)
    profile: Profile = field(default_factory=Profile)
    revision: int = 0
    profile_revision: int = 0
    mail: MailSession = field(default_factory=MailSession)
    reminder: ReminderSession = field(default_factory=ReminderSession)
    exam: ExamSession = field(default_factory=ExamSession)
    documents: DocumentSession = field(default_factory=DocumentSession)
    timetable: TimetableSession = field(default_factory=TimetableSession)
    pending_profile: Profile | None = None
    active_operations: int = 0
    pending_deliveries: set = field(default_factory=set, repr=False)
    delivery_reservations: set = field(default_factory=set, repr=False)
    entered: bool = False
    pending_conversation: dict | None = None
    pending_edit: dict | None = None
    conversation_catalog: dict = field(default_factory=dict, repr=False)
    lock: RLock = field(default_factory=RLock, repr=False)

    def __deepcopy__(self, memo):
        from conversation_service import bind_history
        other = new_app_session(self.chat.configured_model or self.chat.state['model'], self.chat.state['extra_body'])
        bind_history(other.chat, self.chat.archive_store, owner_key=None)
        return other


def new_app_session(model: str, extra_body: dict) -> AppSession:
    return AppSession(new_session(model, extra_body))


@contextmanager
def operation(app: AppSession, expected_revision: int | None = None):
    with app.lock:
        if expected_revision is not None and expected_revision != app.revision:
            raise ValueError('个人资料已改变，请重新操作。')
        if app.pending_profile is not None:
            raise ValueError('请先确认或取消个人资料修改。')
        if app.pending_conversation is not None:
            raise ValueError('请先确认或取消会话操作。')
        app.active_operations += 1
        revision = app.revision
    try:
        previous = _OPERATIONS.get()
        _OPERATIONS.set((*previous, id(app)))
        yield revision
    finally:
        _OPERATIONS.set(previous)
        with app.lock:
            app.active_operations -= 1


def own_operation_count(app):
    return _OPERATIONS.get().count(id(app))


def ensure_chat_mutation(app):
    if (app.pending_deliveries or app.active_operations > own_operation_count(app)
            or app.chat.busy or app.mail.busy):
        raise ValueError('当前操作或结果交付尚未结束，请稍后修改会话。')


def _ensure_idle(app):
    if app.timetable.dirty or app.timetable.busy:
        raise ValueError('课表尚在识别或编辑，请先保存、放弃或取消识别，再修改个人资料。')
    if app.documents.dirty:
        raise ValueError('表格尚未保存，请先保存或放弃填写，再修改个人资料。')
    if app.active_operations or app.pending_deliveries or app.delivery_reservations or app.chat.busy or app.mail.busy:
        raise ValueError('当前操作尚未结束，请等待后再切换会话或修改资料。')


def _apply_profile(app, proposed):
    reset_transient_state(app)
    app.profile = proposed
    from conversation_service import bind_history
    from conversation_ownership import owner_for_profile
    bind_history(app.chat, app.chat.archive_store, owner_key=owner_for_profile(proposed))
    app.revision += 1
    app.profile_revision += 1
    app.pending_profile = None


def reset_transient_state(app):
    # 调用方已持有顶层锁并确认没有正在执行的业务。
    if app.documents.edit_id and app.documents._service:
        from form_session import resolve_leave
        resolve_leave(app.documents._service, app.documents, 'discard')
    app.documents = DocumentSession()
    app.timetable.clear()
    app.timetable = TimetableSession()
    with app.chat.lock:
        from conversation_service import persist_chat, detach_history
        persist_chat(app.chat, strict=True)
        detach_history(app.chat)
        app.chat.messages.clear()
        app.chat.state['history'] = []
        app.chat.state.pop('learning_background', None)
        app.chat.last_record = None
        from web_session import reset_tree
        reset_tree(app.chat)
        app.chat.pending_settings = app.chat.pending_action = None
        app.chat.web_consent = False
        app.chat.pending_features = None
        app.chat.phase = ''
    app.pending_conversation = None
    app.conversation_catalog = {}
    app.pending_edit = None
    app.mail = MailSession()
    app.reminder = ReminderSession()
    app.exam = ExamSession()


def request_profile(app, proposed: Profile, dirty: bool) -> str:
    proposed = validate_profile(asdict(proposed))
    with app.lock:
        _ensure_idle(app)
        if proposed == app.profile:
            app.pending_profile = None
            return 'unchanged'
        if dirty or app.pending_edit:
            app.pending_profile = proposed
            return 'confirm'
        _apply_profile(app, proposed)
        return 'applied'


def resolve_profile(app, accepted: bool) -> bool:
    with app.lock:
        _ensure_idle(app)
        proposed = app.pending_profile
        if accepted and proposed is not None:
            _apply_profile(app, proposed)
            return True
        app.pending_profile = None
        return False
