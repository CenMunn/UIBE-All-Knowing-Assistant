"""只读诊断Docker/WSL和本机资源；不安装、启动或改变系统设置。"""
import errno
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess

from project_paths import PROJECT_DIR

GIB = 1024 ** 3


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, timeout=8, check=False,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def _decode(value: bytes) -> str:
    return value.decode('utf-16le' if b'\x00' in value else 'utf-8-sig', errors='replace')


def find_docker() -> str | None:
    command = shutil.which('docker')
    if command:
        return command
    candidates = [Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Docker/Docker/resources/bin/docker.exe']
    if os.environ.get('LOCALAPPDATA'):
        candidates.append(Path(os.environ['LOCALAPPDATA']) / 'Programs/DockerDesktop/resources/bin/docker.exe')
    return next((str(path) for path in candidates if path.is_file()), None)


def probe_docker(command: str | None) -> dict:
    if not command:
        return {'status': 'missing'}
    try:
        result = _run([command, 'version', '--format', '{{json .}}'])
        if result.returncode != 0:
            return {'status': 'unavailable'}
        data = json.loads(_decode(result.stdout))
        server = data.get('Server')
        if not isinstance(server, dict):
            return {'status': 'unavailable'}
        version = server.get('Version', '')
        if not isinstance(version, str) or not re.fullmatch(r'[a-zA-Z0-9.+_-]{1,48}', version):
            return {'status': 'unknown'}
        if server.get('Os') != 'linux':
            return {'status': 'wrong_engine', 'version': version}
        return {'status': 'available', 'version': version}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        return {'status': 'unknown'}


def probe_wsl() -> dict:
    command = shutil.which('wsl')
    if not command:
        return {'status': 'missing'}
    try:
        result = _run([command, '--version'])
        if result.returncode != 0:
            return {'status': 'unavailable'}
        match = re.search(r'WSL\s*(?:版本|version)\s*[:：]\s*(\d+(?:\.\d+){2,3})', _decode(result.stdout), re.I)
        if not match:
            return {'status': 'unknown'}
        version = match.group(1)
        status = 'available' if tuple(map(int, version.split('.')[:3])) >= (2, 1, 5) else 'outdated'
        return {'status': status, 'version': version}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'status': 'unknown'}


def probe_system() -> dict:
    unknown = {'memory_bytes': None, 'virtualization': None}
    command = shutil.which('powershell.exe')
    if platform.system() != 'Windows' or not command:
        return unknown
    script = ("$ErrorActionPreference='Stop'; "
              "$sys=Get-CimInstance Win32_ComputerSystem; "
              "$cpu=@(Get-CimInstance Win32_Processor); "
              "[pscustomobject]@{memory_bytes=[long]$sys.TotalPhysicalMemory; "
              "hypervisor_present=$sys.HypervisorPresent; "
              "virtualization=$(if ($cpu.Count -eq 0 -or "
              "@($cpu | Where-Object {$null -eq $_.VirtualizationFirmwareEnabled}).Count -gt 0) {$null} "
              "else {@($cpu | Where-Object {$_.VirtualizationFirmwareEnabled -ne $true}).Count -eq 0})} "
              "| ConvertTo-Json -Compress")
    try:
        result = _run([command, '-NoProfile', '-NonInteractive', '-Command', script])
        if result.returncode != 0:
            return unknown
        data = json.loads(_decode(result.stdout))
        memory = data.get('memory_bytes')
        virtual = data.get('virtualization')
        if virtual is False and data.get('hypervisor_present') is True:
            virtual = None
        return {'memory_bytes': memory if type(memory) is int and memory > 0 else None,
                'virtualization': virtual if type(virtual) is bool else None}
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return unknown


def probe_port(port: int) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(.3)
            result = connection.connect_ex(('127.0.0.1', port))
        if result == 0:
            return 'in_use'
        return 'free' if result in {errno.ECONNREFUSED, 10061} else 'unknown'
    except OSError:
        return 'unknown'


def inspect_environment(project_dir: Path) -> dict:
    docker = probe_docker(find_docker())
    wsl = probe_wsl()
    system = probe_system()
    memory = system.get('memory_bytes')
    virtual = system.get('virtualization')
    try:
        free_bytes = shutil.disk_usage(project_dir).free
    except OSError:
        free_bytes = None
    ports = {str(port): probe_port(port) for port in (7861, 7862, 8082)}
    actions = []
    if docker['status'] == 'missing':
        actions.append('请由用户安装并启动Docker Desktop，使用Linux容器；本诊断不安装软件或接受许可。')
    elif docker['status'] == 'wrong_engine':
        actions.append('请在Docker Desktop切换到Linux容器。')
    elif docker['status'] != 'available':
        actions.append('请启动Docker Desktop并等待引擎运行，然后重新诊断。')
    if wsl['status'] != 'available':
        actions.append('WSL2未确认满足要求；请查看wsl --version，需2.1.5或更高，系统更新或重启由用户确认。')
    if virtual is not True:
        actions.append('硬件虚拟化尚未确认启用；请在任务管理器CPU页面核对，不自动改变BIOS或Windows功能。')
    if memory is None or memory < 8 * GIB:
        actions.append('主机内存未确认达到Docker Desktop所需8GiB；请核对可分配资源。')
    if free_bytes is None or free_bytes < 40 * GIB:
        actions.append('项目所在磁盘未确认有40GiB可用空间；还需核对Docker镜像实际存储磁盘。')
    for port in ('7862', '8082'):
        if ports[port] != 'free':
            actions.append(f'{port}端口未确认空闲；请检查占用或改用明确端口，不自动结束其他程序。')
    return {'docker': docker, 'wsl': wsl,
            'virtualization': {'status': 'available' if virtual is True else 'disabled' if virtual is False else 'unknown'},
            'memory': {'bytes': memory, 'status': 'unknown' if memory is None else 'available' if memory >= 8 * GIB else 'insufficient'},
            'project_disk': {'free_bytes': free_bytes, 'status': 'unknown' if free_bytes is None else 'available' if free_bytes >= 40 * GIB else 'insufficient'},
            'ports': ports, 'prerequisites_met': not actions,
            'office_ready': False, 'actions': actions + ['Office尚需真实部署及读取文件/保存回调联调；本次仅只读诊断。']}


def main(project_dir: Path = PROJECT_DIR) -> int:
    report = inspect_environment(project_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get('prerequisites_met') else 2


if __name__ == '__main__':
    raise SystemExit(main())
