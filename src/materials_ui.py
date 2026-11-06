"""Knowledge catalogue lives inside its workspace, not the global sidebar."""
from html import escape
from campus_navigation import categories,leaf


def target_button(label,target,css='materials-entry'):
    return f'<button type="button" class="{css}" data-campus-target="{escape(target,quote=True)}">{escape(label)}</button>'


def materials_breadcrumb(workspace):
    items=[target_button('资料卡片','materials','materials-back')]
    if workspace.startswith('category:'):category=workspace[9:]
    elif workspace.startswith('topic:'):category=leaf(workspace[6:])['category']
    else:return ''
    row=next((r for r in categories() if r['id']==category),None)
    if row is None:raise ValueError('未知资料类别。')
    if workspace.startswith('topic:'):items.append(target_button(row['label'],'category:'+category,'materials-back'))
    else:items.append('<span aria-current="page">'+escape(row['label'])+'</span>')
    return '<nav class="materials-breadcrumb" aria-label="资料位置">'+'<span aria-hidden="true">/</span>'.join(items)+'</nav>'


def render_materials(workspace):
    if workspace=='materials':
        rows=categories();heading='资料卡片';intro='从学习到校园生活，找到你需要的资料。'
        entries=[target_button(r['label'],'category:'+r['id']) for r in rows]
    elif workspace.startswith('category:'):
        row=next((r for r in categories() if r['id']==workspace[9:]),None)
        if row is None:raise ValueError('未知资料类别。')
        heading=row['label'];intro='选择专题，查看相关资料与办理入口。'
        entries=[target_button(r['label'],'topic:'+r['id']) for r in row['children']]
    else:raise ValueError('未知资料页面。')
    return '<section class="materials-page">'+materials_breadcrumb(workspace)+'<header><h2>'+escape(heading)+'</h2><p>'+intro+'</p></header><div class="materials-grid">'+''.join(entries)+'</div></section>'
