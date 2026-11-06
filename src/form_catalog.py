"""经校对的模板与字段白名单，不包含个人填写值。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

KINDS = {'text', 'multiline', 'date', 'number', 'choice'}
STATUSES = {'fillable', 'conversion_required', 'revision_review', 'unsupported'}


def validate_resource_path(root: Path, value: str) -> Path:
    if not isinstance(value, str) or '\\' in value or ':' in value:
        raise ValueError('模板资源位置无效。')
    relative = PurePosixPath(value)
    if relative.is_absolute() or '..' in relative.parts or not value.startswith(('resources/forms/', 'resources/campus/')):
        raise ValueError('模板资源超出允许目录。')
    base = Path(root).resolve()
    path = base.joinpath(*relative.parts)
    if not path.is_relative_to(base) or path.resolve() != path:
        raise ValueError('模板资源不能使用链接。')
    if not path.is_file():
        raise ValueError('模板资源缺失。')
    return path


def validate_form(root: Path, form: dict) -> None:
    if not re.fullmatch(r'TPL[0-9]{3}', form.get('id', '')) or form.get('status') not in STATUSES:
        raise ValueError('模板编号或状态无效。')
    validate_resource_path(root, form['source_file'])
    seen_ids, seen_targets = set(), set()
    if form['status'] == 'fillable':
        path = validate_resource_path(root, form['adapted_file'])
        if hashlib.sha256(path.read_bytes()).hexdigest() != form['template_version']:
            raise ValueError('模板版本已改变，请重新校对。')
        if form['format'] not in {'docx', 'xlsx'} or not form.get('fields'):
            raise ValueError('模板未适配。')
    for field in form.get('fields', []):
        key, target = field.get('id'), field.get('target')
        if (not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', key)
                or key in seen_ids or field.get('kind') not in KINDS or not isinstance(target, dict)
                or type(field.get('max_chars')) is not int or not 1 <= field['max_chars'] <= 5000):
            raise ValueError('模板字段无效。')
        location = json.dumps(target, sort_keys=True)
        if location in seen_targets:
            raise ValueError('模板字段位置重复。')
        seen_ids.add(key)
        seen_targets.add(location)
        if form['format'] == 'docx':
            if set(target)=={'paragraph','slot','prefix'}:
                if any(type(target[k]) is not int or target[k]<0 for k in ('paragraph','slot')) or not isinstance(target['prefix'],str):
                    raise ValueError('Word表头填写位置无效。')
            elif (set(target) != {'table', 'row', 'cell', 'paragraphs', 'prefix'}
                    or any(type(target.get(k)) is not int or target[k] < 0 for k in ('table', 'row', 'cell'))
                    or not isinstance(target['paragraphs'], list) or not target['paragraphs']
                    or any(type(p) is not int or p < 0 for p in target['paragraphs'])):
                raise ValueError('Word字段位置无效。')
        elif set(target) != {'part', 'cell', 'prefix'} or not re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml', target.get('part', '')) or not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,6}', target.get('cell', '')):
            raise ValueError('Excel字段位置无效。')
    preview = form.get('preview', {})
    if preview.get('kind') not in {'pdf', 'image', 'structure'}:
        raise ValueError('模板预览类型无效。')
    if preview.get('file'):
        validate_resource_path(root, preview['file'])


def load_form_catalog(root: Path) -> dict:
    values = json.loads((Path(root) / 'knowledge/template_forms.json').read_text(encoding='utf-8'))
    result = {}
    for form in values['templates']:
        validate_form(root, form)
        if form['id'] in result:
            raise ValueError('重复模板编号。')
        result[form['id']] = form
    return result


def get_form(catalog: dict, template_id: str, *, require_fillable: bool = False) -> dict:
    if not isinstance(template_id, str) or template_id not in catalog:
        raise ValueError('未知申请表。')
    form = catalog[template_id]
    if require_fillable and form['status'] != 'fillable':
        raise ValueError('此表尚未完成填写适配，请下载原表或选择其他模板。')
    return deepcopy(form)


def template_display_name(form: dict) -> str:
    variants = {'TPL008': '国家奖学金（校级评审表）',
                'TPL009': '国家奖学金（系统提交表）',
                'TPL010': '国家励志奖学金申请表'}
    if form['id'] in variants:
        return variants[form['id']]
    name = re.sub(r'\.(?:docx?|xlsx?)$', '', form['name'], flags=re.I)
    return re.sub(r'^(?:附件\d*[：:]\s*|\d+[.、]\s*)', '', name).removeprefix('对外经济贸易大学')


def forms_for_policy(catalog: dict, source_id: str) -> tuple[dict, ...]:
    return tuple(deepcopy(f) for f in catalog.values() if source_id in f.get('policy_ids', []))
