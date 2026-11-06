"""教师目录的快捷收件人；仅填表，不发送。"""
from teacher_catalog import catalog_or_unavailable, affiliation_labels, filter_teachers
from mail_service import cancel_mail


def college_choices():
    return [('全部学院',''),*[(c['name'],c['id']) for c in catalog_or_unavailable()['colleges']]]


def teacher_choices(*,college_id=None):
    data=catalog_or_unavailable()
    return [(f"{row['name']} · {' / '.join(affiliation_labels(data,row))}", row['id'])
            for row in filter_teachers(data,college_id=college_id or None)]


def teacher_contact(teacher_id):
    for row in catalog_or_unavailable()['teachers']:
        if row['id'] == teacher_id:
            return row
    raise ValueError('请先选择目录中的教师。')


def fill_teacher_recipient(current, teacher_id, email):
    row = teacher_contact(teacher_id)
    if not row['emails']:
        raise ValueError('该教师尚未收录明确的公开邮箱，请查看官网资料。')
    if email not in row['emails']:
        raise ValueError('请从该教师已收录的公开邮箱中选择。')
    cancel_mail(current.mail)
    return email
