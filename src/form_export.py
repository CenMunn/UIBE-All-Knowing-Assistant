"""只写经校对的字段位置，保留原包中其他成员。"""
from copy import deepcopy
from io import BytesIO
import math
import re
import unicodedata
from zipfile import ZipFile
from lxml import etree as E
from PIL import Image

from form_catalog import validate_form, validate_resource_path
from document_validation import office_parts, validate_photo, validate_upload
from form_layout import validate_layout

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
S='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P='http://schemas.openxmlformats.org/package/2006/relationships'
C='http://schemas.openxmlformats.org/package/2006/content-types'


def xml(data):
    return E.fromstring(data,E.XMLParser(resolve_entities=False,no_network=True,load_dtd=False))


def serialize(node):return E.tostring(node,encoding='UTF-8',xml_declaration=True,standalone=True)


def validate_fields(form: dict, values: dict) -> dict:
    if not isinstance(values,dict):raise ValueError('填写内容格式无效。')
    fields={f['id']:f for f in form['fields']}
    if set(values)-set(fields):raise ValueError('含有不允许填写的字段。')
    cleaned={}
    for key,f in fields.items():
        value=values.get(key,'')
        if value is None:value=''
        if f['kind']=='number' and isinstance(value,(int,float)) and not isinstance(value,bool):value=str(value)
        if not isinstance(value,str) or any(ord(ch)<32 and ch not in '\n\t' for ch in value):
            raise ValueError(f'请检查“{f["label"]}”的文字。')
        if f['kind']!='multiline' and any(c in value for c in '\r\n\t'):
            raise ValueError(f'“{f["label"]}”只接受单行。')
        if len(value)>f['max_chars']:
            raise ValueError(f'“{f["label"]}”超过{f["max_chars"]}字符，未自动截断。')
        if value and f['kind']=='choice' and value not in f.get('options',[]):raise ValueError('选项无效。')
        if value and f['kind']=='date' and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('日期请使用YYYY-MM-DD。')
        if value and f['kind']=='number':
            try:numeric=float(value)
            except ValueError:raise ValueError('请填写数字。') from None
            if not math.isfinite(numeric) or not f.get('min',-1e12)<=numeric<=f.get('max',1e12):raise ValueError('数字超出范围。')
        cleaned[key]=value
    if sum(map(len,cleaned.values()))>20000:raise ValueError('整份表格文字超出限制。')
    return cleaned


def doc_cell(document,target):
    try:
        table=document.findall('.//{'+W+'}body/{'+W+'}tbl')[target['table']]
        return table.findall('{'+W+'}tr')[target['row']].findall('{'+W+'}tc')[target['cell']]
    except (IndexError, KeyError):raise ValueError('原模板表格位置改变，请重新校对。') from None


def put_paragraph(paragraph,text,font=None,size=12):
    run=paragraph.find('{'+W+'}r')
    rpr=deepcopy(run.find('{'+W+'}rPr')) if run is not None and run.find('{'+W+'}rPr') is not None else None
    for child in list(paragraph):
        if child.tag!='{'+W+'}pPr':paragraph.remove(child)
    run=E.SubElement(paragraph,'{'+W+'}r')
    if rpr is not None:run.append(rpr)
    if font:
        if rpr is None:rpr=E.SubElement(run,'{'+W+'}rPr')
        for tag in ('rFonts','sz','szCs','b','bCs'):
            for el in rpr.findall('{'+W+'}'+tag):rpr.remove(el)
        E.SubElement(rpr,'{'+W+'}rFonts',{'{'+W+'}eastAsia':font,'{'+W+'}ascii':font,'{'+W+'}hAnsi':font})
        E.SubElement(rpr,'{'+W+'}sz',{'{'+W+'}val':str(int(size*2))})
        E.SubElement(rpr,'{'+W+'}szCs',{'{'+W+'}val':str(int(size*2))})
        E.SubElement(rpr,'{'+W+'}b',{'{'+W+'}val':'0'})
    for i,line in enumerate(text.split('\n')):
        if i:E.SubElement(run,'{'+W+'}br')
        E.SubElement(run,'{'+W+'}t',{'{http://www.w3.org/XML/1998/namespace}space':'preserve'}).text=line


def put_doc_photo(parts,document,slot,photo):
    from docx import Document
    from docx.shared import Mm
    with Image.open(BytesIO(photo)) as im:
        scale=min(slot['width_mm']/im.width,slot['height_mm']/im.height)
        width,height=im.width*scale,im.height*scale
    generated=Document();run=generated.add_paragraph().add_run()
    run.add_picture(BytesIO(photo),width=Mm(width),height=Mm(height))
    drawing=deepcopy(run._r.find('{'+W+'}drawing'))
    relpart='word/_rels/document.xml.rels'
    rels=xml(parts[relpart]);ids={r.get('Id') for r in rels}
    index=1
    while f'rIdFormPhoto{index}' in ids:index+=1
    rid=f'rIdFormPhoto{index}'
    for node in drawing.iter():
        if '{'+R+'}embed' in node.attrib:node.set('{'+R+'}embed',rid)
        if node.tag.endswith('}docPr'):node.set('id','900001');node.set('name','个人照片')
    cell=doc_cell(document,slot);paragraph=cell.findall('{'+W+'}p')[0]
    put_paragraph(paragraph,'')
    E.SubElement(paragraph,'{'+W+'}r').append(drawing)
    media='word/media/form-photo.png'
    if media in parts:raise ValueError('模板照片资源名称冲突。')
    parts[media]=photo
    E.SubElement(rels,'{'+P+'}Relationship',Id=rid,Type=R+'/image',Target='media/form-photo.png')
    parts[relpart]=serialize(rels)
    types=xml(parts['[Content_Types].xml'])
    if not any(c.get('Extension')=='png' for c in types):E.SubElement(types,'{'+C+'}Default',Extension='png',ContentType='image/png')
    parts['[Content_Types].xml']=serialize(types)


def export_form(root,form: dict,values: dict,photo: bytes | None=None,*,layout=None) -> bytes:
    validate_form(root,form)
    if form['status']!='fillable':raise ValueError('模板暂不支持填写。')
    values=validate_fields(form,values)
    layout=validate_layout(form,layout)
    source=validate_resource_path(root,form['adapted_file']).read_bytes()
    parts=office_parts(source,'.'+form['format'])
    if photo is not None:
        if not form.get('photo'):raise ValueError('本表没有照片填写位置。')
        photo=validate_photo(photo)
    if form['format']=='docx':
        document=xml(parts['word/document.xml'])
        body_slots={}
        def apply_style(paragraph,style):
            ppr=paragraph.find('{'+W+'}pPr')
            if ppr is None:ppr=E.SubElement(paragraph,'{'+W+'}pPr')
            for tag in ('snapToGrid','spacing'):
                for old in ppr.findall('{'+W+'}'+tag):ppr.remove(old)
            E.SubElement(ppr,'{'+W+'}snapToGrid',{'{'+W+'}val':'0'})
            E.SubElement(ppr,'{'+W+'}spacing',{'{'+W+'}before':'0','{'+W+'}after':'0',
                '{'+W+'}line':str(round(style['line_spacing']*240)),'{'+W+'}lineRule':'auto'})
            if style['alignment']!='original':
                for old in ppr.findall('{'+W+'}jc'):ppr.remove(old)
                E.SubElement(ppr,'{'+W+'}jc',{'{'+W+'}val':style['alignment']})
        for field in form['fields']:
            target=field['target'];style=layout['defaults']|layout['fields'].get(field['id'],{})
            if 'paragraph' in target:
                body_slots.setdefault(target['paragraph'],[]).append((target['slot'],target['prefix']+values[field['id']],style))
                continue
            cell=doc_cell(document,target);paragraphs=cell.findall('{'+W+'}p')
            if layout['grow_rows'] and values[field['id']]:
                row=cell.getparent();height=row.find('{'+W+'}trPr/{'+W+'}trHeight')
                width=cell.find('{'+W+'}tcPr/{'+W+'}tcW')
                room=max(1,int(width.get('{'+W+'}w','2000'))/20-10.8) if width is not None else 80
                text=target.get('prefix','')+values[field['id']]
                # 只用于保守判断是否放开行高；实际换行与增高仍由Word排版。
                longest=max(sum(0 if unicodedata.combining(ch) else (1 if unicodedata.east_asian_width(ch) in 'WF' else .55)
                    for ch in line)*style['font_size'] for line in text.split('\n'))
                needed=style['font_size']*style['line_spacing']*1.25
                too_tight=height is not None and needed>int(height.get('{'+W+'}val','0'))/20
                if height is not None and height.get('{'+W+'}hRule')=='exact' and (longest>room or '\n' in text or too_tight):
                    height.set('{'+W+'}hRule','atLeast')
            for j,index in enumerate(target['paragraphs']):
                if index>=len(paragraphs):raise ValueError('模板段落位置改变。')
                text=target.get('prefix','')+values[field['id']] if j==0 else ''
                put_paragraph(paragraphs[index],text,font=form.get('fill_font') if text else None,size=style['font_size'])
                if j==0:apply_style(paragraphs[index],style)
                if j>0 and not text:
                    ppr=paragraphs[index].find('{'+W+'}pPr')
                    if ppr is None:ppr=E.SubElement(paragraphs[index],'{'+W+'}pPr')
                    spacing=ppr.find('{'+W+'}spacing')
                    if spacing is None:spacing=E.SubElement(ppr,'{'+W+'}spacing')
                    for key,value in {'before':'0','after':'0','line':'1','lineRule':'exact'}.items():spacing.set('{'+W+'}'+key,value)
        for index,slots in body_slots.items():
            paragraphs=document.findall('{'+W+'}body/{'+W+'}p')
            if index>=len(paragraphs):raise ValueError('模板表头位置已改变。')
            slots.sort(key=lambda s:s[0]);p=paragraphs[index]
            put_paragraph(p,'')
            for i,(_,text,style) in enumerate(slots):
                if i:E.SubElement(E.SubElement(p,'{'+W+'}r'),'{'+W+'}tab')
                fragment=E.Element('{'+W+'}p')
                put_paragraph(fragment,text,font=form.get('fill_font'),size=style['font_size'])
                for run in list(fragment):p.append(run)
            apply_style(p,layout['defaults'])
        if photo:put_doc_photo(parts,document,form['photo'],photo)
        parts['word/document.xml']=serialize(document)
    else:
        sheets={}
        for field in form['fields']:
            target=field['target'];part=target['part'];sheet=sheets.setdefault(part,xml(parts[part]))
            data=sheet.find('{'+S+'}sheetData');address=target['cell']
            rownum=re.search(r'\d+',address).group();row=data.find('{'+S+'}row[@r="'+rownum+'"]')
            if row is None:raise ValueError('模板行缺失，请重新校对。')
            cell=row.find('{'+S+'}c[@r="'+address+'"]')
            if cell is None:
                cell=E.SubElement(row,'{'+S+'}c',r=address)
                children=list(row);children.sort(key=lambda c:(len(re.sub(r'\d','',c.get('r',''))),re.sub(r'\d','',c.get('r',''))))
                row[:]=children
            if cell.find('{'+S+'}f') is not None:raise ValueError('不能改写公式单元格。')
            for child in list(cell):
                if child.tag in {'{'+S+'}v','{'+S+'}is'}:cell.remove(child)
            value=values[field['id']];value=target.get('prefix','')+value if value else target.get('prefix','')
            if field['kind']=='number' and value:
                cell.set('t','n');E.SubElement(cell,'{'+S+'}v').text=value
            else:
                cell.set('t','inlineStr');inline=E.SubElement(cell,'{'+S+'}is')
                E.SubElement(inline,'{'+S+'}t',{'{http://www.w3.org/XML/1998/namespace}space':'preserve'}).text=value
        for part,sheet in sheets.items():parts[part]=serialize(sheet)
    out=BytesIO()
    with ZipFile(BytesIO(source)) as before,ZipFile(out,'w') as after:
        for info in before.infolist():after.writestr(info,parts.pop(info.filename))
        for name,data in parts.items():after.writestr(name,data)
    result=out.getvalue();validate_upload('表格.'+form['format'],result)
    return result
