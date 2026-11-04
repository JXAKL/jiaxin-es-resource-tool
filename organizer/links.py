"""Lossless extraction of share links; never visits or downloads link targets."""
import html
import re
from urllib.parse import urlsplit, urlunsplit
from .downloads.model import normalize_url as downloadable_url, PLATFORMS, KEYS

URL = re.compile(r'https?://[^\s<>"\x27]+', re.I)
RULES = [('hanime', 'Hanime1'), ('iwara', 'Iwara'), ('rule34', 'Rule34Video'),
         ('gofile', 'GoFile'), ('mega_file', 'MEGA 文件'), ('mega_folder', 'MEGA 文件夹'),
         ('pixeldrain', 'Pixeldrain'), ('mediafire', 'MediaFire'), ('google_drive', 'Google Drive'), ('1fichier', '1Fichier')]
DOWNLOAD_TYPES = frozenset(KEYS[name] for name in PLATFORMS.values() if name != 'MEGA') | {'mega_file', 'mega_folder'}


def normalize(value):
    value = html.unescape(value).strip().rstrip('.,;，。；）)]>')
    try:
        url = urlsplit(value)
        if url.scheme.lower() not in ('http', 'https') or not url.hostname or url.username or url.password:
            return None
        return urlunsplit((url.scheme.lower(), url.netloc.lower(), url.path, url.query, url.fragment))
    except ValueError:
        return None


def urls(text):
    return list(dict.fromkeys(value for raw in URL.findall(html.unescape(text or '')) if (value := normalize(raw))))


def classify(value):
    url = urlsplit(value)
    host, path = (url.hostname or '').lower().removeprefix('www.'), url.path
    supported = downloadable_url(value)
    if supported and host in PLATFORMS and PLATFORMS[host] != 'MEGA':
        return KEYS[PLATFORMS[host]]
    if host == 'gofile.io' and re.fullmatch(r'/d/[\w-]+/?', path):
        return 'gofile'
    if host in ('mega.nz', 'mega.co.nz', 'mega.io'):
        if path.startswith('/folder/') or url.fragment.startswith('F!'):
            return 'mega_folder'
        if path.startswith('/file/') or url.fragment.startswith('!'):
            return 'mega_file'
    if host == 'pixeldrain.com' and path.startswith(('/u/', '/l/', '/api/file/')):
        return 'pixeldrain'
    if host == 'mediafire.com' and path.startswith(('/file/', '/folder/')):
        return 'mediafire'
    if host == 'drive.google.com' and path.startswith(('/file/d/', '/drive/folders/', '/open', '/uc')):
        return 'google_drive'
    if host == '1fichier.com' and url.query:
        return '1fichier'
    return None


def compile_custom(pattern):
    if len(pattern) > 512:
        raise ValueError('自定义表达式最长为 512 个字符')
    try:
        return re.compile(pattern, re.I) if pattern.strip() else None
    except re.error as exc:
        raise ValueError(f'自定义表达式格式错误：{exc}') from exc


def filter_links(values, selected, custom=None):
    result = {key: [] for key in selected}
    if custom:
        result['custom'] = []
    seen = set()
    for raw in values:
        value = normalize(raw)
        if not value or value in seen:
            continue
        seen.add(value)
        key = classify(value)
        if key in result:
            result[key].append(value)
        # Test whole URL candidates, retaining query and MEGA fragment keys.
        if custom and custom.search(value):
            result['custom'].append(value)
    return result
