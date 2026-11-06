"""与Gradio共用回环端口，不需要容器或Office服务。"""
from pathlib import Path
from contextlib import asynccontextmanager
import gradio as gr
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from document_config import load_file_access_key
from document_store import DocumentStore
from document_service import DocumentService
from document_routes import create_document_routes
from form_catalog import load_form_catalog


class DocumentRuntime:
    def __init__(self,project_dir: Path,origin: str):
        self.root=Path(project_dir)
        self.secret=load_file_access_key(self.root)
        self.service=DocumentService(DocumentStore(self.root/'data/documents'),self.root,load_form_catalog(self.root))
        self.router=create_document_routes(self.service,self.secret,origin)

    def close(self):self.service.store.close()


def create_local_web_app(blocks,runtime,*,server_port,extra_routers=()):
    from web_app import launch_options
    options=launch_options()
    parent=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    expected=f'127.0.0.1:{server_port}'
    @parent.middleware('http')
    async def local_only(request,call_next):
        if request.headers.get('host') not in {expected,f'localhost:{server_port}'}:
            return PlainTextResponse('请通过本机地址访问。',status_code=403)
        origin=request.headers.get('origin')
        if origin and origin not in {f'http://{expected}',f'http://localhost:{server_port}'}:
            return PlainTextResponse('不接受跨站请求。',status_code=403)
        return await call_next(request)
    if runtime:parent.include_router(runtime.router)
    for router in extra_routers:parent.include_router(router)
    from citation_links import create_source_routes
    from project_paths import PROJECT_DIR
    parent.include_router(create_source_routes(PROJECT_DIR))
    blocks.queue(max_size=32)
    return gr.mount_gradio_app(parent,blocks,path='/',server_name='127.0.0.1',server_port=server_port,
        show_error=False,allowed_paths=[],blocked_paths=options['blocked_paths'],max_file_size=10*1024*1024,
        footer_links=[],enable_monitoring=False,mcp_server=False,ssr_mode=False,theme=options['theme'],
        css_paths=options['css_paths'],css=options['css'],js=options['js'])


def run_local_web_app(app,*,server_port,on_started):
    import socket
    import uvicorn
    # 先核实端口可绑定，再允许提醒工作线程启动。
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        listener.bind(('127.0.0.1',server_port));listener.listen(128)
        original=app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            async with original(application):
                on_started()
                yield
        app.router.lifespan_context=lifespan
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=server_port,access_log=False))
        server.run(sockets=[listener])
    finally:listener.close()
