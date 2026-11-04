"""Read-only local inventory -> shared author collection -> missing-resource report."""
import csv
import html
import io
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .collector import CollectionOptions, atomic_text, collect, export_domain_links
from .forum import author_from_input, Cancelled
from .links import RULES
from .model import scan, TopicIndex
from .evidence import candidate_for
from .local_tools.name_match import profile
from .local_tools.pairing import match_records
from .resource_info import payment_info, resource_links, RESOURCE_LABELS, filter_report_resources
from .store import csv_cell
from .moves import safe_author
from .resource_variants import version_summary


@dataclass
class CompletionOptions:
    folder: Path
    author: str
    output: Path
    threshold: float = 85
    refresh: bool = False
    export_downloads: bool = True
    export_scope: str = 'all'
    include_review: bool = False

    def validate(self):
        if not Path(self.folder).is_dir():
            raise ValueError('请选择存在的作者素材文件夹。')
        author_from_input(self.author)
        if not 50 <= self.threshold <= 100:
            raise ValueError('帖子匹配阈值应在 50–100 之间。')
        if self.export_scope not in {'all', 'script', 'video'}:
            raise ValueError('下载链接导出范围无效')


def inventory(folder, stop=lambda: None, log=lambda _: None, *, kinds=None, exclude_roots=()):
    groups = scan([str(folder)], '', recursive=True, stop=stop, log=log, sync_search=True,
                  kinds=kinds, exclude_roots=exclude_roots)
    videos, scripts, by_path = [], [], {}
    for group in groups:
        stop()
        # A video remains an anchor after its main script has been found: an
        # axis group with a strong decorated-name alias can still belong to it.
        f = next((f for f in group.files if f.kind == 'video'), group.files[0])
        by_path[f.path] = group
        record = dict(path=f.path, name=Path(f.path).name, duration=None,
                      name_profile=profile(group.title + Path(f.path).suffix))
        if f.kind == 'video':
            videos.append(record)
        else:
            scripts.append(dict(record, size=f.size, title=group.hints[0] if group.hints else '',
                                original_name=group.hints[1] if len(group.hints) > 1 else ''))
    # Reuse strong local pairing to bridge source/quality wrappers. Only unique
    # high-confidence names may hide a gap; weaker suggestions stay in the report.
    removed = set()
    for row in match_records(videos, scripts, use_duration=False, progress=lambda *_: stop()):
        stop()
        if row['auto_eligible']:
            left, right = by_path[row['video_path']], by_path[row['funscript_path']]
            left.files.extend(right.files)
            left.hints = list(dict.fromkeys(left.hints + right.hints))
            left.topic_ids = list(dict.fromkeys(left.topic_ids + right.topic_ids))
            removed.add(right.key)
    groups = [g for g in groups if g.key not in removed]
    missing = [g for g in groups if len({f.kind for f in g.files}) == 1]
    return groups, missing


def category_map(client, state_dir, refresh, emit):
    path = Path(state_dir) / 'completion-categories.json'
    cached = {}
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        cached = saved['names']
        if not isinstance(cached, dict) or not all(isinstance(k, str) and isinstance(v, str) for k,v in cached.items()):
            cached = {}
            raise ValueError('分类缓存格式无效')
        if not refresh and time.time() - saved['saved'] < 7 * 86400:
            return cached
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        # Classification is optional metadata. Read the normal category page
        # with a bounded page timeout; a broken category API must not block the
        # entire author collection in an unlimited retry loop.
        data = client.read_web('/categories.json')
        listing = data.get('category_list') or {}
        rows = listing.get('categories', []) + listing.get('subcategory_list', [])
        rows += [sub for row in list(rows) for sub in row.get('subcategory_list', []) if isinstance(sub, dict)]
        names = {str(row['id']): str(row.get('name') or row.get('slug') or '') for row in rows if isinstance(row, dict) and row.get('id')}
        if names:
            atomic_text(path, json.dumps(dict(saved=time.time(), names=names), ensure_ascii=False))
            return names
        emit('log', '没有读取到分类名称；按帖子标题、标签和首帖判断，未知项保持未知。')
    except Cancelled:
        raise
    except Exception as exc:
        emit('log', f'分类名称读取失败，继续生成报告：{exc}')
    return cached


def build_rows(groups, topics, threshold, categories=None, failed=None, stop=lambda: None, on_row=lambda _: None):
    stop()
    index = TopicIndex(topics)
    by_id = {t.id: t for t in topics}
    rows = []
    categories, failed = categories or {}, failed or {}
    for n, group in enumerate(groups):
        stop()
        missing = 'script' if any(f.kind == 'video' for f in group.files) else 'video'
        candidates = []
        for topic in index.shortlist(group):
            stop()
            candidate = candidate_for(group, topic, stop)
            if candidate.score >= .5:
                candidates.append(candidate)
        candidates.sort(key=lambda c: (-c.score, c.topic_id))
        # Retain every well-supported matching post, including a later free
        # release of a paid work. Only weak suggestions have a display budget.
        candidates = [c for i,c in enumerate(candidates) if i < 8 or
                      (c.score * 100 >= threshold and c.eligible and c.verified and not c.conflict)]
        matches = []
        for candidate in candidates:
            if candidate.score < .5:
                continue
            topic = by_id[candidate.topic_id]
            error = failed.get(str(topic.id), {}).get('error', '')
            confirmed = (candidate.score * 100 >= threshold and candidate.eligible and
                         candidate.verified and topic.links_loaded and not candidate.conflict and not error)
            resources = [dict(link, scope='帖子级链接；合集内具体文件仍需核对') for link in resource_links(topic)
                         if link['kind'] in {missing, 'shared', 'unknown'}]
            matches.append(dict(id=topic.id, title=topic.title, url=topic.url, author=topic.author,
                score=round(candidate.score * 100, 1), confirmed=confirmed, reason=candidate.reason,
                evidence=candidate.evidence, payment=payment_info(topic, categories.get(str(topic.category_id), '')),
                resources=resources, read_error=error,
                details_loaded=topic.details_loaded, category=categories.get(str(topic.category_id), topic.category_name)))
        confirmed = [m for m in matches if m['confirmed']]
        status = '找到对应帖子' if confirmed else '候选待核对' if matches else '尚未找到对应帖子'
        if confirmed and not any(m['resources'] for m in confirmed):
            status = '对应帖子未提取到所缺资源链接'
        if any(m['read_error'] for m in matches) and not confirmed:
            status = '帖子读取失败，待重试'
        payment = ' / '.join(dict.fromkeys(m['payment']['tag'] for m in confirmed)) or '尚未确认对应帖子'
        rows.append(dict(key=group.key, title=group.title, missing=missing, missing_label='缺脚本' if missing == 'script' else '缺视频',
                         files=[asdict(f) for f in group.files], versions=group.versions,
                         status=status, payment=payment, candidates=matches))
        on_row(rows[-1])
        if n % 10 == 0:
            time.sleep(.001)
    return rows


def new_report_folder(output, author):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    stem = safe_author(author) + '_资源补齐_' + datetime.now().strftime('%Y%m%d_%H%M%S')
    for n in range(10000):
        folder = output / (stem + (f'_{n}' if n else ''))
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise OSError('无法创建独立报告目录。')


def export_report(state, folder):
    state = filter_report_resources(state, missing_only=True)
    folder = Path(folder)
    lines = [f"作者：{state['author']}", f"本地目录：{state['source']}", f"状态：{state['status']}",
             f"总组数：{state['total']}；缺失组：{len(state['rows'])}",
             '只报告本地已有一方的组；双方均无的作品不在范围内。未找到链接不等于付费，免费说明不保证链接仍有效。',
             '链接来自首帖，合集或网盘链接仍需核对内部文件；报告不下载、不购买、不移动素材。', '']
    lines.insert(6, '缺失按作品判断；已识别的差分版本合并统计，各版本与轴文件完整保留，不代表不同剪辑可共用播放时序。')
    entries, links = [], []
    buf = io.StringIO(newline=''); writer = csv.writer(buf)
    writer.writerow(['本地名称', '缺少类型', '核对状态', '付费标签', '帖子', '帖子链接', '匹配分', '确认对应', '脚本付费', '视频付费', '付费依据', '资源链接', '读取错误', '本地文件'])
    for row in state['rows']:
        lines += [f"【{row['missing_label']}】{row['title']} — {row['status']}",
                  version_summary(row.get('versions', [])), '本地文件：']
        lines += [f['path'] for f in row['files']]
        for candidate in row['candidates'] or [None]:
            if candidate:
                p = candidate['payment']
                label = '对应' if candidate['confirmed'] else '候选，需核对'
                lines += [f"{label} · {candidate['score']} 分 · [{p['tag']}] {candidate['title']}", candidate['url'],
                          f"脚本：{p['script']}；视频：{p['video']}", candidate['reason'], *p['evidence']]
                if candidate['read_error']:
                    lines.append('读取失败：' + candidate['read_error'])
                lines += [f"{RESOURCE_LABELS[r['kind']]} | {r.get('label', '')} | {r['url']} | {r['scope']}" for r in candidate['resources']]
                if candidate['confirmed']:
                    links += [r['url'] for r in candidate['resources']]
                values = [row['title'], row['missing_label'], row['status'], p['tag'], candidate['title'], candidate['url'],
                          candidate['score'], label, p['script'], p['video'], ' | '.join(p['evidence']),
                          ' | '.join(r['url'] for r in candidate['resources']), candidate['read_error']]
                entries.append('<details><summary>' + html.escape(f"{label} · {candidate['score']} 分 · [{p['tag']}] {candidate['title']}") + '</summary><p><a href="' + html.escape(candidate['url'], quote=True) + '">打开帖子</a></p><pre>' + html.escape('\n'.join([f"脚本：{p['script']}；视频：{p['video']}", candidate['reason'], *p['evidence'], candidate['read_error']])) + '</pre><ul>' + ''.join('<li>' + html.escape(RESOURCE_LABELS[r['kind']] + ' · ' + r.get('label', '')) + ' <a href="' + html.escape(r['url'], quote=True) + '">打开资源</a> · ' + html.escape(r['scope']) + '</li>' for r in candidate['resources']) + '</ul></details>')
            else:
                values = [row['title'], row['missing_label'], row['status'], row['payment']] + [''] * 9
            writer.writerow([csv_cell(v) for v in values + [' | '.join(f['path'] for f in row['files'])]])
        entries.insert(len(entries) - len(row['candidates']), '<section><h2>' + html.escape(row['missing_label'] + ' · ' + row['title']) + '</h2><p>' + html.escape(row['status']) + '</p><pre>' + html.escape('\n'.join(f['path'] for f in row['files'])) + '</pre>')
        entries.append('</section>')
        lines += ['']
    if state.get('failed'):
        lines += ['未完成读取的帖子（不据此判断付费）：']
        lines += [f"{v.get('title', k)} | {v.get('url', '')} | {v.get('error', '')}" for k, v in state['failed'].items()]
    inventory_lines = ['本地作品版本清单（包含已经配套的作品）：']
    for item in state.get('inventory', []):
        inventory_lines += [item['title'], version_summary(item['versions'])]
        for version in item['versions']:
            inventory_lines += [f"  {version['version']} | {f['path']}" for f in version['files']]
    if state.get('inventory'):
        lines += ['', *inventory_lines]
        entries.append('<details><summary>本地作品版本清单</summary><pre>' +
                       html.escape('\n'.join(inventory_lines)) + '</pre></details>')
    previous = state.get('domain_files', [])
    if not previous:
        try:previous=json.loads((folder/'资源补齐报告.json').read_text(encoding='utf-8')).get('domain_files',[])
        except (OSError,ValueError):previous=[]
    domains = export_domain_links(list(dict.fromkeys(links)), folder / '按域名链接', previous)
    from .completion_export import export_download_text
    download_text = export_download_text(state, folder)
    state = dict(state, domain_files=domains, download_text=download_text)
    atomic_text(folder / '资源补齐报告.json', json.dumps(state, ensure_ascii=False, indent=2))
    atomic_text(folder / '资源补齐报告.txt', '\n'.join(lines))
    atomic_text(folder / '资源补齐报告.csv', '\ufeff' + buf.getvalue())
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>资源补齐报告</title><style>body{background:#101113;color:#e6e8eb;font:15px system-ui;max-width:1100px;margin:auto;padding:28px}section{background:#1b1e22;border-radius:18px;padding:20px;margin:16px 0}a{color:#99d7b7}pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer;padding:12px 0}h2{font-size:18px}</style><h1>资源补齐报告</h1><pre>' + html.escape('\n'.join(lines[:6])) + '</pre>' + ''.join(entries)
    if state.get('failed'):
        page += '<section><h2>未完成读取的帖子</h2><pre>' + html.escape('\n'.join(f"{v.get('title', k)} | {v.get('url', '')} | {v.get('error', '')}" for k, v in state['failed'].items())) + '</pre></section>'
    atomic_text(folder / '资源补齐报告.html', page + '</html>')
    return state


def complete_resources(options, store, client_factory, state_dir, emit=lambda *_: None, check=lambda: None):
    options.validate()
    requested = author_from_input(options.author)
    groups, missing = inventory(options.folder, check, lambda msg: emit('log', msg))
    check()
    folder = new_report_folder(options.output, requested)
    state = dict(version=1, author=requested, source=str(Path(options.folder).resolve()), total=len(groups),
                 inventory=[dict(key=g.key, title=g.title, versions=g.versions) for g in groups],
                 status='本地扫描完成', rows=build_rows(missing, [], options.threshold),
                 failed={}, collection_status='', list_complete=False, report_folder=str(folder))
    state['download_export'] = dict(enabled=options.export_downloads, scope=options.export_scope,
                                    include_review=options.include_review)
    emit('completion', dict(state))
    snapshot, categories = {}, {}
    def collected(kind, value):
        nonlocal snapshot
        if kind == 'collection':
            snapshot = value
        elif kind != 'exported':
            emit(kind, value)
    try:
        if not missing:
            state['status'] = '扫描完成：没有缺失组，无需访问网站'
            return state
        emit('log', f'发现 {len(missing)} 个缺失组，开始读取作者帖子。')
        with client_factory() as client:
            categories = category_map(client, state_dir, options.refresh, emit)
            collect(CollectionOptions(requested, folder / '作者首帖链接', [k for k, _ in RULES],
                                      custom=r'https?://', refresh=options.refresh, resource_details=True),
                    store, client, state_dir, collected)
        state['status'] = '补齐报告完成' if snapshot.get('status') == '采集完成' else '部分报告：采集尚未完整'
    except Cancelled:
        state['status'] = '已停止：已采集内容保留，报告为部分结果'
    except Exception as exc:
        state['status'] = f'部分报告：{type(exc).__name__}: {exc}'
        emit('log', state['status'])
    finally:
        state['author'] = snapshot.get('author', requested)
        state['failed'] = snapshot.get('failed', {})
        state['collection_status'] = snapshot.get('status', '')
        state['list_complete'] = bool(snapshot.get('list_complete'))
        topics = [t for t in store.topics() if t.author.casefold() == state['author'].casefold()]
        # Include cached evidence on interruption, with all freshness failures
        # retained. No further network request is made while writing the report.
        partial = []
        emit('log', f'正在核对 {len(missing)} 个缺失组与 {len(topics)} 条作者帖子…')
        def matched(row):
            partial.append(row)
            if len(partial) % 10 == 0 or len(partial) == len(missing):
                emit('log', f'本地核对 {len(partial)} / {len(missing)} 组；完成后自动导出报告。')
        try:
            state['rows'] = build_rows(missing, topics, options.threshold, categories, state['failed'], check, matched)
        except Cancelled:
            state['status'] = '已停止：已核对结果保留，其余组尚未核对'
            done = {row['key'] for row in partial}
            state['rows'] = partial + [dict(row, status='已停止，尚未核对帖子') for row in state['rows'] if row['key'] not in done]
        state = export_report(state, folder)
        emit('completion', state)
        emit('log', state['status'] + '；报告已保存：' + str(folder))
    return state
