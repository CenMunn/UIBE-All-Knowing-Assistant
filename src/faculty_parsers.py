"""Parse only explicitly registered faculty containers, never campus news."""
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup


def person_label(text):
    text=re.sub(r'[（(].*?[）)]','',text)
    text=re.sub(r'^(系主任|副主任|主任)\s*[:：]','',text)
    text=re.sub(r'^(?:特聘教授|客座研究员|客座教授|双聘教师|兼职教授|外聘教授)[\s:：]*','',text.strip())
    text=re.split(r'(?:副?教授|副?研究员|讲\s*师|教\s*授|助理教授|助教)',text)[0]
    latin=' '.join(text.split()).strip('·•：:')
    if re.fullmatch(r"[A-Za-z][A-Za-z .’'\-]{1,79}",latin) and latin.casefold() not in {'professor','teacher','faculty'}:return latin
    bilingual=re.match(r'^([\u4e00-\u9fff·]{2,6})\s+[A-Za-z]',text.strip())
    if bilingual:text=bilingual[1]
    text=re.sub(r'\s+','',text).strip('·•：:')
    if text in {'特聘','客座','双聘','兼职','外聘','专任','荣休'}:return ''
    return text if re.fullmatch(r'[\u4e00-\u9fff·]{2,6}',text) else ''


def parse_roster(html,url,source):
    soup=BeautifulSoup(html,'html.parser');records=[]
    for container in soup.select(source['selector']):
        label=container.select_one(source['label_selector']) if source.get('label_selector') else container
        if label is None:continue
        text=label.get_text(' ',strip=True);name=person_label(text)
        if not name:continue
        a=container if container.name=='a' else container.select_one('a[href]')
        if a is None:a=container
        href=a.get('href','')
        profile=urljoin(url,href) if href and not href.startswith(('#','javascript:')) else ''
        if profile and (profile.endswith('/') or re.search(r'/index\d*\.htm$',profile)):profile=''
        title=re.search(r'(助理教授|副教授|教授|副研究员|研究员|讲\s*师|教\s*授|助教)',text)
        records.append(dict(name=name,roster_url=url,profile_url=profile,group=source.get('group','官网未公布专业／系所'),
            kind=source.get('kind','unspecified'),title=re.sub(r'\s+','',title[0]) if title else '',
            staff_type=source.get('staff_type','unspecified'),flags=source.get('flags',[])))
    next_urls=[urljoin(url,a['href']) for a in soup.select(source.get('next_selector','a.__no_pagination__')) if a.get('href')]
    return dict(records=records,next_urls=list(dict.fromkeys(next_urls)))


def parse_profile(html,url):
    soup=BeautifulSoup(html,'html.parser')
    node=soup.select_one('#ViewTeacher1_pnlEmail')
    if node is not None:
        result=dict(emails=[],research='',title='',office='',courses=[],notes='')
        mapping={'emails':'#ViewTeacher1_lblEmail','title':'#ViewTeacher1_lblZhiCheng','research':'#ViewTeacher1_lblYanJiuFangXiang','office':'#ViewTeacher1_lblBanGongShi'}
        for key,selector in mapping.items():
            tag=soup.select_one(selector)
            if tag:
                text=tag.get_text(' ',strip=True)
                result[key]=list(dict.fromkeys(re.findall(r'[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}',text))) if key=='emails' else text[:300]
        return result
    node=soup.select_one('article .article, .article, .article-content, .v_news_content, .con_txt, .teacherInfo, .teacher-info, .gp-article, .gp-article-detail, article')
    result=dict(emails=[],research='',title='',office='',courses=[],notes='')
    if node is None:return result
    for tag in node.select('script,style,nav,footer'):tag.decompose()
    text=node.get_text('\n',strip=True)
    result['emails']=list(dict.fromkeys(re.findall(r'[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}',text)))
    blocks=[p.get_text(' ',strip=True) for p in node.select('p,tr,li')]+text.splitlines()
    for key,label,limit in [('research','研究方向|研究领域',300),('office','办公地点|办公室',100)]:
        for line in blocks:
            found=re.match(r'^(?:'+label+r')[ \t\u3000]*[:：][ \t\u3000]*([^\r\n]{2,'+str(limit)+'})',line.strip())
            if found:
                value=re.split(r'(?:电子邮箱|电子邮件|联系电话|办公电话|研究方向|研究领域|职称)[ \t]*[:：]',found[1])[0].strip()
                if value and not re.match(r'^(?:邮箱|电话|电子|职称|研究|https?://)',value) and not (key=='office' and ('@' in value or re.fullmatch(r'[+\d()\- ]{7,}',value))):
                    result[key]=value;break
    title=re.search(r'(?:职称|职\s*称)\s*[:：]?\s*(副教授|教授|讲师|助理教授|研究员|副研究员)',text)
    if title:result['title']=title[1]
    return result
