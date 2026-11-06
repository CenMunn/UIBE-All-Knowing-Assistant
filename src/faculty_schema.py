"""Normalize legacy faculty records without changing identity or inventing facts."""
from copy import deepcopy
from datetime import date
import re
from faculty_sources import official_url


def empty_catalog(sources):
    return dict(schema_version=2,checked_on=sources['checked_on'],available=False,
        colleges=[{k:deepcopy(c[k]) for k in ('id','name','home_url','roster_urls')} for c in sources['colleges']],
        groups=[],teachers=[],departments={},coverage=[dict(college_id=c['id'],roster_state='not_checked',
            visited_pages=[],profile_checked_count=0,gaps=['尚未核对公开师资名录'],checked_on='') for c in sources['colleges']])


def _text(value,limit=2000,required=False):
    if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):raise ValueError('教师资料文字无效。')
    return value


def normalize_catalog(raw,sources):
    try:
        data=deepcopy(raw)
        if data['schema_version']==1:
            old=data;data=empty_catalog(sources);data.update(available=True,checked_on=old['checked_on'])
            for i,(name,url) in enumerate(old['departments'].items(),1):
                data['groups'].append(dict(id=f'ai-data:g{i}',college_id='ai-data',name=name,kind='department',source_url=url))
            groups={g['name']:g['id'] for g in data['groups']}
            for teacher in old['teachers']:
                teacher.update(affiliations=[dict(college_id='ai-data',group_ids=[groups[teacher['department']]],
                    source_urls=[old['departments'][teacher['department']]])],staff_type='unspecified',flags=[],
                    checked_on=old['checked_on'],profile_status='list_only' if not teacher['title'] else 'checked')
                data['teachers'].append(teacher)
            coverage=next(c for c in data['coverage'] if c['college_id']=='ai-data')
            coverage.update(roster_state='partial',visited_pages=list(old['departments'].values()),
                profile_checked_count=sum(t['profile_status']=='checked' for t in data['teachers']),
                checked_on=old['checked_on'],gaps=['旧目录，尚未按全校更新口径重新核对'])
        elif data['schema_version']!=2:raise ValueError('未知教师资料版本。')
        date.fromisoformat(data['checked_on'])
        if type(data['available']) is not bool:raise ValueError('教师目录状态无效。')
        college_ids=set();known={c['id']:c for c in sources['colleges']}
        for c in data['colleges']:
            if c['id'] in college_ids or c['id'] not in known:raise ValueError('学院编号无效或重复。')
            college_ids.add(c['id']);_text(c['name'],100,True)
            if not official_url(c['home_url'],college_id=c['id'],sources=sources):raise ValueError('学院来源无效。')
            if not isinstance(c['roster_urls'],list) or any(not official_url(u,college_id=c['id'],sources=sources) for u in c['roster_urls']):raise ValueError('学院名录来源无效。')
        if college_ids!=set(known):raise ValueError('教学单位登记不完整。')
        groups={}
        for g in data['groups']:
            if not re.fullmatch(r'[a-z0-9:-]{1,100}',g['id']) or g['id'] in groups:raise ValueError('教师分组编号无效。')
            if g['college_id'] not in college_ids or g['kind'] not in {'major','department','unspecified'}:raise ValueError('教师分组归属无效。')
            _text(g['name'],100,True)
            if not official_url(g['source_url'],college_id=g['college_id'],sources=sources):raise ValueError('分组来源无效。')
            groups[g['id']]=g
        seen=set()
        for t in data['teachers']:
            for k,limit in [('id',40),('name',100),('department',100),('title',300),('research',2000),('office',300),('notes',2000),('source_url',2048)]:_text(t[k],limit,k in {'id','name'})
            if not re.fullmatch(r'T\d{2,}',t['id']) or t['id'] in seen:raise ValueError('教师编号无效或重复。')
            seen.add(t['id'])
            if not official_url(t['source_url'],sources=sources):raise ValueError('教师官网来源无效。')
            for k in ('emails','courses'):
                if not isinstance(t[k],list) or len(t[k])>100:raise ValueError('教师邮箱或课程格式无效。')
                for v in t[k]:_text(v,300,True)
            if any(not re.fullmatch(r'[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}',e) for e in t['emails']):raise ValueError('教师邮箱格式无效。')
            if t['staff_type'] not in {'current','external','part_time','emeritus','unspecified'}:raise ValueError('教师任职类型无效。')
            if t['profile_status'] not in {'checked','list_only'}:raise ValueError('教师主页核对状态无效。')
            if not isinstance(t['flags'],list) or any(f!='international_faculty' for f in t['flags']):raise ValueError('教师分类标记无效。')
            date.fromisoformat(t['checked_on'])
            if 'profile_checked_on' in t:date.fromisoformat(t['profile_checked_on'])
            if 'field_provenance' in t:
                if not isinstance(t['field_provenance'],dict) or set(t['field_provenance'])-{'title','research','emails','office','courses','notes'}:raise ValueError('教师字段来源无效。')
                for field in t['field_provenance'].values():
                    date.fromisoformat(field['checked_on'])
                    if not official_url(field['source_url'],sources=sources):raise ValueError('教师字段来源无效。')
            if not isinstance(t['affiliations'],list) or not t['affiliations']:raise ValueError('教师缺少学院归属。')
            for a in t['affiliations']:
                if a['college_id'] not in college_ids or not isinstance(a['group_ids'],list) or not a['group_ids']:raise ValueError('教师归属无效。')
                if any(k not in groups or groups[k]['college_id']!=a['college_id'] for k in a['group_ids']):raise ValueError('教师系所归属无效。')
                if not isinstance(a['source_urls'],list) or not a['source_urls'] or any(not official_url(u,college_id=a['college_id'],sources=sources) for u in a['source_urls']):raise ValueError('教师隶属来源无效。')
        coverage_ids=set()
        for c in data['coverage']:
            if c['college_id'] not in college_ids or c['college_id'] in coverage_ids:raise ValueError('资料覆盖编号无效。')
            coverage_ids.add(c['college_id'])
            if c['roster_state'] not in {'complete','partial','unpublished','blocked','unavailable','not_checked'}:raise ValueError('资料覆盖状态无效。')
            if c['checked_on']:date.fromisoformat(c['checked_on'])
            if type(c['profile_checked_count']) is not int or c['profile_checked_count']<0:raise ValueError('资料覆盖计数无效。')
            if any(not official_url(u,college_id=c['college_id'],sources=sources) for u in c['visited_pages']):raise ValueError('覆盖页面来源无效。')
            if not isinstance(c['gaps'],list) or any(not isinstance(v,str) for v in c['gaps']):raise ValueError('资料缺口格式无效。')
        if coverage_ids!=college_ids:raise ValueError('学院覆盖登记不完整。')
        # Compatibility names are not identities. New consumers always use group IDs.
        names=dict.fromkeys(g['name'] for g in groups.values())
        data['departments']={n:next(g['source_url'] for g in groups.values() if g['name']==n)
            for n in names if sum(g['name']==n for g in groups.values())==1}
        return data
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError('教师资料结构不完整。') from exc
