"""Positive resource-address rules; text around a link cannot make it a file.

No requests, redirects or cookies. Unknown webpages stay out of resource lists.
Known storage/video page routes and explicit media/archive attachments survive.
"""
import html
import re
from functools import lru_cache
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote, parse_qs, quote

BASE = 'https://discuss.eroscripts.com/'
PURCHASE_HOSTS = frozenset({'patreon.com','fantia.jp','gumroad.com','subscribestar.adult',
    'subscribestar.com','fansly.com','onlyfans.com','boosty.to','fanbox.cc','ko-fi.com',
    'buymeacoffee.com','payhip.com','booth.pm','dlsite.com','unifans.io','paypal.com'})
NON_RESOURCE_HOSTS = PURCHASE_HOSTS | {'x.com','twitter.com','pixiv.net','instagram.com',
    'facebook.com','discord.com','discord.gg','t.me','linktr.ee','t.co','tinyurl.com',
    'bit.ly','aliexpress.com','aliexpress.us','thehandy.com','syncbot.com','lovense.com',
    'imgur.com','ibb.co','imgbb.com'}
IMAGE_EXTS = {'.jpg','.jpeg','.png','.gif','.webp','.avif','.svg','.bmp','.ico','.tif','.tiff'}
VIDEO_EXTS = {'.mp4','.mkv','.avi','.mov','.webm','.m4v','.wmv','.flv','.ts','.mpg','.mpeg','.m3u8','.mpd'}
ARCHIVE_EXTS = {'.zip','.rar','.7z','.tar','.gz','.bz2','.xz'}
ES_HOSTS = {'discuss.eroscripts.com','eroscripts-discourse.eroscripts.com','discourse-s3-cdn.eroscripts.com'}
_BARE = re.compile(r'^(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]|$)', re.I)
_TEXT = re.compile(r'(?<![\w@/])(?:https?://|//|(?:[a-z0-9-]+\.)+[a-z]{2,}(?=[/?]))[^\s<>"\x27]+',re.I)


def in_hosts(host, hosts):
    return any(host == name or host.endswith('.'+name) for name in hosts)


@lru_cache(maxsize=50000)
def resource_url(value, base=BASE):
    """Resolve actual URLs, not fragment navigation or arbitrary relative text."""
    if not isinstance(value,str):return None
    value=html.unescape(value).strip().strip('<>"\'')
    if not value or re.search(r'[\x00-\x1f\x7f]',value):return None
    if value.startswith('//'):value='https:'+value
    elif _BARE.match(value):value='https://'+value
    elif not re.match(r'https?://',value,re.I):
        if re.match(r'^/?(?:uploads|secure-uploads)/',value,re.I):value=urljoin(base,'/'+value.lstrip('/'))
        else:return None
    try:
        u=urlsplit(value);host=(u.hostname or '').lower()
        if not host or u.scheme.lower() not in {'https','http'} or u.username or u.password:return None
        _=u.port  # Validate malformed port strings.
        if re.search(r'[\s%\\]',u.netloc):return None
        # Older urljoin(base, 'mega.nz/...') caches accidentally pointed at ES.
        if host=='discuss.eroscripts.com' and _BARE.match(u.path.lstrip('/')):
            return resource_url(u.path.lstrip('/')+('?' + u.query if u.query else '')+('#' + u.fragment if u.fragment else ''))
        return urlunsplit((u.scheme.lower(),u.netloc.lower(),quote(u.path,safe="/%:@!$&'()*+,;=-._~"),
                           u.query.replace(' ','%20'),u.fragment.replace(' ','%20')))
    except (ValueError,UnicodeError):return None


def text_urls(text):
    for match in _TEXT.finditer(html.unescape(text or '')):
        raw=match.group().rstrip('.,;，。；')
        # Strip prose punctuation, but preserve balanced brackets inside URLs.
        for left,right in [('(',')'),('[',']'),('（','）')]:
            while raw.endswith(right) and raw.count(right)>raw.count(left):raw=raw[:-1]
        if url:=resource_url(raw):yield url


def extension(path):
    path=unquote(path).lower().rstrip('/')
    return '.'+path.rsplit('.',1)[-1] if '.' in path.rsplit('/',1)[-1] else ''


def file_kind(path):
    ext=extension(path)
    if ext=='.funscript':return 'script'
    if ext in VIDEO_EXTS:return 'video'
    if ext in ARCHIVE_EXTS or re.search(r'\.(?:zip|7z|rar)\.\d{3}$',path,re.I):return 'storage'
    return None


@lru_cache(maxsize=50000)
def address_kind(url, label=''):
    """Call only on normalized absolute URLs. Never accept by paragraph text."""
    u=urlsplit(url);host=(u.hostname or '').lower().removeprefix('www.')
    path=unquote(u.path);p=path.lower();query=parse_qs(u.query)
    if in_hosts(host,NON_RESOURCE_HOSTS):return None
    if extension(p) in IMAGE_EXTS or extension(label.strip()) in IMAGE_EXTS:return None
    if host in ES_HOSTS:
        if re.match(r'^/(?:uploads|secure-uploads|original)/',p):
            return file_kind(p) or file_kind(label.strip())
        return None
    # Embedded filename/disposition can prove a signed attachment, never a
    # redirect URL parameter or arbitrary anchor text.
    direct=file_kind(p)
    if direct:return direct
    if re.search(r'/(?:download|attachment|attachments)(?:/|$)',p):
        for key in ('filename','file','name','response-content-disposition'):
            for value in query.get(key,[]):
                if extension(value.strip('"\'')) in IMAGE_EXTS:return None
                if kind:=file_kind(value.strip('"\'')):return kind
    # Explicit share objects, never a provider's homepage/login/account page.
    if host in {'mega.nz','mega.co.nz','mega.io'}:
        if re.fullmatch(r'/(?:file|folder)/[^/]+(?:/(?:file|folder)/[^/]+)?/?',path) or (p in {'','/'} and re.match(r'^(?:F)?![^!]+',u.fragment)):
            return 'storage'
    if host=='gofile.io' and re.fullmatch(r'/d/[\w-]+/?',path):return 'storage'
    if host=='pixeldrain.com' and re.fullmatch(r'/(?:u|l|api/file)/[\w-]+/?',path):return 'storage'
    if host=='mediafire.com' and re.match(r'^/(?:file|folder|download)/[^/]+',path):return 'storage'
    if host=='drive.google.com' and (re.match(r'^/(?:file/d|drive/(?:u/\d+/)?folders)/[^/]+',path) or
            (p in {'/open','/uc'} and query.get('id'))):return 'storage'
    if host=='1fichier.com' and p in {'','/'} and re.match(r'^[a-zA-Z0-9]{5,}(?:&|$)',u.query):return 'storage'
    if host in {'disk.yandex.com','disk.yandex.ru','yadi.sk'} and re.match(r'^/[di]/[^/]+',path):return 'storage'
    if host in {'dropbox.com','dl.dropboxusercontent.com'} and re.match(r'^/(?:s|sh|scl/(?:fi|fo))/[^/]+',path):return 'storage'
    if host=='onedrive.live.com' and p in {'/','/redir','/download'} and any(query.get(k) for k in ('id','resid','cid')):return 'storage'
    if host=='1drv.ms' and re.match(r'^/[a-z]/',path):return 'storage'
    if host=='workupload.com' and re.match(r'^/(?:file|archive)/[^/]+',path):return 'storage'
    if host=='send.cm' and re.fullmatch(r'/(?:d/|s/)?[A-Za-z0-9_-]+/?',path) and p not in {'/login','/register'}:return 'storage'
    if host=='cyberfile.me' and re.match(r'^/(?:file|folder)/[^/]+',path):return 'storage'
    # Specific video pages (including sites not yet automated by the downloader).
    if host in {'iwara.tv','ecchi.iwara.tv'} and re.match(r'^/(?:video|videos)/[^/]+',path):return 'video'
    if host=='hanime1.me' and p in {'/watch','/download','/watch/','/download/'} and query.get('v'):return 'video'
    if host in {'rule34video.com','rule34video.party'} and re.match(r'^/(?:video|videos)/\d+(?:/|$)',path):return 'video'
    if host in {'youtube.com','m.youtube.com'} and ((p=='/watch' and query.get('v')) or re.match(r'^/(?:shorts|embed)/[^/]+',path)):return 'video'
    if host=='youtu.be' and re.fullmatch(r'/[\w-]+/?',path):return 'video'
    if in_hosts(host,{'pornhub.com','pornhubpremium.com'}) and p=='/view_video.php' and query.get('viewkey'):return 'video'
    if host=='spankbang.com' and re.match(r'^/[^/]+/video/',path):return 'video'
    if host=='eporner.com' and re.match(r'^/(?:hd-porn/|video/|video-)[^/]+(?:/|$)',path):return 'video'
    if host=='xvideos.com' and re.match(r'^/video(?:\d+|\.[\w-]+)/',path):return 'video'
    if host=='redgifs.com' and re.match(r'^/(?:watch|ifr)/[^/]+',path):return 'video'
    if host=='pmvhaven.com' and re.match(r'^/video/[^/]+',path):return 'video'
    if host=='faptap.net' and re.match(r'^/v/[^/]+',path):return 'video'
    if host=='mmdfans.net' and re.match(r'^/mmd/[^/]+/[^/]+',path):return 'video'
    if host in {'hmvmania.com','hentaibar.com'} and re.match(r'^/videos?/[^/]+',path):return 'video'
    if host=='sexlikereal.com' and re.match(r'^/scenes/[^/]+',path):return 'video'
    if host in {'hanime.tv','members.hanime.tv'} and re.match(r'^/videos/hentai/[^/]+',path):return 'video'
    if host=='vimeo.com' and re.fullmatch(r'/\d+/?',path):return 'video'
    if host=='streamable.com' and re.fullmatch(r'/[A-Za-z0-9]{4,}/?',path) and p not in {'/login','/signup','/pricing'}:return 'video'
    return None
