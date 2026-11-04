"""Author link collection on top of the shared session, catalog and retry policy."""
import hashlib
import json
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from .forum import Forum, ForumError, Cancelled, author_from_input
from .links import filter_links, compile_custom, RULES
from .moves import safe_author


@dataclass
class CollectionOptions:
    author: str
    output: Path
    types: list[str] = field(default_factory=lambda: ['gofile'])
    custom: str = ''
    order: str = 'desc'
    limit: int = 0
    refresh: bool = False
    title_filter_enabled: bool = False
    title_keyword: str = ''
    resource_details: bool = False


def title_filter_keyword(enabled, value):
    if not enabled:
        return ''
    keyword = unicodedata.normalize('NFKC', value).strip().casefold()
    if not keyword:
        raise ValueError('开启标题筛选后，请输入要包含的关键词。')
    return keyword


def title_matches(title, keyword):
    return not keyword or keyword in unicodedata.normalize('NFKC', title or '').casefold()


def timestamp(value):
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return date.replace(tzinfo=timezone.utc).timestamp() if date.tzinfo is None else date.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def sorted_records(records, order):
    known = [r for r in records if timestamp(r.get('date')) is not None]
    unknown = [r for r in records if timestamp(r.get('date')) is None]
    known.sort(key=lambda r: (timestamp(r['date']), r['id']), reverse=order == 'desc')
    return known + sorted(unknown, key=lambda r: r['id'])


def display_date(value):
    stamp = timestamp(value)
    try:
        return datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S +08:00') if stamp is not None else '未知'
    except (ValueError, OverflowError, OSError):
        return '未知'


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def export_domain_links(links, folder, previous_files=()):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    domains = {}
    for link in links:
        domain = (urlsplit(link).hostname or '').lower().removeprefix('www.')
        if domain:
            domains.setdefault(domain, []).append(link)
    files = []
    for domain, values in sorted(domains.items()):
        name = safe_author(domain) + '.txt'
        atomic_text(folder / name, '\n'.join(dict.fromkeys(values)) + '\n')
        files.append(name)
    # Only retire files recorded by our previous export, never user-added TXT.
    for name in previous_files:
        if (isinstance(name, str) and '/' not in name and '\\' not in name
                and Path(name).name == name and Path(name).suffix == '.txt' and name not in files):
            stale = folder / name
            if stale.is_file() and not stale.is_symlink() and stale.resolve().parent == folder.resolve():
                stale.unlink()
    return files


def export_collection(state, output, order):
    records = sorted_records(list(state['records'].values()), order)
    title = safe_author(state['author']) + '_Links'
    labels = dict(RULES, custom='自定义')
    unique = list(dict.fromkeys(url for record in records for values in record['links'].values() for url in values))
    lines = ['作者：' + state['author'], '采集状态：' + state['status'], f'已读取首帖：{len(records)}；唯一链接：{len(unique)}', '']
    if state.get('title_filter_enabled'):
        lines.insert(2, '标题包含：' + state['title_keyword'])
    for record in records:
        date = display_date(record['date'])
        lines += ['=' * 50, '发布时间：' + date, '帖子标题：' + record['title'], '帖子链接：' + record['url']]
        for key, values in record['links'].items():
            lines += ['', labels.get(key, key) + '：', *(values or ['(无)'])]
        lines += ['']
    output = Path(output)
    domain_folder = title + '_按域名'
    previous_files = []
    data_path = output / (title + '_Data.json')
    try:
        previous = json.loads(data_path.read_text(encoding='utf-8'))
        if isinstance(previous, dict) and previous.get('domain_folder') == domain_folder and isinstance(previous.get('domain_files'), list):
            previous_files = previous['domain_files']
    except (ValueError, OSError):
        pass
    atomic_text(output / (title + '_List.txt'), '\n'.join(lines))
    atomic_text(output / (title + '_Unique.txt'), '\n'.join(unique) + ('\n' if unique else ''))
    domain_files = export_domain_links(unique, output / domain_folder, previous_files)
    atomic_text(data_path, json.dumps(dict(state, records=records, domain_folder=domain_folder, domain_files=domain_files), ensure_ascii=False, indent=2))
    return dict(posts=len(records), unique_links=len(unique), failed=len(state['failed']), status=state['status'],
                folder=str(output), domain_folder=str(output / domain_folder), domains=len(domain_files))


def collect(options, store, client, state_dir, emit=lambda *_: None):
    def selected_links(topic):
        if options.resource_details:
            from .resource_info import resource_links
            values=[r['url'] for r in resource_links(topic)]
        else:values=topic.links
        return filter_links(values, options.types, custom)
    keyword = title_filter_keyword(options.title_filter_enabled, options.title_keyword)
    requested = author_from_input(options.author)
    if options.limit < 0 or options.order not in ('asc', 'desc') or not (options.types or options.custom.strip()):
        raise ValueError('请选择链接类型，并填写有效的排序与帖子数量')
    if set(options.types) - {key for key, _ in RULES}:
        raise ValueError('未知链接类型')
    custom = compile_custom(options.custom)
    forum = Forum(client, store, emit, refresh=options.refresh)
    job = hashlib.sha256(requested.casefold().encode()).hexdigest()[:24]
    checkpoint = Path(state_dir) / ('completion-collections' if options.resource_details else 'collections') / (job + '.json')
    state = dict(version=1, author=requested, records={}, failed={}, status='准备采集',
                 list_complete=False, discovered=0, updated_at='', types=options.types, custom=options.custom,
                 title_filter_enabled=options.title_filter_enabled, title_keyword=options.title_keyword.strip())
    if checkpoint.is_file():
        try:
            saved = json.loads(checkpoint.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                raise ValueError('无效记录')
        except (ValueError, OSError) as exc:
            emit('log', f'续抓记录无法读取，将从共用索引恢复：{exc}')
            saved = {}
        if saved.get('requested', '').casefold() == requested.casefold():
            # Re-filter below from the shared full URL cache; rules never poison successful records.
            state['failed'] = saved.get('failed', {}) if isinstance(saved.get('failed'), dict) else {}
            state['author'] = saved.get('author') or requested
            for key in saved.get('records', {}):
                if not str(key).isdigit():
                    continue
                cached = store.topic(int(key))
                if cached and cached.links_loaded and cached.author.casefold() == state['author'].casefold() and title_matches(cached.title, keyword):
                    state['records'][key] = dict(id=cached.id, author=cached.author, title=cached.title,
                        url=cached.url, date=cached.created_at, links=selected_links(cached))
    state['failed'] = {key:value for key,value in state['failed'].items()
                       if str(key).isdigit() and (cached := store.topic(int(key)))
                       and cached.author.casefold() == state['author'].casefold() and title_matches(cached.title, keyword)}
    state['requested'] = requested
    last_publish = 0.
    def save(publish=False):
        nonlocal last_publish
        if publish or time.monotonic() - last_publish >= 1:
            # Cached re-filtering can process thousands of posts per second.
            # Persist progress at most once per second; stop/final export always flushes.
            state['updated_at'] = datetime.now(timezone.utc).isoformat()
            atomic_text(checkpoint, json.dumps(state, ensure_ascii=False, indent=2))
            emit('collection', dict(state, records=list(state['records'].values()), failed=dict(state['failed'])))
            last_publish = time.monotonic()
    try:
        state['status'] = '正在读取作者索引'
        save(True)
        username = forum.author(requested)
        state['author'] = username
        progress = store.author(username) or {}
        state['list_complete'] = bool(progress.get('complete'))
        topics = [t for t in store.topics() if t.author.casefold() == username.casefold()]
        matching = [t for t in topics if title_matches(t.title, keyword)]
        dated = sorted_records([dict(id=t.id, date=t.created_at) for t in matching], options.order)
        by_id = {t.id: t for t in topics}
        state['discovered'] = len(topics)
        state['title_matched'] = len(matching)
        if keyword:
            emit('log', f'标题筛选“{options.title_keyword.strip()}”：{len(topics)} 条中符合 {len(matching)} 条；其他帖子不读取首帖。')
        selected = dated[:options.limit] if options.limit else dated
        selected_ids = {str(row['id']) for row in selected}
        state['records'] = {key: value for key, value in state['records'].items() if key in selected_ids}
        state['failed'] = {key: value for key, value in state['failed'].items() if key in selected_ids}
        for number, ref in enumerate(selected, 1):
            client.check()
            cached = by_id[ref['id']]
            key = str(cached.id)
            try:
                topic = forum.topic(cached.id, fresh=options.refresh or not cached.links_loaded or
                                    (options.resource_details and not cached.resource_details_loaded))
                if topic.author.casefold() != username.casefold():
                    raise ForumError('主题首帖作者与目标作者不同，未混入结果')
                if not topic.links_loaded:
                    raise ForumError('首帖链接尚未完整读取')
                # A fresh first post can reveal a title changed since indexing.
                if title_matches(topic.title, keyword):
                    state['records'][key] = dict(id=topic.id, author=topic.author, title=topic.title, url=topic.url,
                        date=topic.created_at, links=selected_links(topic))
                else:
                    state['records'].pop(key, None)
                state['failed'].pop(key, None)
            except Cancelled:
                raise
            except ForumError as exc:
                state['records'].pop(key, None)
                state['failed'][key] = dict(url=cached.url, title=cached.title, error=str(exc))
                emit('log', f'主题 {cached.id} 保留待重试：{exc}')
            state['status'] = f'已核对 {number} / {len(selected)} 条首帖'
            emit('progress', (number, len(selected), cached.title))
            save()
        complete = state['list_complete'] and len(selected) == len(matching) and not state['failed']
        state['status'] = '采集完成' if complete else '部分结果：列表未完整、数量限制或失败项待续抓'
        if keyword and complete:
            state['status'] = '标题筛选采集完成' if state['records'] else '采集完成：没有符合标题关键词的帖子'
    except Cancelled:
        state['status'] = '已停止，已读取结果及索引保留'
        raise
    except Exception:
        state['status'] = '采集未完成，已读取结果保留'
        raise
    finally:
        save(True)
        result = export_collection(state, options.output, options.order)
        emit('exported', result)
    return result
