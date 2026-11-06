"""文件版本快照与邮件附件；发送期间阻止冲突变更。"""
from dataclasses import dataclass,field
from hashlib import sha256
from document_models import FileRef
from document_validation import MAX_FILE


@dataclass(frozen=True)
class MailAttachment:
    ref: FileRef
    name: str
    media_type: str
    size: int
    digest: str
    content: bytes=field(repr=False)


def validate_attachments(attachments):
    if not isinstance(attachments,tuple) or len(attachments)>10:raise ValueError('每封邮件最多10个附件。')
    if sum(a.size for a in attachments)>20*1024*1024:raise ValueError('附件总大小不能超过20MiB。')
    ids=set()
    for a in attachments:
        if (not isinstance(a,MailAttachment) or not 0<a.size<=MAX_FILE or a.size!=len(a.content)
                or sha256(a.content).hexdigest()!=a.digest or a.ref.id in ids):
            raise ValueError('附件大小、版本或内容无效，请重新选择。')
        ids.add(a.ref.id)
    return attachments


def resolve_attachments(store,refs):
    if not isinstance(refs,tuple) or len(refs)>10:raise ValueError('最多选择10个附件。')
    result=[]
    for ref in refs:
        record=store.get(ref);content=store.read(ref)
        result.append(MailAttachment(ref,record.name,record.media_type,record.size,record.digest,content))
    return validate_attachments(tuple(result))


def deliver_managed_mail(store,refs,session,settings,recipient,subject,body,*,sender,logger):
    from mail_service import deliver_mail
    if not refs:return deliver_mail(session,settings,recipient,subject,body,sender=sender,logger=logger)
    with store.lease(refs,'mail'):
        attachments=resolve_attachments(store,refs)
        return deliver_mail(session,settings,recipient,subject,body,sender=sender,logger=logger,attachments=attachments)
