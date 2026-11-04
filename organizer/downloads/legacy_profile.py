"""Copy a downloader's site session without opening its original profile."""
import os
import shutil
import tempfile
from pathlib import Path


def prepare_iwara_profile(state, legacy, emit):
    return prepare_site_profile(state, legacy, emit, 'iwara', 'Iwara')


def prepare_site_profile(state, legacy, emit, platform, label):
    if platform not in ('iwara', 'hanime'):
        raise ValueError('不支持的兼容会话类型')
    state, legacy = Path(state), Path(legacy)
    target = state / 'download-browser' / f'{platform}-legacy-chrome'
    if target.exists():
        return str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'{platform}-session-', dir=target.parent))
    try:
        if (legacy / 'Local State').is_file() and (legacy / 'Default').is_dir():
            # Keep authentication/site storage; omit caches, history and downloads.
            for relative in ('Local State', 'Default/Preferences', 'Default/Secure Preferences',
                             'Default/Network', 'Default/Local Storage', 'Default/IndexedDB',
                             'Default/Session Storage', 'Default/WebStorage'):
                source = legacy / relative
                if not source.exists():
                    continue
                destination = staging / relative
                if source.is_symlink() or os.path.isjunction(source):
                    raise OSError('旧会话目录含链接，请改用普通目录')
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    for parent, dirs, files in os.walk(source):
                        for name in dirs + files:
                            node = Path(parent) / name
                            if node.is_symlink() or os.path.isjunction(node):
                                raise OSError('旧会话目录含链接，请改用普通目录')
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
            emit({'type':'log', 'text':f'{label}：已复制已有下载器的登录与网站会话，原目录保留。'})
        staging.rename(target)
    finally:
        # Only the temporary directory created above may be removed.
        if staging.exists() and staging.parent.resolve() == target.parent.resolve() and staging.name.startswith(f'{platform}-session-'):
            shutil.rmtree(staging)
    return str(target)


def chrome_installed():
    return any((Path(root) / 'Google/Chrome/Application/chrome.exe').is_file()
               for root in (os.environ.get('PROGRAMFILES', ''), os.environ.get('PROGRAMFILES(X86)', ''),
                            os.environ.get('LOCALAPPDATA', '')) if root)
