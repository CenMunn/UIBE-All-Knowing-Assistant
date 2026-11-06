"""逐轮官方 Search/Fetch 客户端。网页内容仍是不可信资料。"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time
from typing import Protocol, Callable
from urllib.parse import urlsplit, unquote
import httpx

class RequestBudget(Protocol):
    def remaining(self, stage: str) -> float: ...
    def check(self) -> None: ...
    def register_close(self, callback: Callable) -> None: ...

class SearchError(Exception):
    def __init__(self, code):
        self.code=code
        super().__init__('联网资料取得失败（'+code+'），未核实最新信息。')

@dataclass(frozen=True)
class WebPage:
    title: str
    url: str
    content: str
    published_on: str | None
    fetched_at: str

def public_url(url):
    try:
        u=urlsplit(url);host=u.hostname or '';path=unquote(u.path).lower()
        return (isinstance(url,str) and len(url)<=2048 and not re.search(r'[\s\\]',url) and
                u.scheme=='https' and (host=='uibe.edu.cn' or host.endswith('.uibe.edu.cn')) and
                u.port in (None,443) and not u.username and not u.password and not u.query and not u.fragment and
                host not in {'cas.uibe.edu.cn','i.uibe.edu.cn','bkjw.uibe.edu.cn','tas1.uibe.edu.cn'} and
                not re.search(r'login|signin|cas/|jwapp|tp_up|private|/user/|/admin/|/download',path))
    except (TypeError,ValueError):return False

def page_heading(title):
    """仅去掉已知本站名称装饰，统一主题筛选与正文身份校验。"""
    heading=re.sub(r'^\s*对外经济贸易大学\s*[-_—–|｜]?\s*', '', title)
    return re.sub(r'\s*[-_—–|｜]\s*对外经济贸易大学\s*$', '', heading)


def _body_ok(title,content):
    if not content:return True  # 无正文只作为待Fetch导航，不能入模。
    if re.search(r'统一身份认证|请输入.*(?:密码|账号)|<form|type=["\']password',content[:5000],re.I):return False
    clean=lambda s:re.sub(r'[^\w\u4e00-\u9fff]','',s).casefold()
    # 官网页标题可能带站点后缀，正文通常只保留栏目名；仍须匹配真实标题而非任意正文。
    a,b=clean(page_heading(title)),clean(content)
    return bool(a and (a in b or a[:min(8,len(a))] in b))

class SearchClient:
    def __init__(self, api_key, *, transport=None):
        self._http=httpx.Client(headers={'Authorization':'Bearer '+api_key},transport=transport,follow_redirects=False)
        self.search_requests=0
        self.fetch_requests=0
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def close(self):self._http.close()
    def _request(self,kind,payload,control):
        control.register_close(self.close)
        for attempt in range(2):
            control.check()
            field=kind+'_requests'
            if getattr(self,field)>=2:raise SearchError('request_limit')
            left=control.remaining('search')
            if left<=0:raise SearchError('search_timeout')
            setattr(self,field,getattr(self,field)+1)
            try:
                with self._http.stream('POST','https://api.deepseek.com/v1/tools/'+kind,json=payload,
                        timeout=httpx.Timeout(min(10,left),connect=min(5,left))) as response:
                    if response.status_code!=200:
                        code=response.status_code
                        if attempt==0 and (code in (408,429) or 500<=code<=599):
                            left=control.remaining('search')
                            if left<=.15:raise SearchError('search_timeout')
                            time.sleep(min(.15,left/2))
                            control.check()
                            continue
                        raise SearchError('http_'+str(code))
                    chunks=[];size=0
                    for chunk in response.iter_bytes():
                        control.check();control.remaining('search')
                        size+=len(chunk)
                        if size>1048576:raise SearchError('response_too_large')
                        chunks.append(chunk)
                    import json
                    data=json.loads(b''.join(chunks))
                    if not isinstance(data,dict):raise SearchError('invalid_response')
                    return data
            except SearchError:raise
            except (httpx.HTTPError,ValueError,TypeError):raise SearchError('transport_or_format') from None
        raise SearchError('request_limit')
    def search(self,query,control):
        if not isinstance(query,str) or not query.strip() or len(query)>128:raise SearchError('invalid_query')
        data=self._request('search',dict(query=query,count=5,need_content=True),control)
        rows=data.get('results')
        if not isinstance(rows,list):raise SearchError('invalid_response')
        pages=[]
        for row in rows[:5]:
            if not isinstance(row,dict):continue
            title,url,content=row.get('title'),row.get('url'),row.get('content','')
            date=row.get('date')
            if (not isinstance(title,str) or not title.strip() or len(title)>600 or not public_url(url) or
                not isinstance(content,str) or (date is not None and not isinstance(date,str)) or not _body_ok(title,content)):continue
            pages.append(WebPage(title,url,content,date,datetime.now(timezone.utc).isoformat()))
        return pages
    def fetch(self,page,control):
        if not public_url(page.url):raise SearchError('blocked_url')
        data=self._request('fetch',dict(url=page.url),control)
        title,content=data.get('title'),data.get('content')
        if (not isinstance(title,str) or title.strip()!=page.title.strip() or not isinstance(content,str) or
                not content.strip() or not _body_ok(title,content)):raise SearchError('page_identity')
        return WebPage(title,page.url,content,page.published_on,datetime.now(timezone.utc).isoformat())
