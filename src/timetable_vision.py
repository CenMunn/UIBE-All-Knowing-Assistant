"""Vision-only adapter: private images in, unconfirmed structured draft out."""
import base64
from dataclasses import dataclass
import json
import logging
import time
from uuid import uuid4
from project_paths import TEXT_DIR
from timetable_models import RecognitionDraft
from timetable_rules import integer, text

LOGGER = logging.getLogger('timetable.vision')


@dataclass(frozen=True)
class VisionSettings:
    model: str = 'deepseek-flash'
    timeout_seconds: float = 60.0
    max_output_tokens: int = 16384
    verified_endpoint: bool = False


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj: raise ValueError('识别结果包含重复字段。')
        obj[key] = value
    return obj


def _depth(value, depth=0):
    if depth > 8: raise ValueError('识别结果嵌套过深。')
    if isinstance(value, dict):
        for item in value.values(): _depth(item, depth+1)
    elif isinstance(value, list):
        for item in value: _depth(item, depth+1)


def _keys(value, allowed):
    if not isinstance(value, dict) or set(value) != set(allowed):
        raise ValueError('识别结果字段不完整或含不支持的字段。')


def parse_recognition(content, *, image_ids, total_weeks):
    integer(total_weeks, 1, 30)
    if not isinstance(content, str) or len(content.encode('utf-8')) > 512*1024:
        raise ValueError('识别结果为空或超出长度限制。')
    value = content.strip()
    if value.startswith('```json\n') and value.endswith('\n```'):
        value = value[8:-4]
    try:
        def invalid_constant(_): raise ValueError('识别结果含无效数值。')
        data = json.loads(value, object_pairs_hook=_unique_object, parse_constant=invalid_constant)
        _depth(data)
        _keys(data, ('name','term','coverage','records','issues'))
        for key in ('name','term'):
            if data[key] is not None: text(data[key],80)
        if not isinstance(data['records'],list) or len(data['records']) > 300:
            raise ValueError('识别课程数量超出限制。')
        if not isinstance(data['coverage'],list) or len(data['coverage']) != len(image_ids):
            raise ValueError('识别结果缺少各图片的可见范围说明。')
        seen = set()
        for entry in data['coverage']:
            _keys(entry, ('source_id','description'))
            if entry['source_id'] not in image_ids or entry['source_id'] in seen: raise ValueError('图片来源编号无效。')
            text(entry['description'],1000,required=True); seen.add(entry['source_id'])
        allowed = ('title','code','section','teacher','location','weekday','start_period','end_period','weeks','source_ids','source_text')
        for row in data['records']:
            _keys(row, allowed)
            for key, maximum in [('title',120),('code',40),('section',40),('teacher',80),('location',120),('source_text',1000)]:
                if row[key] is not None: text(row[key],maximum)
            for key, high in [('weekday',7),('start_period',20),('end_period',20)]:
                if row[key] is not None: integer(row[key],1,high)
            if row['start_period'] is not None and row['end_period'] is not None and row['start_period'] > row['end_period']:
                raise ValueError('识别小节起止颠倒，请重新校对。')
            if row['weeks'] is not None:
                if not isinstance(row['weeks'],list) or not 1 <= len(row['weeks']) <= total_weeks:
                    raise ValueError('识别周次无效。')
                for w in row['weeks']: integer(w,1,total_weeks)
                row['weeks'] = sorted(set(row['weeks']))
            if not isinstance(row['source_ids'],list) or not row['source_ids'] or len(row['source_ids'])>4:
                raise ValueError('课程缺少图片来源。')
            if any(not isinstance(k,str) or k not in image_ids for k in row['source_ids']):
                raise ValueError('识别结果引用了本次之外的图片。')
        if not isinstance(data['issues'],list) or len(data['issues'])>500: raise ValueError('识别疑点数量超限。')
        for issue in data['issues']:
            _keys(issue, ('message','record_index')); text(issue['message'],1000,required=True)
            if issue['record_index'] is not None: integer(issue['record_index'],0,len(data['records'])-1)
        return RecognitionDraft(data['name'], data['term'], tuple(data['records']),tuple(data['coverage']),tuple(data['issues']))
    except (json.JSONDecodeError, RecursionError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError('图片识别未返回可校对的有效JSON，请重试或手动建立课表。') from exc


def recognize_timetable(client_factory, settings, images, *, total_weeks):
    request_id, started = uuid4().hex, time.monotonic()
    count, error_kind = 0, ''
    try:
        if not settings.verified_endpoint: raise ValueError('尚未确认当前模型服务支持图片，请先核对配置。')
        if not 1 <= len(images) <= 4 or sum(len(data) for _,data in images)>20*1024*1024:
            raise ValueError('图片数量或总大小超出限制。')
        blocks = [{'type':'text','text':'提取本学期课表。总教学周数上限：'+str(total_weeks)+'。图片编号按下列标注对应。'}]
        for key, data in images:
            blocks.append({'type':'text','text':'下一张图片的source_id为：'+key})
            blocks.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(data).decode('ascii'),'detail':'high'}})
        # One initial label is enough for single-image calls, without duplicating history.
        if len(images)==1:
            blocks[0]['text'] += ' 唯一source_id：'+images[0][0]
            del blocks[1]
        client = client_factory().with_options(timeout=settings.timeout_seconds, max_retries=0)
        result = client.chat.completions.create(model=settings.model,
            messages=[{'role':'system','content':(TEXT_DIR/'prompts/timetable_extract.txt').read_text(encoding='utf-8')},
                      {'role':'user','content':blocks}],
            stream=False, max_tokens=settings.max_output_tokens,
            response_format={'type':'json_object'}, extra_body={'thinking':{'type':'disabled'}})
        if not result.choices: raise ValueError('识别接口没有返回结果。')
        choice = result.choices[0]
        if choice.finish_reason != 'stop' or getattr(choice.message,'refusal',None) or not choice.message.content:
            raise ValueError('识别结果不完整或被拒绝，未保存课表。请重试或手动建立。')
        draft = parse_recognition(choice.message.content,image_ids=tuple(k for k,_ in images),total_weeks=total_weeks)
        count = len(draft.records)
        return draft
    except ValueError:
        error_kind = 'validation'
        raise
    except Exception as exc:
        name = type(exc).__name__
        error_kind = ('authentication' if 'Authentication' in name else 'rate_limit' if 'RateLimit' in name
            else 'timeout' if 'Timeout' in name else 'connection' if 'Connection' in name else 'provider')
        messages = {'authentication':'识别服务密钥校验失败，请检查本机配置。',
            'rate_limit':'识别请求受限，请稍后再试。','timeout':'图片识别超时，未保存课表。',
            'connection':'图片识别连接失败，请检查网络。','provider':'图片识别未完成，请核对服务配置或稍后重试。'}
        raise ValueError(messages[error_kind]) from None
    finally:
        LOGGER.info('request=%s model=%s seconds=%.3f count=%d error=%s', request_id,
            settings.model,time.monotonic()-started,count,error_kind)
