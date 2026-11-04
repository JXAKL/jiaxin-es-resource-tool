from __future__ import annotations

import csv
import json
from collections import deque
from dataclasses import asdict
from pathlib import Path

from .forum import Cancelled, ConnectionFailure, Forum, ForumError, SearchResponseError, SearchTooBroad
from .evidence import candidate_for
from .matching import MatchingSession
from .model import match_group, similarity, title_core
from .names import clean_download_name, search_plan, source_ids, fold
from .store import csv_cell


def search_name(title: str) -> str:
    """Remove download wrappers for retrieval only; keep matching evidence intact."""
    return title_core(clean_download_name(title))


def search_ids(forum, group, emit, stop=lambda: None, threshold=.85):
    queries = search_plan(group)
    remaining = list(queries)
    identities = {identity for name in [group.title] + group.hints for identity in source_ids(name)}
    identity_keys = {fold(identity) for identity in identities}
    group.search_queries, group.search_hits = [], {}
    if not queries and not group.topic_ids:
        emit('log', '名称只含短词或泛词，跳过宽泛搜索，保留人工核对。')
    found, matched_queries, errors = {}, {}, []
    while remaining and len(group.search_queries) < 6:
        query = remaining.pop(0)
        if query in group.search_queries:
            continue
        stop()
        group.search_queries.append(query)
        emit('log', f'精准片段搜索 {len(group.search_queries)}/6：{query}')
        try:
            hits = forum.search(query, pages=1, max_hits=20)
        except SearchTooBroad as exc:
            emit('log', f'片段过宽：{query}（{exc}），停止翻页，改用组合词。')
            narrowed = []
            for other in queries:
                if fold(other) in identity_keys or set(other.split()) <= set(query.split()):
                    continue
                combined = ' '.join(dict.fromkeys((query + ' ' + other).split()))
                if combined not in group.search_queries and combined not in narrowed:
                    narrowed.append(combined)
                if len(narrowed) == 2:
                    break
            remaining = narrowed + remaining
            continue
        except SearchResponseError as exc:
            errors.append(exc)
            emit('log', f'此片段搜索异常，继续其余片段：{exc}')
            continue
        emit('log', f'片段“{query}”返回 {len(hits)} 个候选主题。')
        for hit in hits:
            identity = hit['id']
            found[identity] = hit
            matched_queries.setdefault(identity, set()).add(query)
        # A unique search hit is not enough: inspect all small ID-result sets,
        # verify the exact case-sensitive media ID in the first post, then stop.
        if fold(query) in identity_keys and 0 < len(hits) <= 3:
            verified = []
            for hit in hits:
                stop()
                try:
                    topic = forum.topic(hit['id'], fresh=getattr(forum, 'refresh', False))
                    verified.append(topic)
                except ConnectionFailure:
                    raise
                except ForumError as exc:
                    emit('log', f'来源编号候选未能核实：{exc}')
            match = match_group(group, verified, threshold)
            if (len(verified) == len(hits) and match.selected and
                    any(t.author == match.author and identities.intersection(t.source_ids) for t in verified)):
                emit('log', '来源编号已在首帖媒体链接中核实且评分达标，停止其余泛词搜索。')
                break
    if errors and not found and not group.topic_ids:
        raise errors[0]
    # Query counts only rank retrieval. A low title score must not discard a body hit.
    names = [search_name(n) for n in [group.title] + group.hints]
    ranked = sorted(found, key=lambda identity: (
        -int(any(fold(q) in identity_keys for q in matched_queries[identity])),
        -len(matched_queries[identity]),
        -max(similarity(name, found[identity]['title'])[0] for name in names), identity))
    ids = list(dict.fromkeys(group.topic_ids + ranked[:8]))
    group.search_hits = {str(identity): sorted(matched_queries.get(identity, set())) for identity in ids}
    emit('log', f'合并得到 {len(found)} 个主题，将核对 {len(ids)} 个候选的首帖内容。')
    return ids


def batch_matches(groups, indexer, errors, threshold=.85):
    matches = [match_group(group, indexer, threshold) for group in groups]
    for match in matches:
        if match.group.key in errors:
            match.status = '搜索失败'
            match.author, match.selected = '', False
            match.error = errors[match.group.key]
    return matches


def cached_evidence_candidates(match, attempted=()):
    """Read the strongest missing evidence first; retain plausible rival authors."""
    winner = next((c for c in match.candidates if c.author == match.author), None) if match.selected else None
    candidates = []
    for candidate in match.candidates:
        strength = max(candidate.title_score, candidate.attachment_score)
        direct = candidate.topic_id in match.group.topic_ids
        if (candidate.verified or candidate.conflict or candidate.topic_id <= 0
                or candidate.topic_id in attempted or (strength < .72 and not direct)):
            continue
        if winner and (candidate.author.casefold() == winner.author.casefold() or
                       (not direct and strength < max(.90, winner.title_score - .08))):
            continue
        candidates.append(candidate)
    return sorted(candidates, key=lambda c: (-int(c.topic_id in match.group.topic_ids),
                                            -max(c.title_score, c.attachment_score), -c.score, c.topic_id))


def analyse(groups, store, client=None, emit=lambda *_: None, seeds=(), refresh=False,
            max_searches=0, stop=lambda: None, threshold=.85, matcher=None):
    """Discover an author, index all their topics, rematch the batch, then repeat."""
    forum = Forum(client, store, emit, refresh) if client else None
    if forum:
        for author in seeds:
            stop()
            forum.author(author)
    if not .70 <= threshold <= 1.:
        raise ValueError('综合匹配阈值须为 70–100 分')
    matcher = matcher or MatchingSession()
    matcher.sync(store)
    results = matcher.refresh(groups, threshold=threshold, emit=emit, stop=stop)
    if not forum:
        return results
    count = reused = hydrated = 0
    failures = 0
    errors = {}
    pending = {group.key: group for group in groups}
    original_order = {group.key: i for i, group in enumerate(groups)}
    previous_attempts = {group.key: store.attempted(group.key) for group in groups}
    attempted_topics, hydration_counts = set(), {}

    def local_candidates(match):
        if hydration_counts.get(match.group.key, 0) >= 3:
            return []
        return cached_evidence_candidates(match, attempted_topics)

    def schedule():
        by_key = {match.group.key: match for match in results}
        def priority(group):
            match = by_key[group.key]
            candidates = local_candidates(match)
            strength = max((max(c.title_score, c.attachment_score) for c in candidates), default=0)
            # Complete reliable matches and indexed works before any new searches,
            # regardless of their position in the scanned directory.
            lane = 0 if match.selected and not candidates else 1 if strength >= .95 else 2 if candidates else 3
            return lane, -strength, previous_attempts[group.key], original_order[group.key]
        return deque(sorted(pending.values(), key=priority))

    def update_results(force=False):
        nonlocal results
        changed = matcher.sync(store)
        if changed or force:
            results = matcher.refresh(groups, errors, threshold, emit, stop)
        return changed

    queue = schedule()
    emit('log', '优先处理已有索引命中的作品；首帖证据充分时直接复用，缺少证据时优先补读。')
    while queue:
        stop()
        group = queue.popleft()
        emit('progress', (len(groups) - len(pending) + 1, len(groups), group.title))
        cached_match = matcher.match(group, threshold, stop)
        candidates = local_candidates(cached_match)
        if candidates:
            candidate = candidates[0]
            stop()
            attempted_topics.add(candidate.topic_id)
            hydration_counts[group.key] = hydration_counts.get(group.key, 0) + 1
            try:
                emit('log', f'索引优先核验：{group.title} → {candidate.author} / 主题 {candidate.topic_id}'
                     f'（标题 {candidate.title_score:.0%}）；补读一次首帖证据……')
                forum.topic(candidate.topic_id, fresh=refresh)
                hydrated += 1
            except ConnectionFailure:
                raise
            except ForumError as exc:
                emit('log', f'缓存候选 {candidate.topic_id} 无法补充正文：{exc}')
            update_results()
            queue = schedule()
            continue
        del pending[group.key]
        if cached_match.status == '可归类':
            reused += 1
            forum.author(cached_match.author)
            if update_results():
                queue = schedule()
            continue
        if max_searches and count >= max_searches:
            emit('log', f'达到本次搜索上限 {max_searches} 组。其余文件已用现有索引匹配，可再次继续。')
            break
        count += 1
        emit('log', f'搜索文件名：{group.title}')
        try:
            ids = search_ids(forum, group, emit, stop, threshold)
        except SearchResponseError as exc:
            errors[group.key] = str(exc)
            failures += 1
            emit('log', f'此组搜索失败，保留待重试：{exc}')
            update_results(force=True)
            queue = schedule()
            if failures >= 3:
                raise ForumError('连续 3 组搜索异常，已暂停以避免反复请求。详见搜索失败条目与诊断文件。') from exc
            continue
        failures = 0
        for candidate_number, topic_id in enumerate(ids, 1):
            stop()
            try:
                emit('log', f'读取候选首帖 {candidate_number}/{len(ids)}：主题 {topic_id}……')
                topic = forum.topic(topic_id, fresh=refresh)
                best = candidate_for(group, topic, stop)
                emit('log', f'核对 {topic.author} / 主题 {topic.id}：综合 {best.score * 100:.1f}；{best.reason}')
            except ConnectionFailure:
                raise
            except ForumError as exc:
                # A deleted/private topic is an explicit error, never a successful empty match.
                emit('log', f'主题 {topic_id} 无法核实：{exc}')
        matcher.sync(store)
        verified_match = matcher.match(group, threshold, stop)
        if verified_match.status == '可归类':
            stop()
            emit('log', f'作者匹配通过：{verified_match.author}，综合分达到 {threshold * 100:.0f} 分阈值且无作者歧义。')
            forum.author(verified_match.author)
        else:
            emit('log', f'作者暂未确认：{verified_match.status}；未开始采集候选作者的全部帖子。')
        store.mark_attempted(group.key)
        # matcher.sync above may already have consumed this revision. Refresh
        # once here to publish the new evidence and reprioritise the whole queue.
        update_results(force=True)
        queue = schedule()
    emit('log', f'分析完成：{reused} 组复用索引免搜索，补读 {hydrated} 条缓存首帖；'
         f'搜索了 {count} 组文件，缓存中有 {len(matcher.index.topics)} 个主题。')
    return results


def export_matches(matches, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / '归类预览.json').write_text(json.dumps([asdict(m) for m in matches], ensure_ascii=False, indent=2), encoding='utf-8')
    with (folder / '归类预览.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['是否勾选', '状态', '源文件', '作者', '候选作者', '综合匹配分', '依据', '帖子标题', '帖子地址', '搜索错误',
                         '标题分', '正文分', '附件分', '线索覆盖分', '证据片段', '搜索片段'])
        for match in matches:
            candidate = next((c for c in match.candidates if c.author == match.author), None)
            candidate = candidate or (match.candidates[0] if match.candidates else None)
            for local in match.group.files:
                writer.writerow([csv_cell(v) for v in (int(match.selected), match.status, local.path, match.author,
                    ' | '.join(dict.fromkeys(c.author for c in match.candidates)),
                    round(candidate.score * 100, 1) if candidate else '',
                    '人工指定' if match.manual else candidate.reason if candidate else '',
                    candidate.title if candidate else '', candidate.url if candidate else '', match.error,
                    round(candidate.title_score * 100, 1) if candidate else '',
                    round(candidate.body_score * 100, 1) if candidate else '',
                    round(candidate.attachment_score * 100, 1) if candidate else '',
                    round(candidate.coverage_score * 100, 1) if candidate else '',
                    ' | '.join(candidate.evidence) if candidate else '', ' | '.join(match.group.search_queries))])
