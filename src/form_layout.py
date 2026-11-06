"""仅允许白名单填写区域的排版参数，不接受任意CSS或OOXML。"""
from copy import deepcopy

DEFAULTS={'font_size':12,'line_spacing':1,'alignment':'original'}


def default_layout():
    return dict(version=1,grow_rows=True,defaults=deepcopy(DEFAULTS),fields={})


def validate_layout(form,value=None):
    if value is None:return default_layout()
    if not isinstance(value,dict) or set(value)-{'version','grow_rows','defaults','fields'}:
        raise ValueError('排版设置格式无效。')
    if value.get('version',1)!=1 or type(value.get('grow_rows',True)) is not bool:
        raise ValueError('排版版本或行高设置无效。')
    def style(data,partial=False):
        if not isinstance(data,dict) or set(data)-set(DEFAULTS):raise ValueError('含不允许的排版设置。')
        out={} if partial else deepcopy(DEFAULTS)
        for key,v in data.items():
            if key in ('font_size','line_spacing'):
                if isinstance(v,bool):raise ValueError('字号或行距无效。')
                try:v=float(v)
                except (ValueError,TypeError):raise ValueError('字号或行距无效。') from None
                allowed=(10.5,11,12,14,16) if key=='font_size' else (1,1.15,1.5,2)
                if v not in allowed:raise ValueError('字号或行距超出允许范围。')
            elif v not in ('original','left','center','right'):raise ValueError('对齐方式无效。')
            out[key]=v
        return out
    fields=value.get('fields',{})
    if not isinstance(fields,dict) or set(fields)-{f['id'] for f in form['fields']}:
        raise ValueError('不能修改非填写区域的排版。')
    cleaned={key:style(s,True) for key,s in fields.items() if s}
    for field in form['fields']:
        if 'paragraph' in field['target'] and set(cleaned.get(field['id'],{}))-{'font_size'}:
            raise ValueError('学校与学号共用一行，行距和对齐请使用统一设置。')
    return dict(version=1,grow_rows=value.get('grow_rows',True),
        defaults=style(value.get('defaults',{})),fields=cleaned)
