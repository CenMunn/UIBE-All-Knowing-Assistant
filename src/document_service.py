"""已校对模板与个人文件的业务编排。"""
from hashlib import sha256
from pathlib import Path
from form_catalog import get_form
from form_export import export_form,validate_fields
from document_validation import validate_upload, validate_photo
from document_models import DocumentError
from form_layout import validate_layout, default_layout


class DocumentService:
    def __init__(self,store,root,catalog):
        self.store=store;self.root=Path(root);self.catalog=catalog

    def template(self,template_id):return get_form(self.catalog,template_id)

    def create_from_template(self,template_id,name,nonce):
        form=get_form(self.catalog,template_id,require_fillable=True)
        content=export_form(self.root,form,{})
        ext='.'+form['format']
        if not name.casefold().endswith(ext):name+=ext
        return self.store.create(name,content,nonce=nonce,template_id=form['id'],template_version=form['template_version'],
            fields=validate_fields(form,{})|{'__layout':default_layout()})

    def save_form(self,ref,values,photo,operation_id,*,writer_token=None,layout=None):
        record=self.store.get(ref)
        form=get_form(self.catalog,record.template_id,require_fillable=True)
        if record.template_version!=form['template_version']:raise DocumentError('此个人副本使用的模板版本已改变，请保留下载文件并联系管理员核对。')
        values=validate_fields(form,values)
        if layout is None:layout=self.store.form_data(ref)['fields'].get('__layout')
        layout=validate_layout(form,layout)
        photo=validate_photo(photo) if photo is not None else None
        content=export_form(self.root,form,values,photo,layout=layout)
        return self.store.save(ref,content,fields=values|{'__layout':layout},photo=photo,operation_id=operation_id,writer_token=writer_token)

    def import_files(self,items,nonce):
        if not isinstance(items,tuple) or not 1<=len(items)<=10:raise DocumentError('每次请选择1—10个文件。')
        prepared=[]
        for name,content in items:
            validate_upload(name,content)
            prepared.append(dict(name=name,content=content,fields={}))
        return self.store.create_batch(tuple(prepared),nonce)
