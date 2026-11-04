"""Author/date organization built on shared inventory, collection and matching."""
import copy
import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .collector import CollectionOptions, collect
from .completion import inventory, category_map
from .forum import author_from_input, Cancelled
from .links import RULES
from .matching import MatchingSession, configured_workers
from .moves import safe_author, no_links, digest, append, relocate, publish
from .resource_info import payment_info, resource_links
from .resource_source_links import links_for, pair_groups

KINDS = {'scripts': {'script'}, 'videos': {'video'}, 'both': {'script', 'video'}}
MODE_LABELS = {'scripts': '仅脚本', 'videos': '仅视频', 'both': '脚本与视频组合'}
PAIR_LABELS = {'external': '另一半在别处，无需配套', 'missing': '另一半缺失，需补齐'}
DATE_LABELS = {'review': '最高匹配帖，跨月冲突待核对', 'earliest': '最早可靠帖子', 'latest': '最新可靠帖子'}
TZ = timezone(timedelta(hours=8))


@dataclass
class Source:
    path: str
    mode: str = 'both'
    pairing: str = 'missing'
    partner: str = ''


@dataclass
class OrganizeOptions:
    sources: list[Source]
    author: str
    output: Path
    threshold: float = 85
    date_policy: str = 'review'
    refresh: bool = False
    workers: int = 0

    def validate(self):
        author_from_input(self.author)
        if not self.sources:
            raise ValueError('请至少添加一个来源文件夹。')
        if not 50 <= self.threshold <= 100 or self.date_policy not in DATE_LABELS:
            raise ValueError('匹配阈值或日期规则无效。')
        configured_workers(self.workers)
        links_for(self.sources)
        no_links(Path(self.output).absolute())
        output = Path(self.output).resolve()
        seen = set()
        for source in self.sources:
            if source.mode not in KINDS or source.pairing not in PAIR_LABELS:
                raise ValueError('来源类型或配套设置无效。')
            if not source.path.strip() or not Path(source.path).is_dir():
                raise ValueError('来源文件夹不存在：'+source.path)
            no_links(Path(source.path).absolute())
            root = Path(source.path).resolve()
            if root in seen:
                raise ValueError('同一来源文件夹不能重复添加。')
            seen.add(root)
            if root.is_relative_to(output) or output.is_relative_to(root):
                raise ValueError('输出目录须与来源目录分开，不能互相包含：'+str(root))


def month_of(value):
    """Same +08:00 calendar as collection; never substitute file/update dates."""
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        date = date.astimezone(TZ)
        return date.strftime('%Y/%m'), date.isoformat()
    except (ValueError, TypeError, OverflowError):
        return '', ''


def scan_sources(sources, check=lambda: None, log=lambda _: None):
    roots = [Path(s.path).resolve() for s in sources]
    links=links_for(sources)
    auxiliary=set(links.values())
    inventories={}
    groups, rows = [], []
    for i,(source, root) in enumerate(zip(sources, roots)):
        check()
        # Most-specific selected directory owns its files, even with different
        # type rules. It is excluded before reading parent metadata or pairing.
        excluded = [p for p in roots if p != root and p.is_relative_to(root)]
        log('扫描来源：'+str(root)+' · '+MODE_LABELS[source.mode])
        local, _ = inventory(root, check, log, kinds=KINDS[source.mode], exclude_roots=excluded)
        for group in local:
            group.key = str(root) + '|' + group.key
        inventories[i]=local
    for i,(source,root) in enumerate(zip(sources,roots)):
        if i in auxiliary:continue
        local=inventories[i]
        pairs,notes=pair_groups(local,inventories[links[i]],source.mode,check) if i in links else ({},{})
        if i in links:
            log(f'关联配套：以{MODE_LABELS[source.mode]}目录为主，{len(local)} 组中 {len(pairs)} 组可靠对应；辅助目录其余 {len(inventories[links[i]])-len(pairs)} 组未纳入。')
        for n,group in enumerate(local):
            primary_title=group.display_name
            pairing_note=notes.get(n,'')
            if n in pairs:
                partner_index,score,reason=pairs[n]
                counterpart=inventories[links[i]][partner_index]
                group.files.extend(counterpart.files)
                group.hints=list(dict.fromkeys(group.hints+counterpart.hints+[counterpart.title]))
                group.topic_ids=list(dict.fromkeys(group.topic_ids+counterpart.topic_ids))
                pairing_note=f'关联配套 {score:.1%}：{reason}'
            kinds = {f.kind for f in group.files}
            missing = ('script' if 'video' in kinds else 'video') if len(kinds) == 1 else ''
            external = bool(missing and i not in links and source.mode != 'both' and source.pairing == 'external')
            label = ('另一半在别处，无需配套' if external else
                     '缺脚本' if missing == 'script' else '缺视频' if missing else '视频与脚本齐全')
            rows.append(dict(key=group.key, title=primary_title, source=str(root),
                linked_source=str(roots[links[i]]) if i in links else '', primary_mode=source.mode,
                source_roots=[str(root)]+([str(roots[links[i]])] if n in pairs else []), pairing_note=pairing_note,
                mode=source.mode, pairing=source.pairing, completeness=label,
                missing='' if external else missing, external=external,
                files=[asdict(f) for f in group.files], versions=group.versions, candidates=[], chosen=None,
                selected=False, manual=False, target='', month='', status='等待帖子匹配', operations=[]))
            groups.append(group)
    return groups, rows


def choose(row, candidate, output, author, *, manual=False):
    """One explicit post fixes both resource evidence and calendar destination."""
    if not candidate.get('month'):
        raise ValueError('该帖子没有有效的首次发布日期，不能按年月整理。')
    row.update(chosen=candidate['id'], month=candidate['month'], manual=manual,
               target=str(Path(output)/safe_author(author)/candidate['month']),
               selected=True, status='已手动核对' if manual else '可整理')


def decide(row, options, author):
    reliable = [c for c in row['candidates'] if c['confirmed']]
    if not reliable:
        row['status'] = '候选待核对' if row['candidates'] else '未匹配'
        return
    dated = [c for c in reliable if c['month']]
    if not dated:
        row['status'] = '对应帖子缺少发布日期，待核对'
        return
    if options.date_policy == 'review':
        best = reliable[0]
        rivals = [c for c in reliable if best['score']-c['score'] < 8]
        if not best['month'] or any(c['month'] != best['month'] for c in rivals):
            row['status'] = '多个可靠帖子日期不同，待核对'
            return
        chosen = best
    else:
        chosen = sorted(dated, key=lambda c: (c['date_local'], c['id']),
                        reverse=options.date_policy == 'latest')[0]
    choose(row, chosen, options.output, author)


def match_rows(groups, rows, topics, options, categories=None, failed=None,
               check=lambda: None, emit=lambda *_: None, cache_path=None):
    categories, failed = categories or {}, failed or {}
    # Filter here as well as in collection, so callers cannot mix another author.
    topics = [t for t in topics if t.author.casefold() == author_from_input(options.author).casefold()]
    by_id = {t.id: t for t in topics}
    session = MatchingSession(options.workers, cache_path)
    stopped = False
    try:
        session.update(topics)
        try:
            session.refresh(groups, threshold=options.threshold/100,
                            emit=lambda kind, value: emit(kind,value) if kind != 'matches' else None, stop=check)
        except Cancelled:
            stopped = True
        for group, row in zip(groups, rows):
            key = session.key(group)
            if key not in session.results:
                row['status'] = '已停止，尚未核对' if stopped else '未匹配'
                continue
            candidates = sorted(session.cache.get(key, {}).values(), key=lambda c: (-c.score,c.topic_id))
            for rank, candidate in enumerate(candidates):
                if candidate.score < .5:
                    continue
                topic = by_id[candidate.topic_id]
                error = failed.get(str(topic.id), {}).get('error','')
                confirmed = (candidate.score*100 >= options.threshold and candidate.eligible and
                             candidate.verified and topic.links_loaded and not candidate.conflict and not error)
                if rank >= 8 and not confirmed:
                    continue
                month, local_date = month_of(topic.created_at)
                row['candidates'].append(dict(id=topic.id, title=topic.title, author=topic.author,
                    url=topic.url, created_at=topic.created_at, updated_at=topic.updated_at,
                    date_local=local_date, month=month, filenames=list(topic.filenames),
                    score=round(candidate.score*100, 3), confirmed=confirmed, conflict=candidate.conflict,
                    reason=candidate.reason, evidence=list(candidate.evidence), read_error=error,
                    payment=payment_info(topic,categories.get(str(topic.category_id),topic.category_name)),
                    resources=resource_links(topic)))
            decide(row, options, options.author)
        return stopped, dict(session.last_work)
    finally:
        session.dispose()


def prepare(options, store, client_factory, state_dir, emit=lambda *_: None,
            check=lambda: None, *, local_only=False):
    from .resource_organize_report import export_report
    options.validate()
    options = copy.deepcopy(options)
    options.author = author_from_input(options.author)
    options.output = Path(options.output).resolve()
    options.sources = [Source(str(Path(s.path).resolve()),s.mode,s.pairing,
                             str(Path(s.partner).resolve()) if s.partner else '') for s in options.sources]
    groups, rows = scan_sources(options.sources, check, lambda s:emit('log',s))
    folder = Path(options.output)/'_整理报告'/f'{safe_author(options.author)}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}'
    state = dict(version=1, author=options.author, output=str(options.output),
        sources=[asdict(s) for s in options.sources], threshold=options.threshold, date_policy=options.date_policy,
        status='本地扫描完成', rows=rows, failed={}, list_complete=False, report_folder=str(folder))
    emit('organized', copy.deepcopy(state))
    snapshot, categories = {}, {}
    def collected(kind, value):
        nonlocal snapshot
        if kind == 'collection':
            snapshot = value
        elif kind != 'exported':
            emit(kind,value)
    try:
        if rows and not local_only:
            with client_factory() as client:
                categories = category_map(client, state_dir, options.refresh, emit)
                collect(CollectionOptions(options.author,folder/'作者帖子', [k for k,_ in RULES],
                    custom=r'https?://',refresh=options.refresh,resource_details=True),
                    store,client,state_dir,collected)
        options.author = snapshot.get('author',options.author)
        state.update(author=options.author, failed=snapshot.get('failed',{}),
                     list_complete=bool(snapshot.get('list_complete')))
    except Cancelled:
        state['status']='已停止采集，保留已读取内容'
    except Exception as exc:
        state['status']='采集未完整：'+str(exc)
        emit('log',state['status'])
    finally:
        options.author = snapshot.get('author',options.author)
        state.update(author=options.author,failed=snapshot.get('failed',{}),
                     list_complete=bool(snapshot.get('list_complete')))
        topics = store.topics()
        stopped, work = match_rows(groups,rows,topics,options,categories,state['failed'],check,emit,
                                  Path(state_dir)/'resource-organize-scores.sqlite3')
        state['work'] = work
        if stopped:
            state['status']='已停止；已核对结果保留，其余尚未核对'
        elif local_only:
            state['status']='本地索引预览完成；未访问网站，缓存可能不完整'
        elif snapshot.get('status') == '采集完成':
            state['status']='采集与匹配完成；请核对预览后执行'
        mark_collisions(state)
        try:
            export_report(state)
        finally:
            emit('organized', copy.deepcopy(state))
    return state


def mark_collisions(state):
    targets = {}
    for row in state['rows']:
        row['collision'] = ''
        if not row.get('target') or not row['selected']:
            continue
        for f in row['files']:
            target = Path(row['target'])/Path(f['path']).name
            identity = os.path.normcase(str(target))
            if target.exists():
                row['collision']='目标已有同名文件；本组不会覆盖'
            if identity in targets:
                row['collision']='所选项存在同名目标；本组不执行'
                targets[identity]['collision']=row['collision']
            targets[identity]=row


def checked_copy(source, target, checksum, check):
    partial=target.with_name('.resource-'+uuid.uuid4().hex+'.partial')
    target.parent.mkdir(parents=True,exist_ok=True)
    no_links(target)
    before=source.stat()
    try:
        with source.open('rb') as src, partial.open('xb') as dst:
            while True:
                check()
                block=src.read(4*1024*1024)
                if not block: break
                dst.write(block)
            dst.flush();os.fsync(dst.fileno())
        after=source.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns) or digest(partial,check)!=checksum:
            raise ValueError('复制校验失败或源文件变化，保留原文件')
        shutil.copystat(source,partial)
        no_links(target);publish(partial,target)
    finally:
        partial.unlink(missing_ok=True)


def execute(state, mode, check=lambda:None, emit=lambda *_:None):
    from .resource_organize_report import export_report
    if mode not in {'copy','move'}:
        raise ValueError('请选择复制或移动。')
    state=copy.deepcopy(state)
    output=Path(state['output']).absolute();no_links(output)
    output=output.resolve()
    journal=Path(state['report_folder'])/(mode+'-'+uuid.uuid4().hex+'.jsonl')
    mark_collisions(state)
    last_export=time.monotonic()
    try:
        for row in state['rows']:
            check()
            if not row['selected']: continue
            candidate=next((c for c in row['candidates'] if c['id']==row['chosen']),None)
            try:
                if not candidate or not (candidate['confirmed'] or row['manual']):
                    raise ValueError('尚未确认对应帖子')
                month,_=month_of(candidate['created_at'])
                if not month or candidate['author'].casefold()!=state['author'].casefold():
                    raise ValueError('作者或发布日期无效')
                target_dir=output/safe_author(state['author'])/month
                no_links(target_dir)
                no_links(Path(row['target']))
                if target_dir!=Path(row['target']).resolve() or not target_dir.resolve().is_relative_to(output):
                    raise ValueError('预览目标已改变，请重新分析')
                if row['collision']:
                    raise FileExistsError(row['collision'])
                jobs=[]
                for f in row['files']:
                    src=Path(f['path']);target=target_dir/src.name
                    no_links(src);no_links(target)
                    if not any(src.resolve().is_relative_to(Path(root).resolve()) for root in row.get('source_roots',[row['source']])):
                        raise ValueError('来源文件超出扫描目录')
                    if target.exists(): raise FileExistsError('目标同名：'+str(target))
                    stat=src.stat()
                    if (stat.st_size,stat.st_mtime_ns)!=(f['size'],f['mtime_ns']):
                        raise ValueError('扫描后文件变化：'+str(src))
                    jobs.append((src,target,f))
                for src,target,f in jobs:
                    check()
                    emit('log',f'{"复制" if mode=="copy" else "移动"}：{src.name} → {month}')
                    checksum=digest(src,check)
                    stat=src.stat()
                    if (stat.st_size,stat.st_mtime_ns)!=(f['size'],f['mtime_ns']):
                        raise ValueError('校验时文件变化：'+str(src))
                    operation=dict(id=uuid.uuid4().hex,source=str(src),target=str(target),sha256=checksum,
                        size=f['size'],author=state['author'],mode=mode,post=candidate['url'],status='计划')
                    append(journal,dict(operation,event='planned'))
                    row['operations'].append(operation)
                    if mode=='copy': checked_copy(src,target,checksum,check)
                    else: relocate(src,target,checksum,check)
                    operation['status']='完成'
                    append(journal,dict(id=operation['id'],event='done'))
                row['status']='复制完成' if mode=='copy' else '移动完成'
                row['selected']=False
            except (OSError,ValueError) as exc:
                row['status']='未全部完成：'+str(exc)
                emit('log',row['status'])
            if time.monotonic()-last_export>=5:
                export_report(state)
                last_export=time.monotonic()
        state['status']='整理结束；请查看各项结果及报告中的未完成项'
    except Cancelled:
        state['status']='整理已停止；已完成文件保留，未完成文件仍在原处'
        for row in state['rows']:
            if row['selected']:
                row['status']='整理已停止；部分文件已完成' if row['operations'] else '已停止，尚未执行'
    finally:
        state['last_journal']=str(journal) if journal.exists() else ''
        state['last_mode']=mode
        try:
            export_report(state)
        except OSError as exc:
            state['status']+='；报告写入失败：'+str(exc)
        finally:
            emit('organized',copy.deepcopy(state))
    return state
