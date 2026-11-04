"""Resumable tag/video-creator discovery; ES poster identity stays separate."""
from dataclasses import dataclass
from pathlib import Path
from collections import Counter
import csv
import hashlib
import html
import io
import json
import re
import time
import unicodedata
from urllib.parse import urlsplit, unquote

from .collector import atomic_text, export_domain_links
from .completion import category_map
from .forum import Forum, Cancelled, TopicUnavailable, SearchResponseError, search_page
from .resource_info import payment_info, resource_links


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def parse_tags(value):
    """Comma-separated set, canonicalized for repeatable checkpoints."""
    tags = set()
    for item in unicodedata.normalize('NFKC', value).split(','):
        item = item.strip()
        if not item:
            continue
        if item.startswith(('https://', 'http://')):
            parsed = urlsplit(item)
            if parsed.hostname != 'discuss.eroscripts.com':
                raise ValueError('标签网址必须来自 ES。')
            parts = parsed.path.strip('/').split('/')
            if len(parts) < 2 or parts[0] not in {'tag', 'tags'}:
                raise ValueError('请填写标签名称或 ES 标签页面地址。')
            item = unquote(parts[1])
        item = normalized(item.lstrip('#'))
        if not re.fullmatch(r'[\w\-]+', item):
            raise ValueError('多个标签用逗号隔开，标签内不要包含空格或搜索指令。')
        tags.add(item)
    return sorted(tags)


def video_creators(title):
    """Only consecutive leading bracket groups, never a poster or body mention."""
    title = unicodedata.normalize('NFKC', html.unescape(title)).strip()
    found = []
    while title:
        match = re.match(r'^(?:\(([^()]*)\)|\[([^\[\]]*)\]|【([^【】]*)】|「([^「」]*)」)\s*', title)
        if not match:
            break
        value = next(x for x in match.groups() if x is not None).strip()
        value = re.sub(r'^(?:by\s+|(?:video\s+)?(?:creator|artist|author)\s*:\s*|(?:视频作者|作者)\s*[:：]\s*)', '', value, flags=re.I)
        if normalized(value) not in {'free', 'paid', 'script', 'scripts', 'funscript', 'mmd', 'vr', '4k', '1080p', '免费', '付费'}:
            found.append(value)
            found.extend(x.strip() for x in re.split(r'\s+[&/]\s+|[,、]', value) if x.strip() != value)
        title = title[match.end():].lstrip()
    return list(dict.fromkeys(x for x in found if x))


@dataclass
class ScrapeOptions:
    output: Path
    tag: str = ''
    creator: str = ''
    limit: int = 0
    refresh: bool = False
    exclude_tags: str = ''
    include_paid: bool = False
    thumbnails: bool = False
    thumbnail_dir: Path | None = None

    def filters(self):
        tag = ','.join(parse_tags(self.tag))
        parse_tags(self.exclude_tags)
        creator = unicodedata.normalize('NFKC', self.creator).strip()
        bracketed = video_creators(creator)
        if bracketed and re.fullmatch(r'(?:\([^()]+\)|\[[^\[\]]+\]|【[^【】]+】)', creator):
            creator = bracketed[0]
        if creator and (len(creator) > 100 or not re.fullmatch(r"[\w\s.'’&,+/\-]+", creator)):
            raise ValueError('视频作者请填写名称，不要填写网址或搜索指令。')
        if not (tag or creator):
            raise ValueError('请填写标签、视频作者，或同时填写两者。')
        if self.limit < 0:
            raise ValueError('每轮读取上限不能小于 0。')
        if self.thumbnails and not self.thumbnail_dir:
            raise ValueError('请指定缩略图缓存目录。')
        return tag, creator


def inspect_topic(topic, tag, creator, categories, exclude_tags='', include_paid=False):
    creators = video_creators(topic.title)
    payment = payment_info(topic, categories.get(str(topic.category_id), topic.category_name))
    topic_tags = {normalized(t) for t in topic.tags}
    rejected = topic_tags.intersection(parse_tags(exclude_tags))
    if rejected:
        return None, '命中排除标签：' + ', '.join(sorted(rejected))
    if tag and not set(parse_tags(tag)).issubset(topic_tags):
        return None, '标签不符'
    if creator and normalized(creator) not in {normalized(c) for c in creators}:
        return None, '标题开头的视频作者不符'
    # A paid video can accompany a free script. Keep its explicit payment label.
    if not include_paid and (payment['script'] in {'付费', '混合/需核实'} or payment['post'] in {'付费', '混合/需核实'}):
        return None, '脚本/帖子付费或证据冲突'
    if not include_paid and payment['script'] != '免费' and payment['post'] != '免费':
        return None, '免费状态不明'
    resources = resource_links(topic)
    return dict(id=topic.id, author=topic.author, video_creators=creators, title=topic.title,
                url=topic.url, date=topic.created_at, tags=topic.tags, payment=payment,
                category=categories.get(str(topic.category_id), topic.category_name),
                resources=resources, links={'resources': [r['url'] for r in resources]}), ''


def author_counts(records):
    names, counts = {}, Counter()
    for row in records.values():
        key = row['author'].casefold()
        names.setdefault(key, row['author'])
        counts[key] += 1
    return [dict(author=names[key], count=count) for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def export_report(state, output):
    output = Path(output)
    records = sorted(state['records'].values(), key=lambda r: (r['date'], r['id']), reverse=True)
    rankings = author_counts(state['records'])
    summary = dict(state, ranking=rankings)
    previous = output / '搜索刮削报告.json'
    old_files = []
    try:
        old_files = json.loads(previous.read_text(encoding='utf-8')).get('domain_files', [])
    except (ValueError, OSError):
        pass
    urls = list(dict.fromkeys(u for r in records for u in r['links']['resources']))
    files = export_domain_links(urls, output / '按域名链接', old_files)
    for name in files:
        target = output / '按域名链接' / name
        scope = '包含付费/状态不明帖子' if state.get('include_paid') else '仅明确免费帖子'
        atomic_text(target, f'# 搜索刮削：{scope}中的文件分享/视频链接；保留参数和密钥。\n'
                    '# 第三方资源可能需登录/付费或已失效；逐帖依据见搜索刮削报告。\n' + target.read_text(encoding='utf-8'))
    summary['domain_files'] = files
    atomic_text(previous, json.dumps(summary, ensure_ascii=False, indent=2))
    stream = io.StringIO(newline='')
    writer = csv.writer(stream); writer.writerow(['ES 发帖作者', '纳入帖子数'])
    for row in rankings:
        writer.writerow([("'" if row['author'].startswith(('=', '+', '-', '@')) else '') + row['author'], row['count']])
    atomic_text(output / 'ES作者排名.csv', '\ufeff' + stream.getvalue())
    esc = lambda value: html.escape(str(value), quote=True)
    parts = ['<!doctype html><meta charset="utf-8"><title>搜索刮削</title><style>body{background:#17191c;color:#e4e7eb;font:16px sans-serif;max-width:1100px;margin:32px auto;padding:16px}a{color:#9dd7bb}article,table{background:#24272c;border-radius:12px;padding:16px;margin:12px 0}td,th{text-align:left;padding:8px}small{color:#b6bec9}</style><h1>搜索刮削</h1>',
             f'<p>标签：{esc(state["tag"] or "不限")} · 视频作者：{esc(state["creator"] or "不限")} · {esc(state["status"])}</p>',
             f'<p>指定标签必须全部满足；排除标签（命中任意一个即剔除）：{esc(state.get("exclude_tags") or "无")}；包含付费帖子：{"开启（不按付费状态排除）" if state.get("include_paid") else "关闭（仅明确免费）"}</p>',
             '<p>按 ES 发帖作者统计去重后纳入的帖子，排除项不计数。免费指脚本/帖子；视频的付费状态另列。结果受登录权限及网站搜索可见范围限制。</p><table><tr><th>ES 发帖作者</th><th>帖子数</th></tr>']
    parts += [f'<tr><td>{esc(r["author"])}</td><td>{r["count"]}</td></tr>' for r in rankings]
    parts.append('</table>')
    for row in records:
        parts.append(f'<article><h3><a href="{esc(row["url"])}">{esc(row["title"])}</a></h3><p>ES 作者：{esc(row["author"])} · 视频作者：{esc(", ".join(row["video_creators"]))} · {esc(row["date"])}</p><p>帖子：{esc(row["payment"]["post"])}；视频：{esc(row["payment"]["video"])}；脚本：{esc(row["payment"]["script"])}</p><small>{esc("；".join(row["payment"]["evidence"]))}</small><ul>')
        parts += [f'<li><a href="{esc(r["url"])}">{esc(r.get("label") or r["url"])}</a></li>' for r in row['resources']]
        parts.append('</ul></article>')
    parts.append(f'<p>未纳入：{len(state["excluded"])} 帖；读取失败：{len(state["failed"])} 帖。逐帖原因见 JSON 报告。</p>')
    atomic_text(output / '搜索刮削报告.html', '\n'.join(parts))
    return dict(folder=str(output), posts=len(records), unique_links=len(urls), domains=len(files), status=state['status'])


def scrape(options, store, client, state_dir, emit=lambda *_: None):
    tag, creator = options.filters()
    excluded_tags = ','.join(parse_tags(options.exclude_tags))
    identity = dict(tag=tag, creator=normalized(creator))
    if excluded_tags or options.include_paid:
        identity.update(exclude_tags=excluded_tags, include_paid=options.include_paid)
    job = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
    checkpoint = Path(state_dir) / 'search-scrapes' / (job + '.json')
    output = Path(options.output) / ('搜索刮削-' + job)
    # Use one known search operand to obtain a superset; require ALL tags on
    # the fetched first post. Do not depend on ambiguous multi-tag OR syntax.
    seed_tag = next(iter(parse_tags(tag)), '')
    queries = [' '.join([*([f'tags:{seed_tag}'] if seed_tag else []),
                        *([f'"{creator}"'] if creator else []), 'in:first', 'order:latest'])]
    state = dict(version=2, tag_mode='all', tag=tag, creator=creator, exclude_tags=excluded_tags, include_paid=options.include_paid,
                 queries=queries, query_index=0, query_seen=[], page=1, pending=[], more=False, page_loaded=False,
                 records={}, excluded={}, failed={}, removed=[], seen=[], list_complete=False, status='准备搜索', discovered=0)
    if checkpoint.exists():
        try:
            saved = json.loads(checkpoint.read_text(encoding='utf-8'))
            if (saved.get('version') in (1, 2) and saved.get('tag') == tag
                    and (saved.get('tag_mode') == 'all' or len(parse_tags(tag)) <= 1)
                    and normalized(saved.get('creator', '')) == identity['creator']
                    and saved.get('exclude_tags', '') == excluded_tags
                    and saved.get('include_paid', False) == options.include_paid):
                if saved['version'] == 1:
                    saved.update(version=2, query_seen=saved.get('seen', []))
                if options.refresh:
                    saved = dict(removed=saved.get('removed', []))
                state.update(saved)
                if state['list_complete']:
                    # A finished task starts discovery again to find new posts, reusing details.
                    state.update(page=1, pending=[], more=False, page_loaded=False, seen=[], list_complete=False,
                                 query_index=0, query_seen=[])
        except (ValueError, OSError):
            pass
    seen = set(state['seen'])
    state.update(job=job, report_folder=str(output), thumbnails=options.thumbnails)
    removed = set(state.get('removed', []))
    for key in removed:
        state['records'].pop(key,None);state['failed'].pop(key,None)
    forum = Forum(client, store, emit)
    preview_cache = None
    if options.thumbnails:
        from .search_thumbnails import PreviewCache
        preview_cache = PreviewCache(options.thumbnail_dir, client, store, emit)
    checked = 0
    last_publish = 0.

    def publish(force=False):
        nonlocal last_publish
        now = time.monotonic()
        if not force and now - last_publish < 1:
            return
        last_publish = now
        state['seen'] = sorted(seen)
        state['discovered'] = len(seen)
        atomic_text(checkpoint, json.dumps(state, ensure_ascii=False))
        emit('collection', json.loads(json.dumps(state)))

    def read_one(item, categories):
        nonlocal checked
        client.check()
        key = str(item['id'])
        if key in removed:return
        # Cheap title rejection before a topic visit; membership checked again on full data.
        if creator and normalized(creator) not in {normalized(c) for c in video_creators(item['title'])}:
            state['excluded'][key] = dict(title=item['title'], reason='标题开头的视频作者不符')
            state['records'].pop(key, None); state['failed'].pop(key, None)
            return
        if options.limit and checked >= options.limit:
            raise ScrapeLimit()
        checked += 1
        emit('log', f'核对首帖 {item["id"]}：{item["title"]}')
        try:
            if preview_cache:
                topic, preview_data = preview_cache.topic(item['id'], refresh=options.refresh)
            else:
                cached = store.topic(item['id'])
                topic = forum.topic(item['id'], fresh=options.refresh or not (cached and cached.resource_details_loaded))
            row, reason = inspect_topic(topic, tag, creator, categories, excluded_tags, options.include_paid)
            if row:
                old_preview = state['records'].get(key,{}).get('preview')
                if old_preview:row['preview']=old_preview
                state['records'][key] = row; state['excluded'].pop(key, None)
                # Publish the accepted post before optional media work, so Stop
                # during a download never loses an already confirmed result.
                publish()
                if preview_cache:row['preview']=preview_cache.fetch(topic,preview_data)
            else:
                # Keep only the minimal exclusion reason, not scraped content/links.
                state['excluded'][key] = dict(reason=reason)
                state['records'].pop(key, None)
            state['failed'].pop(key, None)
        except TopicUnavailable as exc:
            state['records'].pop(key, None)
            state['failed'][key] = dict(item, error=str(exc))

    try:
        publish(True)
        categories = category_map(client, state_dir, options.refresh, emit)
        # Failures are retried even when the search cursor has already passed them.
        for item in list(state['failed'].values()):
            read_one(item, categories); publish()
        while not state['list_complete']:
            client.check()
            if not state['page_loaded']:
                query = queries[state['query_index']]
                emit('log', f'搜索条件 {state["query_index"]+1}/{len(queries)}，第 {state["page"]} 页：{query}')
                items, more = search_page(client.get('/search.json', {'q': query, 'page': state['page']}))
                fresh = [item for item in items if str(item['id']) not in seen]
                query_fresh = {str(item['id']) for item in items} - set(state['query_seen'])
                if more and not query_fresh:
                    raise SearchResponseError('网站声称有后续页，但没有新帖子；保留断点，不标记为完成。')
                state['query_seen'] = sorted(set(state['query_seen']) | query_fresh)
                seen.update(str(item['id']) for item in items)
                state.update(pending=fresh, more=more, page_loaded=True)
                publish(True)
            while state['pending']:
                item = state['pending'][0]
                read_one(item, categories)
                state['pending'].pop(0)
                state['status'] = f'已纳入 {len(state["records"])} 帖 · 排除 {len(state["excluded"])} 帖 · 失败 {len(state["failed"])} 帖'
                publish()
            state['page'] += 1
            state['page_loaded'] = False
            if not state['more']:
                state['query_index'] += 1
                state['list_complete'] = state['query_index'] >= len(queries)
                if not state['list_complete']:
                    state.update(page=1, query_seen=[])
            publish(True)
        state['status'] = '搜索完成' if not state['failed'] else '搜索结束，部分首帖读取失败，可继续重试'
    except ScrapeLimit:
        state['status'] = '达到本轮读取上限，已保存断点，可继续'
    except Cancelled:
        state['status'] = '已停止，结果及断点已保留'
        raise
    except Exception as exc:
        state['status'] = f'未完成，已保存断点：{exc}'
        raise
    finally:
        publish(True)
        emit('exported', export_report(state, output))
    return state


class ScrapeLimit(Exception):
    pass


def remove_posts(state, keys, state_dir):
    """Local result removal only. Tombstones survive continuing and refreshing."""
    state=json.loads(json.dumps(state))
    job=state.get('job','')
    if not re.fullmatch(r'[0-9a-f]{16}',job):raise ValueError('请先完成或停止一轮采集，再删除帖子。')
    keys={str(k) for k in keys}.intersection(state['records'])
    state['removed']=sorted(set(state.get('removed',[]))|keys)
    for key in keys:state['records'].pop(key,None)
    state['status']=f'已删除 {len(keys)} 条结果；保留 {len(state["records"])} 帖'
    atomic_text(Path(state_dir)/'search-scrapes'/(job+'.json'),json.dumps(state,ensure_ascii=False))
    return state
