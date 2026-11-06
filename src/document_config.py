"""本地文档服务的独立配置；不加载模型或邮箱凭据。"""
from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from dotenv import dotenv_values


@dataclass(frozen=True)
class OfficeConfig:
    browser_base: str
    service_base: str
    callback_base: str
    jwt_secret: str = field(repr=False)


def _private_dir(project_dir: Path) -> Path:
    private = Path(project_dir).resolve() / '.private'
    if private.is_symlink() or private.resolve() != private:
        raise ValueError('文档私密配置目录不能使用符号链接或目录联接。')
    return private


def _local_host(host: str) -> bool:
    if host in {'localhost', 'host.docker.internal', 'documentserver', 'onlyoffice'}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7')
    return any(address in ipaddress.ip_network(network) for network in networks)


def validate_origins(config: OfficeConfig) -> None:
    for value in (config.browser_base, config.service_base, config.callback_base):
        try:
            parsed = urlsplit(value)
            valid = (isinstance(value, str) and not any(c.isspace() or ord(c) < 32 for c in value)
                     and parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
                     and _local_host(parsed.hostname) and parsed.username is None
                     and parsed.password is None and parsed.path in ('', '/')
                     and not parsed.query and not parsed.fragment and '\\' not in value
                     and (parsed.port is None or 1 <= parsed.port <= 65535))
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError('文档服务地址须为明确的本地服务地址，不能含路径、凭据或全网卡监听地址。')
    if (not isinstance(config.jwt_secret, str) or not 32 <= len(config.jwt_secret) <= 512
            or any(ord(c) < 32 for c in config.jwt_secret)):
        raise ValueError('请为文档服务配置至少32字符的独立随机JWT密钥。')


def read_document_config(project_dir: Path) -> OfficeConfig | None:
    path = _private_dir(project_dir) / 'documents.env'
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('文档配置文件不能使用链接。')
    if not path.exists():
        return None
    names = ('ONLYOFFICE_BROWSER_BASE', 'ONLYOFFICE_SERVICE_BASE',
             'ONLYOFFICE_CALLBACK_BASE', 'ONLYOFFICE_JWT_SECRET')
    try:
        values = dotenv_values(path, encoding='utf-8', interpolate=False)
    except (OSError, UnicodeError):
        raise ValueError('文档配置无法读取，请检查独立配置文件和权限。') from None
    if set(values) != set(names) or any(not values.get(name) for name in names):
        raise ValueError('文档配置不完整或含不支持的字段，请检查独立文档配置。')
    config = OfficeConfig(*(values[name] for name in names))
    validate_origins(config)
    return config


def load_file_access_key(project_dir: Path) -> bytes:
    private = _private_dir(project_dir)
    private.mkdir(mode=0o700, exist_ok=True)
    path = private / 'documents-access.key'
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('文档访问密钥文件不能使用链接。')
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(secrets.token_bytes(32))
            file.flush()
            os.fsync(file.fileno())
    with path.open('rb') as file:
        key = file.read(33)
    if len(key) != 32:
        raise ValueError('文档访问密钥无效；未自动覆盖，请检查文件或恢复备份。')
    return key
