"""Private image preview: no client-supplied filesystem paths."""
from urllib.parse import urlparse
from fastapi import APIRouter, Request
from fastapi.responses import Response, PlainTextResponse


def create_timetable_routes(assets, resolve_access):
    router = APIRouter()

    @router.get('/timetable-images/{image_id}')
    def preview(image_id: str, request: Request, token: str = ''):
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return PlainTextResponse('不接受跨站预览。', status_code=403)
        try:
            content = assets.verify_ticket(image_id, token, resolve_access)
            return Response(content, media_type='image/png', headers={
                'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
                'Content-Security-Policy':"default-src 'none'"})
        except ValueError:
            return PlainTextResponse('预览已失效，请重新打开课表。', status_code=403,
                                     headers={'Cache-Control':'no-store'})
    return router
