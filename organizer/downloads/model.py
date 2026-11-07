"""Supported user links and durable queue, independent of the desktop."""
from dataclasses import dataclass, asdict, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import hashlib
import base64
import json
import re

PLATFORMS = {'hanime1.me': 'Hanime1', 'iwara.tv': 'Iwara', 'rule34video.com': 'Rule34Video',
             'mega.nz': 'MEGA', 'pixeldrain.com': 'Pixeldrain', 'gofile.io': 'GoFile'}
KEYS = {'Hanime1': 'hanime', 'Iwara': 'iwara', 'Rule34Video': 'rule34', 'MEGA': 'mega', 'Pixeldrain': 'pixeldrain', 'GoFile': 'gofile'}

def protected_mega_kind(fragment):
    if not re.fullmatch(r'P![A-Za-z0-9_-]{118}|P![A-Za-z0-9_-]{139}',fragment): return None
    data=base64.urlsafe_b64decode(fragment[2:]+'==')
    if data[0] in (0,1,2):
        if data[1]==0 and len(data)==88:return 'mega_folder'
        if data[1]==1 and len(data)==104:return 'mega_file'
    return None

def normalize_url(value):
    try:
        u = urlsplit(value.strip().strip('<>\"\''))
        host = (u.hostname or '').lower().removeprefix('www.')
        if host in ('mega.io','mega.co.nz'): host = 'mega.nz'
        if u.scheme.lower() != 'https' or u.username or u.password or u.port not in (None, 443) or host not in PLATFORMS:
            return None
        patterns = {'hanime1.me': r'^/(?:watch|download)(?:/|$)', 'iwara.tv': r'^/video/[^/]+',
                    'rule34video.com': r'^/(?:video|videos)/[^/]+', 'gofile.io': r'^/d/[\w-]+/?$',
                    'pixeldrain.com': r'^/(?:u|l)/[\w-]+/?$', 'mega.nz': r'^/(?:file|folder)/[^/]+'}
        if host == 'mega.nz' and u.path in ('', '/') and (re.match(r'^(?:F)?!', u.fragment) or protected_mega_kind(u.fragment)):
            pass
        elif not re.match(patterns[host], u.path):
            return None
        return urlunsplit(('https', host, u.path, u.query, u.fragment))
    except ValueError:
        return None

@dataclass
class Item:
    url: str
    title: str = ''
    status: str = '待处理'
    detail: str = ''
    sources: list[dict] = field(default_factory=list)

    @property
    def key(self):
        return hashlib.sha256(self.url.encode()).hexdigest()[:24]

    @property
    def platform(self):
        return PLATFORMS[urlsplit(self.url).hostname]

def link_tokens(text):
    """Ignore full-line TXT comments, including URLs in source descriptions."""
    for line in str(text).splitlines():
        if line.lstrip('\ufeff \t').startswith('#'):
            continue
        for raw in re.findall(r'https?://[^\s<>\"\'\[\]()]+', line, re.I):
            yield line, raw


def parse_items(text):
    result, seen = [], set()
    for line, raw in link_tokens(text):
        url = normalize_url(raw.rstrip('.,，。；;'))
        if url and url not in seen:
            title = line.split(' — ', 1)[1].strip() if ' — ' in line else ''
            result.append(Item(url, title)); seen.add(url)
    return result


def merge_sources(existing, incoming):
    """Keep all source posts when several authors share the same resource."""
    seen = {(row.get('url'), row.get('author'), row.get('topic_id')) for row in existing}
    for row in incoming:
        if not isinstance(row, dict):
            continue
        source = {key: row.get(key, '') for key in ('author', 'topic_id', 'title', 'url', 'date')}
        if any(not isinstance(value, (str, int)) or isinstance(value, bool) for value in source.values()):
            continue
        key = source['url'], source['author'], source['topic_id']
        if key not in seen:
            existing.append(source)
            seen.add(key)


def collection_items(records):
    """Translate ES collection records to queue items without visiting targets."""
    by_url, pending_sources, ignored = {}, {}, set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('links'), dict):
            raise ValueError('采集记录格式无效')
        source = {key: record.get(key, '') for key in ('author', 'title', 'url', 'date')}
        source['topic_id'] = record.get('id', '')
        for values in record['links'].values():
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError('采集链接格式无效')
            for raw in values:
                url = normalize_url(raw)
                if not url:
                    ignored.add(raw)
                    continue
                item = by_url.setdefault(url, Item(url, str(record.get('title', ''))))
                pending_sources.setdefault(url, []).append(source)
    # Shared folders can occur in thousands of posts. Deduplicate their sources
    # once per resource instead of rescanning an ever-growing list per post.
    for url, item in by_url.items():
        merge_sources(item.sources, pending_sources[url])
    return list(by_url.values()), len(ignored)

def save_queue(path, items, options):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'version': 1, 'items': [asdict(x) for x in items], 'options': options}, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def load_queue(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('version') != 1:
            return [], {}
        items = []
        for row in data.get('items', []):
            url = normalize_url(row.get('url', ''))
            if url:
                status = row.get('status', '待处理')
                if status in ('处理中', '下载中', '等待网站验证', '等待官网下载'):
                    status = '待继续'
                item = Item(url, str(row.get('title', '')), status, str(row.get('detail', '')))
                if isinstance(row.get('sources'), list):
                    merge_sources(item.sources, row['sources'])
                items.append(item)
        return items, data.get('options', {})
    except (ValueError, OSError, TypeError, AttributeError):
        return [], {}
