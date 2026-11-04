"""Incremental matching within an analysis run, including its offline preview."""
import time
import multiprocessing
import os
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import replace

from .evidence import candidate_for
from .model import Match, TopicIndex, select_match
from .forum import Cancelled
from .author_weight import author_support, weighted_match


_worker_cancel = None


def worker_limit():
    return min(32, max(1, os.cpu_count() or 1))


def configured_workers(value):
    count = int(value)
    limit = worker_limit()
    if not 0 <= count <= limit:
        raise ValueError(f'本地计算进程须为 0–{limit}；0 表示自动。')
    return max(1, min(8, limit - 2)) if count == 0 else count


def _worker_init(cancel):
    global _worker_cancel
    _worker_cancel = cancel


def _score_in_process(group, topics):
    def stop():
        if _worker_cancel.is_set():
            raise Cancelled('已停止本地计算')
    started = time.process_time()
    candidates = []
    for topic in topics:
        stop()
        candidates.append(candidate_for(group, topic, stop))
    return candidates, os.getpid(), time.process_time() - started


def _cost(topics):
    """Cheap scheduling estimate; does not alter retrieval or evidence scores."""
    return sum(1 + len(topic.body) / 256 + len(topic.filenames) for topic in topics)


def _evidence_key(topic):
    if topic is None:
        return None
    # Views, dates and collection links cannot change the matching decision.
    return (topic.title, topic.author, topic.url, topic.body, tuple(topic.filenames),
            tuple(topic.source_ids), topic.details_loaded)


class MatchingSession:
    def __init__(self, workers=0, cache_path=None):
        self.index = TopicIndex([])
        self.revision = 0
        self.cache = {}
        self.results = {}
        self.scored_pairs = 0
        self.author_support = {}
        self._store = None
        self._store_revision = None
        self._published = None
        self.workers = configured_workers(workers)
        self._pool = self._cancel = None
        self._pool_workers = 0
        self._last_parallel = 0.
        self.last_work = {}
        self.reused_pairs = 0
        self._topic_hashes = {}
        self._disk_cache = None
        if cache_path is not None:
            from .score_cache import ScoreCache
            self._disk_cache = ScoreCache(cache_path)

    def close(self):
        if self._pool:
            self._cancel.set()
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = self._cancel = None
            self._pool_workers = 0
        if self._disk_cache is not None:
            self._disk_cache.flush()

    def dispose(self):
        self.close()
        if self._disk_cache is not None:
            self._disk_cache.close()
            self._disk_cache = None

    def _executor(self, workers):
        if self._pool is not None and self._pool_workers < workers:
            self.close()
        if self._pool is None:
            context = multiprocessing.get_context('spawn')
            self._cancel = context.Event()
            self._pool = ProcessPoolExecutor(max_workers=workers, mp_context=context,
                                            initializer=_worker_init, initargs=(self._cancel,))
            self._pool_workers = workers
        return self._pool

    def sync(self, store):
        revision = store.revision_token()
        if self._store is store and self._store_revision == revision:
            return False
        changed = self.update(store.topics())
        self._store, self._store_revision = store, revision
        return changed

    def update(self, topics):
        incoming = {t.id: t for t in topics}
        changed = {identity for identity in self.index.topics.keys() | incoming.keys()
                   if _evidence_key(self.index.topics.get(identity)) != _evidence_key(incoming.get(identity))}
        if not changed:
            self.index.topics = incoming
            return False
        self.index.update(incoming, changed)
        if self._disk_cache is not None:
            from .score_cache import fingerprint
            last_yield = time.monotonic()
            for position, identity in enumerate(changed):
                if identity in incoming:
                    self._topic_hashes[identity] = fingerprint(_evidence_key(incoming[identity]))
                else:
                    self._topic_hashes.pop(identity, None)
                if position % 32 == 0 and time.monotonic() - last_yield >= .025:
                    time.sleep(.001)
                    last_yield = time.monotonic()
        self.revision += 1
        self.author_support = {}
        for pairs in self.cache.values():
            for identity in changed:
                pairs.pop(identity, None)
        return True

    @staticmethod
    def key(group):
        return group.key, group.title, tuple(group.hints), tuple(group.topic_ids)

    def match(self, group, threshold=.85, stop=lambda: None, heartbeat=lambda: None, *, use_prior=True):
        stop()
        key = self.key(group)
        previous = self.results.get(key)
        if previous and previous[:2] == (self.revision, threshold):
            # Rescans create new groups containing current file stats/search history.
            result = replace(previous[2], group=group)
            return self._weighted(result, threshold) if use_prior else result
        pairs, pending = self.prepare(group)
        self._score_jobs([(0, group, pending)], stop, lambda *_: heartbeat())
        result = self._finish(group, threshold)
        return self._weighted(result, threshold) if use_prior else result

    def _weighted(self, result, threshold):
        if not self.author_support or result.error:
            return result
        pairs = self.cache.get(self.key(result.group), {})
        candidates = [c for c in pairs.values() if c.score >= .35 or c.title_score >= .55]
        return weighted_match(result.group, candidates, self.author_support, threshold)

    def _finish(self, group, threshold):
        key = self.key(group)
        pairs = self.cache[key]
        candidates = sorted((c for c in pairs.values() if c.score >= .35 or c.title_score >= .55),
                            key=lambda c: (-c.score, c.author.casefold(), c.topic_id))
        result = select_match(group, candidates, threshold)
        self.results[key] = (self.revision, threshold, result)
        return result

    def prepare(self, group):
        pairs = self.cache.setdefault(self.key(group), {})
        shortlist = self.index.shortlist(group)
        if self._disk_cache is not None and any(topic.id not in pairs for topic in shortlist):
            loaded = self._disk_cache.load(group, {topic.id:self._topic_hashes[topic.id] for topic in shortlist})
            fresh = {identity:candidate for identity, candidate in loaded.items() if identity not in pairs}
            self.reused_pairs += len(fresh)
            pairs.update(fresh)
        keep = {t.id for t in shortlist}
        for identity in pairs.keys() - keep:
            del pairs[identity]
        return pairs, [topic for topic in shortlist if topic.id not in pairs]

    def _score_jobs(self, jobs, stop, heartbeat, complete=lambda *_: None):
        """Split the evidence pairs, including a single group's large shortlist.

        Whole-group jobs used to leave one worker doing a long tail and skipped
        multiprocessing entirely below 20 groups. A bounded queue of small chunks
        balances those cases without re-reading media or sharing mutable scores.
        """
        jobs = [(number, group, topics) for number, group, topics in jobs if topics]
        pairs = sum(len(topics) for _, _, topics in jobs)
        cost = sum(_cost(topics) for _, _, topics in jobs)
        # Network hydration often adds just a few pairs for many minutes.
        # Retain a warm pool briefly, then free processes which have no work.
        if self._pool is not None and time.monotonic() - self._last_parallel >= 30:
            self.close()
        # Spawning Windows interpreters costs more than a handful of comparisons.
        # A warm pool can profitably handle smaller batches. Never spawn empty work.
        minimum = 80 if self._pool is not None else 300
        workers = min(self.workers, pairs, max(1, int(cost / minimum)))
        self.last_work = {'pairs': pairs, 'workers': workers if pairs else 0, 'processes': {}}
        if workers < 2:
            started = time.process_time()
            for number, group, topics in jobs:
                for topic in topics:
                    stop()
                    def checkpoint():
                        stop()
                        heartbeat(number)
                    candidate = candidate_for(group, topic, checkpoint)
                    self.cache[self.key(group)][topic.id] = candidate
                    if self._disk_cache is not None:
                        self._disk_cache.store(group, [candidate], self._topic_hashes)
                    self.scored_pairs += 1
                complete(number)
                heartbeat(number)
            if pairs:
                self.last_work['processes'][os.getpid()] = {'pairs': pairs, 'cpu_seconds': time.process_time() - started}
            return
        executor = self._executor(workers)
        self._last_parallel = time.monotonic()
        # Bound batches independently of the size of the entire library.
        # Otherwise a large scan makes each result batch extremely expensive.
        chunk_cost = min(180, max(20, cost / (workers * 6)))
        chunks = []
        remaining = defaultdict(int)
        for number, group, topics in jobs:
            lightweight = replace(group, files=[], search_queries=[], search_hits={})
            chunk, weight = [], 0
            # Interleave expensive and cheap work via short, bounded chunks. No
            # single group owns an entire worker for the duration of its shortlist.
            for topic in sorted(topics, key=lambda t: (-len(t.body), t.id)):
                chunk.append(topic)
                weight += _cost([topic])
                if weight >= chunk_cost or len(chunk) >= 8:
                    chunks.append((number, lightweight, chunk))
                    remaining[number] += 1
                    chunk, weight = [], 0
            if chunk:
                chunks.append((number, lightweight, chunk))
                remaining[number] += 1
        # Finish groups in scan order. Globally sorting all long-body batches
        # first left thousands of partially scored groups with no visible result.
        cursor, pending = 0, {}
        try:
            while cursor < len(chunks) or pending:
                stop()
                while cursor < len(chunks) and len(pending) < workers * 2:
                    stop()
                    number, group, topics = chunks[cursor]
                    cursor += 1
                    pending[executor.submit(_score_in_process, group, topics)] = (number, group)
                if pending:
                    done, _ = wait(pending, timeout=.05, return_when=FIRST_COMPLETED)
                    for future in done:
                        number, group = pending.pop(future)
                        candidates, pid, cpu_seconds = future.result()
                        self.cache[self.key(group)].update((c.topic_id, c) for c in candidates)
                        if self._disk_cache is not None:
                            self._disk_cache.store(group, candidates, self._topic_hashes)
                        self.scored_pairs += len(candidates)
                        usage = self.last_work['processes'].setdefault(pid, {'pairs': 0, 'cpu_seconds': 0.})
                        usage['pairs'] += len(candidates)
                        usage['cpu_seconds'] += cpu_seconds
                        remaining[number] -= 1
                        if not remaining[number]:
                            complete(number)
                heartbeat(-1)
            self._last_parallel = time.monotonic()
        except BaseException:
            for future in pending:
                future.cancel()
            self.close()
            raise

    def refresh(self, groups, errors=None, threshold=.85, emit=lambda *_: None, stop=lambda: None):
        errors = errors or {}
        start, before, before_reuse = time.monotonic(), self.scored_pairs, self.reused_pairs
        signature = self.revision, threshold, tuple(self.key(g) for g in groups), tuple(sorted(errors.items()))
        needed = sum(self.key(g) not in self.results or self.results[self.key(g)][:2] != (self.revision, threshold) for g in groups)
        if needed:
            emit('log', f'本地匹配：检查 {len(groups)} 组文件（计算进程上限 {self.workers}），复用未变化评分；可随时停止。')
        last_prepare = last_yield = start
        # Publish all groups before expensive work, including on cancellation.
        results = []
        for group in groups:
            previous = self.results.get(self.key(group))
            if previous and previous[:2] != (self.revision, threshold):
                # A catalog revision does not invalidate every file. If its
                # current shortlist is fully scored, reselect synchronously
                # before publishing, including stricter thresholds/deletions.
                pairs = self.cache.get(self.key(group), {})
                identities = {topic.id for topic in self.index.shortlist(group)}
                if identities <= pairs.keys():
                    for identity in pairs.keys() - identities:
                        del pairs[identity]
                    self._finish(group, threshold)
                    previous = self.results[self.key(group)]
            if previous and previous[:2] == (self.revision, threshold):
                results.append(replace(previous[2], group=group))
            else:
                # A new competing author or a stricter threshold can revoke a
                # match. Never leave stale selections actionable after Stop.
                results.append(Match(group, '等待匹配', previous[2].candidates if previous else []))
            now = time.monotonic()
            if now - last_yield >= .1:
                # Yield the GIL during retrieval as well as during scoring.
                time.sleep(.001)
                last_yield = now
            if now - last_prepare >= .25:
                emit('progress', (len(results), len(groups), f'本地匹配 · 复核候选 {len(results)}/{len(groups)} · 已用 {now-start:.1f} 秒'))
                last_prepare = now
        def apply_error(result):
            return replace(result, status='搜索失败', author='', selected=False, error=errors[result.group.key]) if result.group.key in errors else result
        def snapshot():
            # Only completed current decisions may provide author support or
            # receive a bonus. An unfinished group must never become actionable.
            valid = [self.results.get(self.key(r.group), ())[:2] == (self.revision, threshold) for r in results]
            self.author_support = author_support([apply_error(r) for r, ready in zip(results, valid) if ready])
            return [apply_error(self._weighted(r, threshold) if ready else r) for r, ready in zip(results, valid)]
        if signature == self._published:
            return snapshot()
        emit('matches', snapshot())
        last_progress = last_preview = start
        def heartbeat(number, title=''):
            nonlocal last_progress, last_preview
            now = time.monotonic()
            if now - last_progress >= .25:
                time.sleep(.001)
                pairs_done = self.scored_pairs - before
                emit('progress', (number, len(groups),
                     f'本地匹配 · 已用 {now - start:.1f} 秒 · 已核对 {pairs_done} 对候选 · {title}'))
                last_progress = now
            if now - last_preview >= 1:
                emit('matches', snapshot())
                last_preview = now
        try:
            jobs, completed = [], 0
            for number, group in enumerate(groups):
                stop()
                previous = self.results.get(self.key(group))
                if previous and previous[:2] == (self.revision, threshold):
                    completed += 1
                    continue
                _, topics = self.prepare(group)
                if topics:
                    jobs.append((number, group, topics))
                else:
                    results[number] = apply_error(self._finish(group, threshold))
                    completed += 1
                heartbeat(completed, '准备候选')
            if needed:
                if self.reused_pairs > before_reuse:
                    emit('log', f'复用历史评分 {self.reused_pairs - before_reuse} 对；名称、首帖证据或评分规则改变时自动重算。')
                emit('log', f'候选准备完成：{sum(len(topics) for _, _, topics in jobs)} 对待评分，按文件顺序分批发布结果。')
            def finish(number):
                nonlocal completed
                results[number] = apply_error(self._finish(groups[number], threshold))
                completed += 1
            self._score_jobs(jobs, stop, lambda *_: heartbeat(completed), finish)
        except BaseException:
            # Flush completed groups even when Stop arrives before the next
            # throttled preview. The caller also exports this final snapshot.
            emit('matches', snapshot())
            raise
        if self._disk_cache is not None:
            self._disk_cache.flush()
        results = snapshot()
        self._published = signature
        emit('matches', list(results))
        weighted = sum(m.selected and any(c.author_bonus and c.author == m.author for c in m.candidates) for m in results)
        if weighted:
            emit('log', f'作者累计证据加权：{weighted} 组高相似作品可直接归类，跳过逐帖读取；详情显示独立作品数及加分。')
        if needed:
            emit('log', f'本地匹配完成：{len(groups)} 组，新增/变更评分 {self.scored_pairs - before} 对，用时 {time.monotonic() - start:.1f} 秒。')
            if len(self.last_work.get('processes', {})) > 1:
                distribution = '，'.join(f'{pid}：{value["pairs"]} 对' for pid, value in sorted(self.last_work['processes'].items()))
                emit('log', f'实际并行评分 {len(self.last_work["processes"])} 个进程（{distribution}）。')
            elif self.last_work.get('pairs'):
                emit('log', f'本轮仅 {self.last_work["pairs"]} 对新增评分，使用 1 个进程；准备候选的时间已计入总耗时。')
        return results
