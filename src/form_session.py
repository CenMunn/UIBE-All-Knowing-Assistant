"""填写草稿、单写者租约与保存后操作；不创建个人副本。"""
from copy import deepcopy
from dataclasses import dataclass,field
from threading import RLock
from uuid import uuid4
from form_catalog import get_form
from form_export import validate_fields
from document_validation import validate_photo
from form_layout import validate_layout, default_layout


@dataclass
class DocumentSession:
    ref: object=None
    file_name: str='个人副本'
    form: dict | None=None
    values: dict=field(default_factory=dict)
    layout: dict=field(default_factory=default_layout)
    layout_upgrade: bool=False
    photo: bytes | None=field(default=None,repr=False)
    dirty: bool=False
    edit_id: str | None=None
    operation_id: str=field(default_factory=lambda:uuid4().hex)
    selected_template: str | None=None
    create_nonce: str=field(default_factory=lambda:uuid4().hex)
    mail_refs: tuple=()
    pending_mail_refs: tuple=()
    pending_target: str | None=None
    query: str=''
    pending_delete: tuple=()
    pending_rename: object=None
    file_catalog: dict=field(default_factory=dict)
    draft_present: bool=False
    _service: object=field(default=None,repr=False)
    lock: RLock=field(default_factory=RLock,repr=False)

    def __deepcopy__(self,memo):return DocumentSession()


def begin_form(service,session,ref):
    with session.lock:
        if session.dirty:raise ValueError('请先保存或放弃当前填写内容。')
        record=service.store.get(ref)
        form=get_form(service.catalog,record.template_id,require_fillable=True)
        if record.template_version!=form['template_version']:raise ValueError('模板版本改变，请先核对已有副本。')
        if session.edit_id and session._service:session._service.store.release_lease(session.edit_id)
        token=service.store.acquire_lease((ref,),'form')
        data=service.store.form_data(ref)
        saved=dict(data['fields']);layout=saved.pop('__layout',None)
        session.ref=ref;session.form=form;session.values=saved;session.photo=data['photo']
        session.layout=validate_layout(form,layout);session.layout_upgrade=layout is None
        session.file_name=record.name
        session.edit_id=token;session._service=service;session.dirty=False;session.pending_target=None
        session.operation_id=uuid4().hex
        return dict(record=record,form=deepcopy(form),values=deepcopy(session.values),has_photo=session.photo is not None)


def stage_fields(session,values):
    """先保留有界的原始文字；只有导出通过全部规则后才写入文件库。"""
    with session.lock:
        if not session.form or not session.edit_id:raise ValueError('请先打开个人副本。')
        if not isinstance(values,dict):raise ValueError('填写内容格式无效。')
        allowed={f['id'] for f in session.form['fields']}
        if set(values)-allowed or any(not isinstance(v,str) for v in values.values()):
            raise ValueError('填写字段无效。')
        merged=session.values|values
        if sum(len(v) for v in merged.values())>200000:raise ValueError('草稿文字超出临时存储限制。')
        if merged!=session.values:
            session.values=merged;session.dirty=True;session.operation_id=uuid4().hex


def update_fields(session,values,photo):
    with session.lock:
        stage_fields(session,values)
        merged=validate_fields(session.form,session.values)
        if photo is not None:photo=validate_photo(photo)
        if photo and not session.form.get('photo'):raise ValueError('此模板没有照片栏。')
        if merged!=session.values or photo!=session.photo:
            session.values=merged;session.photo=photo;session.dirty=True;session.operation_id=uuid4().hex


def stage_layout(session,value):
    with session.lock:
        if not session.form:raise ValueError('请先打开个人副本。')
        value=validate_layout(session.form,value)
        if value!=session.layout:
            session.layout=value;session.dirty=True;session.operation_id=uuid4().hex


def renew_form(session):
    with session.lock:
        if session.ref and session.edit_id and session._service:
            session._service.store.renew_lease(session.ref,session.edit_id)


def save_form_session(service,session):
    with session.lock:
        if session.ref is None or not session.edit_id:raise ValueError('请先打开要填写的文件。')
        service.store.renew_lease(session.ref,session.edit_id)
        if not session.dirty and not session.layout_upgrade:return service.store.get(session.ref)
        record=service.save_form(session.ref,session.values,session.photo,session.operation_id,writer_token=session.edit_id,layout=session.layout)
        session.ref=record.ref;session.dirty=False;session.layout_upgrade=False;session.operation_id=uuid4().hex
        return record


def prepare_form_action(service,session,action):
    if action not in {'download','mail','preview'}:raise ValueError('未知文件操作。')
    return save_form_session(service,session)


def request_leave(session,target):
    with session.lock:
        session.pending_target=target
        return session.dirty


def resolve_leave(service,session,choice):
    if choice not in {'save','discard','cancel'}:raise ValueError('请选择保存、放弃或返回填写。')
    with session.lock:
        if choice=='cancel':
            session.pending_target=None;return None
        target=session.pending_target
        if choice=='save':save_form_session(service,session)
        if session.edit_id:service.store.release_lease(session.edit_id)
        session.ref=None;session.form=None;session.values={};session.photo=None
        session.edit_id=None;session.dirty=False;session.pending_target=None;session._service=None
        session.layout=default_layout();session.layout_upgrade=False
        return target


def fill_from_profile(session,profile):
    with session.lock:
        if not session.form:raise ValueError('请先打开个人副本。')
        values=dict(session.values)
        for field in session.form['fields']:
            key=field.get('profile_key')
            if key and hasattr(profile,key):values[field['id']]=getattr(profile,key)
        update_fields(session,values,session.photo)
        return deepcopy(session.values)
