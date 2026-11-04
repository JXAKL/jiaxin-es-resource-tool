"""Preview-only filtering. This never changes resource links or paid-post labels."""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .preview_blacklist import KNOWN_UPLOADS, KNOWN_LEGACY_URL_HASHES, KNOWN_CONTENT_HASHES

UPLOAD = re.compile(r'/([a-f0-9]{40})(?:_|\.)', re.I)
AD_MARKER = re.compile(r'(?:^|[\s_-])(?:ads?|advertisement|advertising|sponsored|sponsor|affiliate|house-creative|discourse-adplugin)(?:$|[\s_-])', re.I)
PROMOTION = re.compile(r'\b(?:advertisement|sponsored|promo\s*code|coupon|discount|support\s+me|buy\s+me\s+a\s+coffee|leave\s+me\s+a\s+tip|join\s+(?:my\s+)?patreon)\b|广告|打赏|赞助|优惠码', re.I)
AD_HOSTS = {'doubleclick.net', 'googlesyndication.com', 'exoclick.com', 'trafficjunky.net',
            'thehandy.com', 'handyfeeling.com', 'funosr.com', 'syncbot.com', 'osrmarket.com'}
SUPPORT_HOSTS = {'patreon.com', 'ko-fi.com', 'buymeacoffee.com', 'subscribestar.adult',
                 'subscribestar.com', 'fanbox.cc', 'fantia.jp', 'discord.gg'}


def on_host(host, domains):
    return any(host == domain or host.endswith('.'+domain) for domain in domains)


def upload_id(url):
    parsed = urlsplit(url)
    if on_host((parsed.hostname or '').lower(), {'eroscripts.com'}):
        match = UPLOAD.search(parsed.path)
        if match:return match[1].lower()
    return None


def source_key(url):
    identity = upload_id(url)
    if identity:return 'es-upload:'+identity
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, ''))


def cache_key(url):
    # Do not replace an animated original with an older optimized still image.
    path=urlsplit(url).path.lower()
    return source_key(url)+('::original' if '/original/' in path else '::other')+Path(path).suffix


def blocked_reason(url, anchor='', hint=''):
    if upload_id(url) in KNOWN_UPLOADS:return '已知广告或无关图片'
    host = (urlsplit(url).hostname or '').lower()
    if on_host(host, AD_HOSTS):return '广告图片来源'
    if anchor:
        parsed = urlsplit(anchor);host = (parsed.hostname or '').lower()
        if on_host(host, AD_HOSTS):return '设备推广链接'
        # Specific paid posts can have real previews; creator/membership buttons cannot.
        if on_host(host, SUPPORT_HOSTS) and not re.search(r'/(?:posts?|products?)/', parsed.path, re.I):
            return '赞助或社群按钮'
    if PROMOTION.search(hint):return '广告或打赏标记'
    return ''


def clean_sources(rows):
    result=[];seen=set();excluded=0
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('url'),str):continue
        url=row['url']
        if blocked_reason(url,row.get('anchor',''),row.get('hint','')):
            excluded+=1;continue
        key=source_key(url)
        if key in seen:continue
        seen.add(key);result.append(row)
    return result,excluded


def legacy_name(topic_id, url):
    extension=Path(urlsplit(url).path).suffix.lower()
    return f'{topic_id}-{hashlib.sha256(url.encode()).hexdigest()[:20]}{extension}'


def visible_files(preview, topic_id):
    """Filter already-saved results too, without downloading or modifying old media."""
    rows=preview.get('files',[])
    sources={}
    if str(topic_id).isdigit() and any(not r.get('source_url') for r in rows):
        manifest=Path(preview.get('root',''))/(str(topic_id)+'.json')
        try:
            if manifest.stat().st_size<=2_000_000:
                data=json.loads(manifest.read_text(encoding='utf-8'))
                sources={hashlib.sha256(r['url'].encode()).hexdigest()[:20]:r
                         for r in data.get('sources',[]) if isinstance(r,dict) and isinstance(r.get('url'),str)}
        except (OSError,ValueError,AttributeError):pass
    result=[];seen=set()
    for row in rows:
        name=row.get('file','');stem=Path(name).stem.rsplit('-',1)[-1]
        source=sources.get(stem,{})
        url=row.get('source_url') or source.get('url','')
        if stem in KNOWN_LEGACY_URL_HASHES or row.get('sha256') in KNOWN_CONTENT_HASHES:continue
        if url and blocked_reason(url,source.get('anchor',''),source.get('hint','')):continue
        key=row.get('sha256') or (source_key(url) if url else name)
        if key in seen:continue
        seen.add(key);result.append(row)
    return result
