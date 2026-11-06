"""College-first read-only workspace; faculty browsing never changes chat or mail."""
from html import escape as esc
from math import ceil
from pathlib import Path
from uuid import uuid4
import gradio as gr
from teacher_catalog import catalog_or_unavailable, filter_teachers, affiliation_labels

STATES={'complete':'名录已核对','partial':'部分资料','not_checked':'待补充','unpublished':'未找到公开名录','blocked':'访问受限','unavailable':'暂不可用'}
STAFF={'current':'现任','external':'外聘','part_time':'兼职','emeritus':'荣休','unspecified':'官网未注明任职类型'}
FILTERS={'default':{'current','unspecified'},'all':set(STAFF),**{k:{k} for k in STAFF}}


def _search(query=''):
    return ('<div class="faculty-search"><input aria-label="搜索教师" data-faculty-query maxlength="100" '
            f'placeholder="搜索姓名、课程或研究方向" value="{esc(query,quote=True)}">'
            '<button data-faculty-action="search">搜索</button></div>')


def render_colleges(data):
    coverage={c['college_id']:c for c in data['coverage']};cards=[]
    for c in data['colleges']:
        count=sum(any(a['college_id']==c['id'] for a in t['affiliations']) for t in data['teachers'])
        cards.append(f'<button class="faculty-college" data-faculty-action="open-college" data-college-id="{esc(c["id"])}">'
            f'<span>{esc(c["name"])}</span><small>{count} 位已收录 · {STATES[coverage[c["id"]]["roster_state"]]}</small><i aria-hidden="true">↗</i></button>')
    note='' if data['available'] else '<p role="status">教师资料暂不可用；学院入口保留，稍后请重试。</p>'
    return '<header><p class="faculty-eyebrow">校园师资</p><h2 tabindex="-1">教师资料</h2><p>从学院开始，找到课程与研究方向对应的教师。</p></header>'+_search()+note+'<div class="faculty-colleges">'+''.join(cards)+'</div>'


def render_faculty(data,*,college_id=None,group_id=None,query='',staff_type='default',page=1):
    if staff_type not in FILTERS:raise ValueError('任职类型无效。')
    if not isinstance(query,str) or len(query)>100:raise ValueError('搜索内容最多100字。')
    rows=filter_teachers(data,query=query,college_id=college_id,group_id=group_id,staff_types=FILTERS[staff_type])
    pages=max(1,ceil(len(rows)/24))
    if type(page) is not int or not 1<=page<=pages:raise ValueError('教师页码已变化，请重新筛选。')
    college=next((c for c in data['colleges'] if c['id']==college_id),None)
    title=college['name'] if college else '全校搜索'
    official=f'<a class="faculty-official" href="{esc(college["home_url"],quote=True)}" target="_blank" rel="noopener noreferrer">学院官网 ↗</a>' if college else ''
    groups=[g for g in data['groups'] if college_id and g['college_id']==college_id]
    options='<option value="">全部系所</option>'+''.join(f'<option value="{esc(g["id"])}"'+(' selected' if g['id']==group_id else '')+f'>{esc(g["name"])}</option>' for g in groups)
    types=''.join(f'<option value="{k}"'+(' selected' if k==staff_type else '')+f'>{v}</option>' for k,v in {'default':'现任与未注明','all':'全部任职类型',**STAFF}.items())
    filters=f'<div class="faculty-filters"><label>系所<select data-faculty-group>{options}</select></label><label>任职类型<select data-faculty-staff>{types}</select></label></div>'
    cards=[]
    for row in rows[(page-1)*24:page*24]:
        emails='、'.join(row['emails']) or '尚未收录明确的公开邮箱'
        cards.append('<article class="teacher-card">'+f'<h3>{esc(row["name"])}</h3><p class="faculty-affiliation">{esc(" / ".join(affiliation_labels(data,row)))}</p>'
            f'<p class="faculty-title">{esc(row["title"] or "官网未注明职称")} · {STAFF[row["staff_type"]]}</p><p>{esc(row["research"] or "研究方向尚未收录")}</p>'
            '<details><summary>联系方式与资料来源</summary>'+f'<p>{esc(emails)}</p><p>{esc(row["office"])}</p>'
            f'<a href="{esc(row["source_url"],quote=True)}" target="_blank" rel="noopener noreferrer">教师官网资料</a><small>核对日期：{esc(row["checked_on"])}</small></details></article>')
    gaps=''
    if college_id:
        coverage=next(c for c in data['coverage'] if c['college_id']==college_id)
        if coverage['gaps']:gaps='<details class="faculty-coverage"><summary>资料范围 · '+STATES[coverage['roster_state']]+'</summary><p>'+esc('；'.join(coverage['gaps']))+'</p></details>'
    empty='<p class="faculty-empty">尚未收录符合条件的资料。可调整筛选或通过学院官网核实；不能据此判断实际教师人数。</p>' if not rows else ''
    paging=f'<nav class="faculty-pagination" aria-label="教师分页"><button data-faculty-action="page" data-page="{page-1}"'+(' disabled' if page==1 else '')+f'>上一页</button><span>{page} / {pages}</span><button data-faculty-action="page" data-page="{page+1}"'+(' disabled' if page==pages else '')+'>下一页</button></nav>'
    return '<button class="faculty-back" data-faculty-action="back">← 返回学院</button>'+f'<header><h2 tabindex="-1">{esc(title)}</h2>{official}</header>'+_search(query)+filters+f'<p class="faculty-count">找到 {len(rows)} 位教师</p>'+gaps+empty+'<div class="faculty-grid">'+''.join(cards)+'</div>'+paging


def mount_teacher_panel():
    data=catalog_or_unavailable()
    initial=dict(college_id=None,group_id=None,query='',staff_type='default',page=1,home=True,view_token=uuid4().hex)
    state=gr.State(initial)
    script=(Path(__file__).resolve().parents[1]/'resources'/'teachers.js').read_text(encoding='utf-8')
    bridge=gr.HTML(value=dict(html=render_colleges(data),view_token=initial['view_token'],delivery_id=uuid4().hex),
        html_template='<section class="faculty-workspace"><p class="faculty-feedback" role="status"></p><div class="faculty-surface"></div></section>',
        js_on_load=script,elem_id='teacher-results')
    def show(value,current):
        action=value.get('action',{}) if isinstance(value,dict) else {};expected=action.get('view_token')
        if expected!=current['view_token']:return gr.skip(),current
        updated=dict(current)
        try:
            kind=action.get('kind')
            if kind=='back':updated.update(home=True,college_id=None,group_id=None,query='',staff_type='default',page=1)
            elif kind=='open-college':
                cid=action.get('college_id')
                if cid not in {c['id'] for c in data['colleges']}:raise ValueError('学院已变化，请返回重选。')
                updated.update(home=False,college_id=cid,group_id=None,query='',staff_type='default',page=1)
            elif kind=='search':updated.update(home=False,college_id=None,group_id=None,query=action.get('query',''),page=1)
            elif kind=='filter':updated.update(home=False,group_id=action.get('group_id') or None,staff_type=action.get('staff_type'),page=1)
            elif kind=='page':updated.update(page=action.get('page'))
            else:raise ValueError('教师操作无效。')
            html=render_colleges(data) if updated['home'] else render_faculty(data,**{k:updated[k] for k in ('college_id','group_id','query','staff_type','page')})
            updated['view_token']=uuid4().hex
            return dict(html=html,view_token=updated['view_token'],delivery_id=uuid4().hex,reply_for=expected,focus_college=current['college_id'] if kind=='back' else None),updated
        except ValueError as error:
            return dict(html=render_colleges(data) if current['home'] else render_faculty(data,**{k:current[k] for k in ('college_id','group_id','query','staff_type','page')}),
                view_token=current['view_token'],delivery_id=uuid4().hex,reply_for=expected,message=str(error)),current
    bridge.submit(show,[bridge,state],[bridge,state],queue=False,api_name=False,show_progress='hidden')
    return dict(bridge=bridge,state=state)
