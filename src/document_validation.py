"""本地文件边界校验；不运行附件、宏或远程资源。"""
from io import BytesIO
from pathlib import PurePosixPath
import warnings
from zipfile import ZipFile, BadZipFile
from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError
from PIL import Image, ImageOps

MAX_FILE = 10 * 1024 * 1024
MIMES = {'.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
         '.doc':'application/msword','.xls':'application/vnd.ms-excel','.pdf':'application/pdf',
         '.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'}


def office_parts(content: bytes, extension: str) -> dict[str, bytes]:
    try:
        with ZipFile(BytesIO(content)) as package:
            infos=package.infolist();names=[i.filename for i in infos]
            if (len(infos)>2000 or len(set(names))!=len(names)
                    or sum(i.file_size for i in infos)>100*1024*1024):
                raise ValueError('Office文件结构超出限制。')
            result={}
            for info in infos:
                name=info.filename; lowered=name.casefold()
                if (PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name or ':' in name
                        or info.flag_bits & 1 or info.file_size>20*1024*1024
                        or info.file_size>max(info.compress_size,1)*100
                        or any(x in lowered for x in ('vbaproject','/activex/','/embeddings/','externallinks/'))):
                    raise ValueError('附件含有不支持的活动内容或压缩结构。')
                data=package.read(info)
                if name.endswith(('.xml','.rels')):
                    node=fromstring(data)
                    # 按解码后的声明检查，不能依赖部件名或UTF-8字节匹配。
                    for el in node.iter():
                        declarations=(el.get('ContentType',''),el.get('Type',''))
                        if any(any(bad in value.casefold() for bad in
                                ('vba','macroenabled','macrosheet','activex','oleobject','attachedtemplate','externallink'))
                                for value in declarations):
                            raise ValueError('不支持含宏、嵌入对象或活动模板的附件。')
                        if any(bad in text.casefold() for text in (el.text or '',el.tail or '')
                               for bad in ('ddeauto','dde ')):
                            raise ValueError('不支持Office动态数据指令。')
                    if name.endswith('.rels'):
                        for rel in node:
                            if rel.get('TargetMode')=='External' and not rel.get('Type','').endswith('/hyperlink'):
                                raise ValueError('不支持含外部活动关系的附件。')
                    if any(bad in data.lower() for bad in (b'macroenabled',b'ddeauto',b'<!doctype',b'<!entity')):
                        raise ValueError('不支持此Office活动内容。')
                result[name]=data
            marker='word/document.xml' if extension=='.docx' else 'xl/workbook.xml'
            if marker not in result or '[Content_Types].xml' not in result:
                raise ValueError('文件扩展名与实际Office格式不一致。')
            return result
    except (BadZipFile, KeyError, RuntimeError, OSError, ParseError, DefusedXmlException) as exc:
        raise ValueError('Office文件损坏或无法安全读取。') from exc


def validate_photo(content: bytes) -> bytes:
    if not isinstance(content,bytes) or not content or len(content)>5*1024*1024:
        raise ValueError('照片须为不超过5MiB的PNG/JPEG。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as im:
                if im.format not in {'JPEG','PNG'} or max(im.size)>8192 or im.width*im.height>16_000_000:
                    raise ValueError('照片格式或像素超出限制。')
                im.load()
                normalized=ImageOps.exif_transpose(im).convert('RGB')
                out=BytesIO();normalized.save(out,format='PNG')
                if len(out.getvalue())>MAX_FILE:raise ValueError('照片解码后文件过大。')
                return out.getvalue()
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('照片无法安全读取。') from exc


def validate_upload(name: str, content: bytes) -> tuple[str,str]:
    if not isinstance(name,str) or not name or any(c in name for c in '/\\\x00\r\n:'):
        raise ValueError('文件名无效。')
    ext=PurePosixPath(name).suffix.casefold()
    if ext not in MIMES or not isinstance(content,bytes) or not 0<len(content)<=MAX_FILE:
        raise ValueError('文件类型不支持，或大小超出10MiB。')
    if ext in {'.docx','.xlsx'}:
        office_parts(content,ext)
    elif ext in {'.doc','.xls'}:
        import olefile
        if not olefile.isOleFile(BytesIO(content)):raise ValueError('旧Office格式无效。')
        with olefile.OleFileIO(BytesIO(content)) as ole:
            streams=['/'.join(p).casefold() for p in ole.listdir()]
            if any(any(x in s for x in ('vba','macros','objectpool','mbd','encrypted')) for s in streams):
                raise ValueError('旧Office附件含有活动内容。')
            if not any(s in streams for s in (['worddocument'] if ext=='.doc' else ['workbook','book'])):
                raise ValueError('扩展名与旧Office格式不一致。')
    elif ext=='.pdf':
        import pymupdf
        try:
            with pymupdf.open(stream=content,filetype='pdf') as pdf:
                if pdf.is_encrypted or pdf.embfile_count():raise ValueError('不接受加密或嵌入文件的PDF。')
                for i in range(1,pdf.xref_length()):
                    obj=pdf.xref_object(i)
                    if any(action in obj for action in ('/JavaScript','/JS ','/Launch','/RichMedia','/OpenAction')):
                        raise ValueError('不接受带活动内容的PDF。')
        except RuntimeError as exc:raise ValueError('PDF文件损坏。') from exc
    else:
        with Image.open(BytesIO(content)) as im:
            expected='PNG' if ext=='.png' else 'JPEG'
            if im.format!=expected:raise ValueError('图片扩展名与真实格式不一致。')
        validate_photo(content)
    return ext,MIMES[ext]
