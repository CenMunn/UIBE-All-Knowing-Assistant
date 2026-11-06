"""同源、短期能力限定的文件读取接口；不接受磁盘路径。"""
import base64
from dataclasses import asdict
from hashlib import sha256
import hmac
import json
import time
from urllib.parse import quote, urlsplit
from fastapi import APIRouter,HTTPException,Request,Response
from document_models import FileRef,DocumentConflict
from document_validation import validate_upload
from form_catalog import get_form,validate_resource_path
from document_preview import docx_preview_page, ASSETS


def issue_document_token(secret,ref,purpose,expires_in=300):
    payload=dict(ref=asdict(ref) if isinstance(ref,FileRef) else ref,purpose=purpose,expires=int(time.time())+min(expires_in,300))
    body=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).decode().rstrip('=')
    return body+'.'+hmac.new(secret,body.encode(),sha256).hexdigest()


def verify_document_token(secret,token):
    try:
        if not isinstance(token,str) or len(token)>2000:raise ValueError
        body,signature=token.split('.')
        if not hmac.compare_digest(signature,hmac.new(secret,body.encode(),sha256).hexdigest()):raise ValueError
        payload=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        if payload['expires']<=time.time() or payload['purpose'] not in {'download','file-preview','template-original','template-preview'}:raise ValueError
        return payload
    except (ValueError,KeyError,TypeError):raise HTTPException(403,'文件链接已过期或无效，请重新点击下载。') from None


def create_document_routes(service,secret,origin):
    router=APIRouter(prefix='/documents');expected=urlsplit(origin)
    @router.get('/assets/{name}')
    def asset(request: Request,name: str):
        host=urlsplit('http://'+request.headers.get('host',''))
        if host.hostname!=expected.hostname or host.port!=expected.port:raise HTTPException(403,'仅限本机访问。')
        if name not in ASSETS:raise HTTPException(404,'资源不存在。')
        path=service.root/'resources'/('docx-reader.js' if name=='docx-reader.js' else 'vendor/'+name)
        try:data=path.read_bytes()
        except OSError:raise HTTPException(503,'预览组件缺失，请检查resources/vendor。') from None
        return Response(data,media_type='text/javascript',headers={'X-Content-Type-Options':'nosniff','Cache-Control':'public, max-age=3600'})
    @router.get('/content')
    def content(request: Request,token: str):
        host=urlsplit('http://'+request.headers.get('host',''))
        if (host.hostname!=expected.hostname or host.port!=expected.port
                or request.headers.get('origin',origin).rstrip('/')!=origin.rstrip('/')
                or request.headers.get('sec-fetch-site')=='cross-site'):
            raise HTTPException(403,'请从本机系统页面访问文件。')
        payload=verify_document_token(secret,token)
        try:
            purpose=payload['purpose']
            if purpose in {'download','file-preview'}:
                ref=FileRef(**payload['ref']);record=service.store.get(ref)
                data=service.store.read(ref);name=record.name;media=record.media_type;inline=False
                if purpose=='file-preview':
                    if media=='application/vnd.openxmlformats-officedocument.wordprocessingml.document':
                        validate_upload(name,data)
                        html,csp=docx_preview_page(name,data)
                        return Response(html,media_type='text/html',headers={'Cache-Control':'private, no-store',
                            'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Content-Security-Policy':csp})
                    if media not in {'application/pdf','image/png','image/jpeg'}:raise ValueError('此类型请下载查看。')
                    inline=True
            else:
                form=get_form(service.catalog,payload['ref'])
                file=form['source_file'] if purpose=='template-original' else form['preview'].get('file')
                if not file:raise ValueError('此模板暂无原样预览，请下载原件。')
                path=validate_resource_path(service.root,file);data=path.read_bytes()
                name=form['original_name'] if purpose=='template-original' else form['id']+'.pdf'
                _,media=validate_upload(name,data);inline=purpose=='template-preview'
            mode='inline' if inline else 'attachment'
            return Response(data,media_type=media,headers={'Content-Disposition':mode+"; filename*=UTF-8''"+quote(name,safe=''),
                'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store',
                'Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; frame-ancestors 'self'; sandbox"})
        except DocumentConflict as exc:raise HTTPException(409,str(exc)) from None
        except (ValueError,TypeError,KeyError,OSError):raise HTTPException(400,'文件不可用，请刷新后重新选择；原文件未删除。') from None
    return router
