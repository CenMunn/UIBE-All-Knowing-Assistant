"""Local suggestions only: a candidate is never an automatic OCR correction."""
import re
import unicodedata
from teacher_catalog import catalog_or_unavailable,affiliation_labels


def _name(text):return re.sub(r'\s+','',unicodedata.normalize('NFKC',text or '')).casefold()


def _distance(left,right):
    previous=list(range(len(right)+1))
    for i,a in enumerate(left,1):
        row=[i]
        for j,b in enumerate(right,1):row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(a!=b)))
        previous=row
    return previous[-1]


def teacher_candidates(name,*,catalog=None,limit=3):
    data=catalog if catalog is not None else catalog_or_unavailable()
    result=dict(candidates=[],status='empty',truncated=False)
    if not data.get('available'):return result|dict(status='unavailable')
    if not isinstance(name,str) or len(name)>100 or type(limit) is not int or limit<=0:return result
    name=_name(name)
    if not name:return result
    hits=[]
    for teacher in data['teachers']:
        other=_name(teacher['name'])
        distance=0 if other==name else 99
        if distance and re.fullmatch(r'[\u4e00-\u9fff]{2,4}',name) and re.fullmatch(r'[\u4e00-\u9fff]{2,4}',other):distance=_distance(name,other)
        if distance>1:continue
        hits.append(dict(teacher_id=teacher['id'],name=teacher['name'],affiliations=affiliation_labels(data,teacher),
            source_url=teacher['source_url'],reason='姓名一致，仍需核对学院' if distance==0 else '相近姓名，需核对原图及学院',distance=distance,
            similarity=1-distance/max(len(name),len(other)),
            sort_college=min(a['college_id'] for a in teacher['affiliations'])))
    hits.sort(key=lambda r:(-r['similarity'],r['distance'],r['sort_college'],r['teacher_id']))
    candidates=[{k:v for k,v in row.items() if k!='sort_college'} for row in hits[:min(limit,3)]]
    return dict(candidates=candidates,status='ok' if hits else 'empty',truncated=len(hits)>min(limit,3))
