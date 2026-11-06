"""Bounded official-only collection and validated atomic publication."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit,urljoin
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.robotparser import RobotFileParser
from faculty_sources import official_url
from faculty_schema import normalize_catalog
from faculty_parsers import parse_roster,parse_profile

AGENT='UIBEFacultyCatalog/1.0'


class MissingRobots(ValueError):
    """The university CMS redirects nonexistent robots.txt to the school home."""


class OfficialFetcher:
    def __init__(self,sources,*,min_interval=1,max_pages=2500):
        self.sources=sources;self.interval=min_interval;self.limit=max_pages
        self.count=0;self.times={};self.cache={};self.robots={};self.blocked=set();self.robots_notes={}

    def validate_redirect(self,old,new,count):
        if count>=3 or urlsplit(old).hostname!=urlsplit(new).hostname or not official_url(new,sources=self.sources):raise ValueError('官方页面重定向超出登记范围。')
        rules=self.robots.get(urlsplit(new).netloc)
        if rules is not None and not rules.can_fetch(AGENT,new):raise PermissionError('robots.txt禁止重定向目标。')
        return new

    def _read(self,url,*,robots=False):
        if not official_url(url,sources=self.sources):raise ValueError('页面不在官方来源登记内。')
        host=urlsplit(url).hostname
        if host in self.blocked:raise PermissionError('该官网已返回403／429，本批停止访问。')
        if self.count>=self.limit:raise OSError('本批页面上限已达到。')
        delay=self.interval-(time.monotonic()-self.times.get(host,0))
        if delay>0:time.sleep(delay)
        outer=self
        class Redirect(HTTPRedirectHandler):
            hops=0
            def redirect_request(self,req,fp,code,msg,headers,newurl):
                if robots and newurl in {'http://www.uibe.edu.cn','http://www.uibe.edu.cn/'}:
                    raise MissingRobots('robots.txt跳转学校首页，未提供可解析的抓取规则；按公开HTML、限速方式核对')
                outer.validate_redirect(req.full_url,newurl,self.hops);self.hops+=1
                if outer.count>=outer.limit:raise OSError('本批页面上限已达到。')
                host=urlsplit(newurl).hostname
                delay=outer.interval-(time.monotonic()-outer.times.get(host,0))
                if delay>0:time.sleep(delay)
                outer.count+=1;outer.times[host]=time.monotonic()
                return super().redirect_request(req,fp,code,msg,headers,newurl)
        self.count+=1;self.times[host]=time.monotonic()
        try:
            with build_opener(Redirect()).open(Request(url,headers={'User-Agent':AGENT}),timeout=15) as response:
                kind=response.headers.get_content_type()
                if kind not in ({'text/plain','text/html'} if robots else {'text/html','application/xhtml+xml'}):raise ValueError('来源不是HTML页面。')
                data=response.read(2*1024*1024+1)
                if len(data)>2*1024*1024:raise ValueError('页面超过2MiB上限。')
                encoding=response.headers.get_content_charset() or 'utf-8'
                return data.decode(encoding,errors='replace')
        except HTTPError as error:
            if error.code in {403,429}:self.blocked.add(host)
            raise

    def __call__(self,url):
        if not official_url(url,sources=self.sources):raise ValueError('来源不在登记内。')
        if url in self.cache:return self.cache[url]
        host=urlsplit(url).netloc
        if host not in self.robots:
            robot=RobotFileParser();robot.set_url(urljoin(url,'/robots.txt'))
            try:robot.parse(self._read(robot.url,robots=True).splitlines())
            except MissingRobots as error:
                self.robots_notes[host]=str(error);robot.parse([])
            except HTTPError as error:
                if error.code not in {404,410}:raise
                robot.parse([])
            self.robots[host]=robot
        if not self.robots[host].can_fetch(AGENT,url):raise PermissionError('robots.txt禁止本次采集。')
        self.cache[url]=self._read(url)
        return self.cache[url]


def collect_college(source,*,fetch,previous,checked_on,profile_limit=300):
    pages=list(source.get('pages',[]));records=[];visited=[];gaps=[];blocked=False
    queue=list(pages);seen=set();profile_seen={};profile_count=0
    if not pages:
        try:fetch(source['home_url']);visited.append(source['home_url'])
        except (OSError,ValueError) as error:
            gaps.append(source['home_url']+'：'+type(error).__name__)
            blocked=isinstance(error,PermissionError) or isinstance(error,HTTPError) and error.code in {403,429}
    while queue and len(visited)<40:
        page=queue.pop(0);url=page['url'];key=(url,page.get('group'),page['selector'])
        if key in seen:continue
        seen.add(key)
        try:
            if not official_url(url,college_id=source['id']):raise ValueError('名录来源未登记。')
            parsed=parse_roster(fetch(url),url,page);visited.append(url)
            for record in parsed['records']:
                profile=record['profile_url']
                if source.get('upgrade_https') and profile.startswith('http://'):
                    profile='https://'+profile[7:];record['profile_url']=profile
                if profile and not official_url(profile,college_id=source['id']):
                    record['profile_url']=''
                    gaps.append(profile+'：个人页不在本院来源登记内，仅保留名录信息')
            if not parsed['records']:gaps.append(url+'：未解析出教师条目，保留旧资料')
            records.extend(parsed['records'])
            queue.extend(dict(page,url=u) for u in parsed['next_urls'] if (u,page.get('group'),page['selector']) not in seen)
        except (OSError,ValueError) as error:
            blocked=isinstance(error,PermissionError) or isinstance(error,HTTPError) and error.code in {403,429}
            gaps.append(url+'：'+type(error).__name__)
            if blocked:break
    if queue:gaps.append('名录未全部读取（访问或40页上限）')
    for record in records:
        profile=record['profile_url']
        if not profile or not official_url(profile,college_id=source['id']):continue
        if profile in profile_seen:
            if any(profile_seen[profile].values()):record['details']=profile_seen[profile]
            continue
        if profile_count>=profile_limit:continue
        try:
            profile_count+=1
            details=parse_profile(fetch(profile),profile)
            profile_seen[profile]=details
            if any(details.values()):record['details']=details
            else:gaps.append(profile+'：未提取到受支持的详情字段，仅保留名录信息')
        except (OSError,ValueError) as error:
            gaps.append(profile+'：详情'+type(error).__name__)
            if isinstance(error,PermissionError) or isinstance(error,HTTPError) and error.code in {403,429}:blocked=True;break
    unchecked=sum('details' not in r for r in records)
    robot_note=getattr(fetch,'robots_notes',{}).get(urlsplit(source['home_url']).netloc)
    if robot_note:gaps.append(robot_note)
    if unchecked:gaps.append(f'{unchecked}条名录记录未核对详情；邮箱和研究方向不推测')
    if not pages:gaps.append('尚未找到可解析的公开教师名录；需补充官方入口')
    state='blocked' if blocked else 'partial' if gaps else 'complete' if records else 'unpublished'
    return dict(college_id=source['id'],records=records,coverage=dict(college_id=source['id'],roster_state=state,
        visited_pages=list(dict.fromkeys(visited)),profile_checked_count=sum(any(v.values()) for v in profile_seen.values()),gaps=gaps,checked_on=checked_on),checked_on=checked_on)


def assemble_catalog(parts,previous,sources):
    result=normalize_catalog(previous,sources)
    result.update(checked_on=max([result['checked_on'],*[p['checked_on'] for p in parts]]),available=True)
    previous_ids={t['id'] for t in result['teachers']}
    # Existing rows are retained even if a partial page omits them. Identity is a canonical profile URL, not a name.
    roster_urls={u for c in sources['colleges'] for u in c['roster_urls']}
    by_url={(t['source_url']+'#'+t['name'] if t['source_url'] in roster_urls else t['source_url']):t for t in result['teachers']}
    next_id=max((int(t['id'][1:]) for t in result['teachers']),default=0)+1
    for part in parts:
        cid=part['college_id'];coverage=deepcopy(part['coverage'])
        alias_targets={}
        for r in part['records']:
            key=(r['roster_url'],r['name'])
            alias_targets.setdefault(key,set()).add(r['profile_url'] or r['roster_url']+'#'+r['name'])
        result['coverage']=[coverage if c['college_id']==cid else c for c in result['coverage']]
        if not part['records']:continue
        for record in sorted(part['records'],key=lambda r:(r['profile_url'] or r['roster_url'],r['name'],r['group'])):
            existing_group=next((g for g in result['groups'] if g['college_id']==cid and g['name']==record['group']),None)
            gid=existing_group['id'] if existing_group else cid+':'+sha256(record['group'].encode()).hexdigest()[:12]
            if not any(g['id']==gid for g in result['groups']):result['groups'].append(dict(id=gid,college_id=cid,name=record['group'],kind=record['kind'],source_url=record['roster_url']))
            identity=record['profile_url'] or record['roster_url']+'#'+record['name']
            row=by_url.get(identity)
            if row is None:
                matches=[t for t in result['teachers'] if t['id'] in previous_ids and t['source_url'] in roster_urls and t['profile_status']=='list_only' and t['name']==record['name'] and
                    any(a['college_id']==cid and record['roster_url'] in a['source_urls'] for a in t['affiliations'])]
                if len(matches)==1 and len(alias_targets[(record['roster_url'],record['name'])])==1:
                    row=matches[0];by_url[identity]=row
            if row and row['name']!=record['name']:
                coverage['gaps'].append('个人页姓名与旧身份冲突，保留旧记录：'+identity);coverage['roster_state']='partial';continue
            if row is None:
                row=dict(id=f'T{next_id:02}',name=record['name'],department=record['group'],title=record.get('title',''),research='',emails=[],office='',courses=[],notes='',
                    source_url=record['profile_url'] or record['roster_url'],affiliations=[],staff_type=record['staff_type'],flags=record['flags'],checked_on=part['checked_on'],profile_status='list_only')
                next_id+=1;result['teachers'].append(row);by_url[identity]=row
            aff=next((a for a in row['affiliations'] if a['college_id']==cid),None)
            if aff is None:aff=dict(college_id=cid,group_ids=[],source_urls=[]);row['affiliations'].append(aff)
            if gid not in aff['group_ids']:aff['group_ids'].append(gid)
            if record['roster_url'] not in aff['source_urls']:aff['source_urls'].append(record['roster_url'])
            if 'details' in record:
                provenance=row.setdefault('field_provenance',{})
                for k in ('title','research','emails','office','courses','notes'):
                    if row[k] and k not in provenance:provenance[k]=dict(checked_on=row['checked_on'],source_url=row['source_url'])
                for k,v in record['details'].items():
                    if v:
                        row[k]=v;provenance[k]=dict(checked_on=part['checked_on'],source_url=record['profile_url'] or record['roster_url'])
                if any(record['details'].values()):
                    row.update(profile_checked_on=part['checked_on'],profile_status='checked',
                        checked_on=min((p['checked_on'] for k,p in provenance.items() if row.get(k)),default=part['checked_on']))
        coverage['profile_checked_count']=sum(t['profile_status']=='checked' and t.get('profile_checked_on',t['checked_on'])==part['checked_on'] and any(a['college_id']==cid for a in t['affiliations']) for t in result['teachers'])
    return normalize_catalog(result,sources)


def publish_catalog(candidate,target,*,sources):
    candidate=normalize_catalog(candidate,sources);target=Path(target);path=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=target.parent,prefix='.faculty-',suffix='.tmp',delete=False) as file:
            path=Path(file.name);json.dump(candidate,file,ensure_ascii=False,indent=2);file.flush();os.fsync(file.fileno())
        os.replace(path,target)
    finally:
        if path and path.exists():path.unlink()
