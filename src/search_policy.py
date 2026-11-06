"""从可信公共词表重构查询，不把删减后的用户原句发给搜索服务。"""
from dataclasses import dataclass
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from campus_officials import SCHOOLS
from web_search import page_heading

@dataclass(frozen=True)
class SearchDecision:
    query: str | None
    reason: str
    freshness_required: bool = False

TOPICS = {
    '国家励志奖学金': ('国家励志奖学金',), '国家奖学金': ('国家奖学金',),
    '奖学金申请': ('奖学金',), '助学金申请': ('助学金',),
    '本科生学籍管理办法 重修': ('重修','补考'), '考试规则': ('考试规则','考场纪律'),
    '课程选择 选课通知': ('选课','课程选择'), '培养方案 学分 毕业要求': ('学分制度','毕业要求','培养方案'),
    '宿舍管理': ('宿舍','住宿'), '餐饮服务': ('食堂','餐饮'),
    '学生社团 招募': ('社团','百团大战','社联'), '志愿者活动': ('志愿者','志愿活动','志愿服务'), '体育场馆': ('体育馆','健身','体育赛事'),
    '校医院 医保报销': ('校医院','医疗报销','医保'), '心理咨询': ('心理咨询',),
    '学费缴纳': ('学费','缴费'), '校园招聘': ('招聘会','校园招聘','就业'),
    '校园安全': ('校园安全',), '校园交通': ('校车','自行车','校园交通'),
    '校园网络': ('校园网',), '图书馆电子资源': ('图书馆','电子资源'),
    '学习平台 超星 智慧树': ('学习通','智慧树','TAS平台'),
    '国际交流 交换项目': ('国际交流','交换项目','出国留学'),
}

def _colleges():
    from teacher_catalog import catalog_or_unavailable
    return [c['name'] for c in catalog_or_unavailable()['colleges']]

def _college(text):
    matches=[c for c in _colleges() if c in text]
    if not matches and any(a in text for a in ('信息学院','智数学院')):
        matches=['人工智能与数据科学学院']
    return matches[0] if len(set(matches))==1 else None

def decide_search(question, history, background, local_evidence, *, enabled, consent):
    if not enabled: return SearchDecision(None,'disabled')
    if not consent: return SearchDecision(None,'consent_required')
    if re.search(r'我的.*(?:考试|成绩|考场|排名|绩点)|(?:本人|学号).*考试|保研丹|程序.*挂|python|sql',question,re.I):
        return SearchDecision(None,'private_or_nonpublic')
    school=background.get('school','')
    if school and school not in SCHOOLS and not any(s in question for s in SCHOOLS):
        return SearchDecision(None,'unknown_school')
    if re.search(r'(?:北京大学|清华大学|其他大学|别的学校)',question):
        return SearchDecision(None,'unknown_school')
    fresh=bool(re.search(r'最新|现任|今年|本学期|本周|这周|近期|最近|当前|现在|仍可|还[能可]以?报名|院长|校长|截止|报名时间',question))
    if '院长' in question or '学院领导' in question:
        college=_college(question)
        if not college and re.search(r'[\u4e00-\u9fff]{1,20}学院',question.replace('我们学院','').replace('本学院','')):
            return SearchDecision(None,'unknown_college',True)
        if not college: college=_college(background.get('college',''))
        if not college:
            for m in reversed(history[-12:]):
                if m.get('role')=='user':
                    college=_college(m.get('content',''))
                    if college:break
        if not college:return SearchDecision(None,'unknown_college',True)
        topic=college+' 学院领导 院长'
    elif '校长' in question: topic='学校领导 校长'
    else:
        topics=[t for t,words in TOPICS.items() if any(w in question for w in words)]
        if not topics:return SearchDecision(None,'unsupported_topic',fresh)
        # 更具体的奖项优先，所有输出词均来自词表。
        if '国家励志奖学金' in topics:topics=['国家励志奖学金']
        elif '国家奖学金' in topics:topics=['国家奖学金']
        topic=' '.join(topics[:2])
        terms=[w for t in topics for w in TOPICS[t]]
        covered=any(any(w in r.get('title','') for w in terms) and
                    not re.search(r'缺口|未收录|待补',r.get('title','')) for r in local_evidence)
        if covered and not fresh:return SearchDecision(None,'local_covered')
    years=re.findall(r'(?<!\d)(20\d{2})(?=年|—|－|-)',question)
    time_hint=years[0] if years else ''
    if not years and fresh and any(t in topic for t in ('学生社团 招募','志愿者活动')):
        now=datetime.now(timezone(timedelta(hours=8)))
        time_hint=f'{now.year}年{now.month}月'
    query='对外经济贸易大学 '+topic+(' '+time_hint if time_hint else '')+' site:uibe.edu.cn'
    return SearchDecision(query[:128],'public_topic',fresh)


def filter_public_pages(pages, decision):
    """保守筛选对象与标题；发布时间未知保持未知，不能当作最新发布。"""
    if not decision.query:return []
    query=decision.query
    requested_year=re.search(r'\s(20\d{2})\s+site:',query)
    target_year=int(requested_year[1]) if requested_year else datetime.now(timezone(timedelta(hours=8))).year
    selected=[]
    for p in pages:
        title=p.title;body=p.title+'\n'+p.content
        if '学院领导 院长' in query:
            college=query.removeprefix('对外经济贸易大学 ').split(' 学院领导')[0]
            aliases=[college]+(['信息学院','智数学院'] if college=='人工智能与数据科学学院' else [])
            if not any(c in body for c in aliases) or not re.search(r'学院领导|院长|领导机构',title):continue
        elif '学校领导 校长' in query:
            # 学校名录常只题为“现任领导”；不把学院同名页或校长讲话当作任职名录。
            main_site=urlsplit(p.url).hostname in ('uibe.edu.cn','www.uibe.edu.cn')
            heading=page_heading(title)
            if not re.fullmatch(r'\s*(?:学校领导(?:\s*校长)?|现任领导|校领导|校长信息|现任校长)\s*',heading):continue
            if not main_site:continue
        else:
            topics=[t for t in TOPICS if t in query]
            words={w for t in topics for w in TOPICS[t]}
            u=urlsplit(p.url)
            activity_page=(u.hostname=='ea.uibe.edu.cn' and
                           bool(re.fullmatch(r'/(?:index/)?class(?:/.*)?',u.path,re.I)) and
                           any(t in topics for t in ('学生社团 招募','志愿者活动')))
            matched=any(w in title for w in words) or (activity_page and any(w in p.content for w in words))
            if not words or not matched:continue
        dates=re.findall(r'(?<!\d)(20\d{2})(?:年|[-/])',title+' '+(p.published_on or ''))
        if decision.freshness_required and dates and max(map(int,dates))<target_year:continue
        selected.append(p)
    return selected
