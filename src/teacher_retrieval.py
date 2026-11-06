"""Bounded complete faculty blocks from the same snapshot used by the directory."""
import re
import teacher_catalog as catalog_module

SWITCH=re.compile(r'校园网|宿舍|食堂|报修|申请表|奖学金|考试时间|考试安排|考场|取消提醒|报名')
INTENT=re.compile(r'教师|老师|导师|师资|教授|邮箱|研究方向|联系方式|办公室')


def _rank(question,data):
    explicit=set(re.findall(r'(?<![A-Za-z0-9])(T\d{2,})(?![A-Za-z0-9])',question))
    if explicit:return [(100,t) for t in data['teachers'] if t['id'] in explicit]
    names={c['id']:c['name'] for c in data['colleges']}
    groups={g['id']:g['name'] for g in data['groups']}
    result=[]
    for t in data['teachers']:
        score=100 if t['name'] in question else 0
        if INTENT.search(question):
            labels=[names[a['college_id']] for a in t['affiliations']]
            labels += [groups[g] for a in t['affiliations'] for g in a['group_ids']]
            score+=20*sum(label in question for label in labels)
            score+=10*sum(c in question for c in t['courses'] if len(c)>=3)
            words=re.split(r'[，、；;。/（）()：:与和\s]+',t['research'])
            score+=5*sum(w in question for w in words if len(w)>=3)
        if score:result.append((score,t))
    return result


def relevant_teachers(question,history=(),*,limit=2400,max_records=6,catalog=None):
    if not isinstance(question,str) or limit<=0:return ''
    data=catalog if catalog is not None else catalog_module.catalog_or_unavailable()
    if not data.get('available',True):return ''
    ranked=_rank(question,data)
    if not ranked and not SWITCH.search(question) and re.search(r'他|她|这位|该老师|邮箱|联系方式|研究方向|办公室|授课',question):
        for message in reversed(list(history)[-6:]):
            if message.get('role')!='user':continue
            previous=message.get('content','')
            if SWITCH.search(previous):break
            ranked=_rank(previous,data)
            if ranked:break
    chunks=[];used=0
    for _,teacher in sorted(ranked,key=lambda pair:(-pair[0],pair[1]['id'])):
        block=catalog_module.teacher_block(data,teacher)
        cost=len(block)+(2 if chunks else 0)
        if used+cost>min(limit,2400):continue
        chunks.append(block);used+=cost
        if len(chunks)>=min(max_records,6):break
    return '\n\n'.join(chunks)
