"""自填学号对应的本机会话分区；不代表身份认证。"""
from session_profile import Profile


def owner_for_profile(profile: Profile) -> str | None:
    from exam_import import student_id
    if not profile.student_id.strip():
        return None
    return 'student:' + student_id(profile.student_id)


def validate_owner_key(value: str) -> str:
    from exam_import import student_id
    if not isinstance(value, str) or not value.startswith('student:'):
        raise ValueError('请填写有效学号后再管理历史会话。')
    sid = value[len('student:'):]
    if not sid or student_id(sid) != sid:
        raise ValueError('历史会话归属无效，请重新填写学号。')
    return value
