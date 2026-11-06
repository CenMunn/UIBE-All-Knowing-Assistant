"""Transient editor state and explicit leave decisions, never a user identity."""
from dataclasses import dataclass, field
from threading import RLock
from uuid import uuid4


@dataclass
class TimetableSession:
    session_id: str = field(default_factory=lambda:uuid4().hex)
    selected_ref: object = None
    view_week: int = 1
    selected_event_id: str | None = None
    view_token: str = field(default_factory=lambda:uuid4().hex)
    recognition_id: str | None = None
    recognition_claim: tuple | None = field(default=None,repr=False)
    busy: bool = False
    dirty: bool = False
    draft: dict | None = None
    pending_leave: str | None = None
    return_context: dict | None = None
    source_ids: tuple[str,...] = ()
    modal: str = ''
    create_token: str = field(default_factory=lambda:uuid4().hex)
    source_reminder: dict | None = None
    source_view_token: str = field(default_factory=lambda:uuid4().hex)
    _assets: object = field(default=None,repr=False)
    lock: RLock = field(default_factory=RLock,repr=False)

    def __deepcopy__(self,memo): return TimetableSession()

    def clear(self):
        if self._assets:
            self._assets.discard(self.source_ids,session_id=self.session_id)
            self._assets.revoke(self.session_id)
        self.recognition_id=None;self.recognition_claim=None;self.busy=False;self.draft=None;self.dirty=False
        self.view_token=uuid4().hex


def request_timetable_leave(session,target):
    session.pending_leave=target
    return dict(needs_confirmation=bool(session.dirty),navigate=None if session.dirty else target)


def resolve_timetable_leave(session,decision):
    if decision not in {'save','discard','stay'}:raise ValueError('离开操作无效。')
    target=session.pending_leave
    if decision=='stay':session.pending_leave=None;return dict(navigate=None,save_required=False)
    if decision=='save':return dict(navigate=None,save_required=True)
    session.clear();session.pending_leave=None
    return dict(navigate=target,save_required=False)
