"""个人文件的版本引用，不把显示名当作磁盘位置。"""
from dataclasses import dataclass


class DocumentError(ValueError):
    pass


class DocumentConflict(DocumentError):
    pass


@dataclass(frozen=True)
class FileRef:
    id: str
    version: int
    revision: int


@dataclass(frozen=True)
class FileRecord:
    ref: FileRef
    name: str
    media_type: str
    size: int
    digest: str
    template_id: str | None
    template_version: str | None
    deleted: bool = False
