"""Optional, bounded preview cache. Never used as a downloadable resource list."""
import hashlib
import http.cookiejar
import ipaddress
import json
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler
from PIL import Image
from .collector import atomic_text
from .forum import Cancelled, topic_from_json
from .preview_policy import (AD_MARKER, blocked_reason, clean_sources, cache_key,
                             legacy_name, KNOWN_CONTENT_HASHES)

MAX_BYTES = 32 * 1024 * 1024
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.apng', '.avif', '.mp4', '.webm'}


def media_url(value, base):
    if (value or '').startswith(('uploads/','secure-uploads/')):value='/'+value
    url = urljoin(base, (value or '').strip())
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        return None
    if parsed.hostname != urlsplit(base).hostname:
        if parsed.hostname.lower() in {'localhost', 'localhost.localdomain'} or parsed.hostname.endswith('.local'):
            return None
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global: return None
        except ValueError: pass
    return url


class PreviewParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.rows, self.ignored = base, [], []
        self.video = False
        self.anchor = None
        self.hint = ''

    def add(self, raw, kind):
        url = media_url(raw, self.base)
        if not url: return
        path = urlsplit(url).path.lower()
        if any(part in path for part in ('/emoji/', '/user_avatar/', '/avatars/')): return
        anchor=media_url(self.anchor,self.base) if self.anchor else ''
        if blocked_reason(url,anchor or '',self.hint):return
        if kind == 'image' and Path(path).suffix in {'.gif', '.apng', '.webp'}: kind = 'animation'
        self.rows.append(dict(url=url, kind=kind, anchor=anchor or '', hint=self.hint))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs); void = tag in {'img','br','hr','source','input','meta','link','wbr'}
        marker=' '.join(attrs.get(k) or '' for k in ('class','id','data-ad-slot','data-ad-client'))
        if self.ignored or tag in {'blockquote','script','style'} or AD_MARKER.search(marker) or 'data-ad-slot' in attrs or 'data-ad-client' in attrs or (tag=='aside' and 'quote' in attrs.get('class','').split()):
            if not void:self.ignored.append(tag)
            return
        if tag == 'a': self.anchor = attrs.get('href')
        self.hint=' '.join(attrs.get(k) or '' for k in ('alt','title','aria-label'))
        if tag == 'video':
            self.video = True
            if attrs.get('src'): self.add(attrs['src'], 'video')
            if attrs.get('poster'): self.add(attrs['poster'], 'image')
        if tag == 'source' and self.video and attrs.get('src'): self.add(attrs['src'], 'video')
        if tag == 'img':
            if any(x in attrs.get('class','').lower() for x in ('emoji','avatar')):return
            dimensions=[int(attrs[k]) for k in ('width','height') if attrs.get(k,'').isdigit()]
            if dimensions and min(dimensions)<40:return
            # A lightbox often points at the animated original while img is a still.
            if self.anchor and Path(urlsplit(self.anchor).path).suffix.lower() in {'.gif','.apng','.webp'}:
                self.add(self.anchor,'animation')
            raw=attrs.get('data-src') or attrs.get('src')
            if not raw and attrs.get('srcset'):raw=attrs['srcset'].split(',')[0].strip().split(' ')[0]
            if raw:self.add(raw,'image')

    def handle_endtag(self, tag):
        if self.ignored:
            if tag in self.ignored:del self.ignored[len(self.ignored)-self.ignored[::-1].index(tag)-1:]
            return
        if tag=='video':self.video=False
        if tag=='a':self.anchor=None


def extract_previews(cooked, base):
    parser=PreviewParser(base);parser.feed(cooked)
    rows,_=clean_sources(sorted(parser.rows, key=lambda r:r['kind']=='image'))
    return rows[:8]


def cache_path(root, value):
    root=Path(root).resolve();path=(root/value).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in EXTENSIONS or not path.is_file():
        return None
    return path


def validate_media(path):
    with path.open('rb') as stream:header=stream.read(32)
    if path.suffix in {'.mp4','.webm'}:
        if not (header[4:8]==b'ftyp' or header.startswith(b'\x1aE\xdf\xa3')):
            raise ValueError('预览不是有效的视频文件')
    else:
        with Image.open(path) as image:
            if image.width*image.height>40_000_000:raise ValueError('缩略图尺寸过大')
            image.verify()


def download_preview(client, url, referer, destination):
    """Stream normal media GET with scoped browser cookies; one attempt only."""
    jar=http.cookiejar.CookieJar()
    for item in client.context.cookies():
        domain=item['domain'];expires=item.get('expires',-1)
        jar.set_cookie(http.cookiejar.Cookie(0,item['name'],item['value'],None,False,domain,
            domain.startswith('.'),domain.startswith('.'),item.get('path','/'),True,item.get('secure',False),
            int(expires) if expires>0 else None,expires<=0,None,None,{},False))
    class Redirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not media_url(newurl,referer):raise ValueError('预览重定向地址无效')
            if blocked_reason(newurl):raise ValueError('预览重定向到了广告来源，已拦截')
            client.pace('/preview-media')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    opener=build_opener(HTTPCookieProcessor(jar),Redirect())
    user_agent=client.page.evaluate('() => navigator.userAgent')
    temporary=destination.with_suffix(destination.suffix+'.part')
    client.pace('/preview-media')
    try:
        with opener.open(Request(url,headers={'User-Agent':user_agent,'Referer':referer}),timeout=10) as response:
            length=int(response.headers.get('Content-Length') or 0)
            if length>MAX_BYTES:raise ValueError('预览超过 32 MB，已跳过')
            total=0;started=time.monotonic()
            with temporary.open('wb') as target:
                while True:
                    client.check()
                    if time.monotonic()-started>30:raise ValueError('预览下载超时')
                    block=response.read(64*1024)
                    if not block:break
                    total+=len(block)
                    if total>MAX_BYTES:raise ValueError('预览超过 32 MB，已跳过')
                    target.write(block)
            if not total or (length and total!=length):raise ValueError('预览下载不完整')
        temporary.replace(destination)
        try:validate_media(destination)
        except Exception:
            destination.unlink(missing_ok=True);raise
    finally:
        temporary.unlink(missing_ok=True)
        client.pacer.finished(time.time())


class PreviewCache:
    def __init__(self, root, client, store, emit):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.client,self.store,self.emit=client,store,emit
        self.shared={}
        self.checked={}
        # Reuse old per-post files without deleting or copying the user's cache.
        for manifest in self.root.glob('*.json'):
            if not manifest.stem.isdigit():continue
            self.client.check()
            try:
                if manifest.stat().st_size>2_000_000:continue
                data=json.loads(manifest.read_text(encoding='utf-8'))
                for row in clean_sources(data.get('sources',[]))[0]:
                    key=cache_key(row['url'])
                    old=cache_path(self.root,legacy_name(manifest.stem,row['url']))
                    if old:self.shared.setdefault(key,old.name)
                for row in data.get('files',[]):
                    if row.get('source_url') and not blocked_reason(row['source_url']):
                        old=cache_path(self.root,row.get('file',''))
                        if old:self.shared.setdefault(cache_key(row['source_url']),old.name)
            except (OSError,ValueError,AttributeError):continue

    def topic(self, identity, refresh=False):
        manifest=self.root/f'{identity}.json'
        data={}
        try:data=json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        topic=self.store.topic(identity)
        cleaned,blocked=clean_sources(data.get('sources',[]))
        # Old releases capped at eight before filtering; recover previews hidden by ads.
        truncated_old = data.get('preview_policy',0)<1 and blocked and (len(data.get('sources',[]))>=8 or not cleaned)
        if refresh or truncated_old or not topic or not topic.resource_details_loaded or data.get('updated')!=topic.updated_at or 'sources' not in data:
            raw=self.client.get(f'/t/{identity}.json');topic=topic_from_json(raw);self.store.upsert(topic)
            first=next(p for p in raw['post_stream']['posts'] if p.get('post_number')==1)
            data=dict(updated=topic.updated_at,sources=extract_previews(first.get('cooked') or '',topic.url),files=[],preview_policy=1)
            atomic_text(manifest,json.dumps(data,ensure_ascii=False))
        return topic,data

    def fetch(self, topic, data):
        files=[];errors=[];seen_content=set()
        sources,excluded=clean_sources(data['sources'])
        for number,row in enumerate(sources,1):
            self.client.check()
            ext=Path(urlsplit(row['url']).path).suffix.lower()
            if ext not in EXTENSIONS:
                # Discourse short-upload URLs without extensions are decoded as images.
                ext='.png' if row['kind']!='video' else '.mp4'
            key=cache_key(row['url'])
            name=self.shared.get(key) or f'shared-{hashlib.sha256(key.encode()).hexdigest()[:20]}{ext}'
            destination=self.root/name
            try:
                if destination.is_file():
                    stat=destination.stat()
                    try:
                        if (name,stat.st_size,stat.st_mtime_ns) not in self.checked:validate_media(destination)
                    except Exception:destination.unlink()
                if not destination.exists():
                    self.emit('log',f'缓存帖子 {topic.id} 预览 {number}/{len(data["sources"])}…')
                    download_preview(self.client,row['url'],topic.url,destination)
                stat=destination.stat();signature=(name,stat.st_size,stat.st_mtime_ns)
                digest=self.checked.get(signature)
                if digest is None:
                    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
                    self.checked[signature]=digest
                if digest in KNOWN_CONTENT_HASHES:
                    excluded+=1;continue
                self.shared[key]=name
                if digest in seen_content:continue
                seen_content.add(digest)
                files.append(dict(file=name,kind=row['kind'],source_url=row['url'],sha256=digest))
            except Cancelled:raise
            except Exception as exc:
                errors.append(str(exc));self.emit('log',f'帖子 {topic.id} 的预览未缓存：{exc}；帖子结果保留。')
        if excluded:self.emit('log',f'帖子 {topic.id}：已过滤 {excluded} 个广告或无关预览。')
        data.update(files=files,errors=errors,filtered=excluded)
        atomic_text(self.root/f'{topic.id}.json',json.dumps(data,ensure_ascii=False))
        return dict(root=str(self.root),files=files,errors=errors,filtered=excluded)
