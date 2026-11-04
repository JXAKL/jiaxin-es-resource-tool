from __future__ import annotations

import html
import heapq
import json
import math
import os
import re
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Callable
from .names import clean_download_name, source_ids, COMMON, CJK
from .script_axes import AXES, base_stem
from .resource_variants import parts as resource_parts, split_stem, version_inventory

VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v', '.wmv', '.flv', '.ts', '.mpg', '.mpeg'}
TECH = re.compile(r'(?<!\w)(?:360p|480p|720p|1080p|1440p|2160p|4320p|4k|8k|h264|h265|x264|x265|hevc|avc|\d{2,3}fps)(?!\w)', re.I)
LABEL = re.compile(r'[\[【(]\s*(?:free|paid|funscript|script|manual|ai|vr|2d|3d)\s*[\]】)]', re.I)
VARIANT = re.compile(r'(?:\s*[\[({【](?:hard|easy|soft|fast|slow|intense|gentle|轻度|强力|慢速|快速)[\])}】]|[ _.-]+(?:hard|easy|soft|fast|slow|intense|gentle|轻度|强力|慢速|快速))\s*$', re.I)
TOPIC_URL = re.compile(r'https://discuss\.eroscripts\.com/t/(?:[^\s/<>"?]+/)?(\d+)(?:[/\s?#]|$)', re.I)


@lru_cache(maxsize=100_000)
def title_core(title: str) -> str:
    value = unicodedata.normalize('NFKC', html.unescape(title)).casefold().replace('_', ' ')
    value = LABEL.sub(' ', value)
    while VARIANT.search(value):
        value = VARIANT.sub('', value)
    value = TECH.sub(' ', value)
    return re.sub(r'\s+', ' ', re.sub(r'[^\w]+', ' ', value)).strip()


def file_title(path: Path) -> str:
    stem = unicodedata.normalize('NFKC', path.stem).strip()
    return split_stem(stem).title if path.suffix.lower() == '.funscript' else stem


def script_hint(value: str) -> str:
    value = unicodedata.normalize('NFKC', value).strip()
    if value.lower().endswith('.funscript'):
        return file_title(Path(value))
    return split_stem(value).title


def local_group_key(path: Path) -> str:
    title = resource_parts(path).work
    core = title_core(title)
    if core and not re.fullmatch(r'\d{3,4}p\d{2,3}fps', title, re.I):
        return core
    # A title made solely of quality tags must still join its own axes. Keep
    # the literal base and directory, not the full per-axis path or an empty key.
    literal = unicodedata.normalize('NFKC', title).casefold()
    return 'literal:' + json.dumps([os.path.normcase(str(path.parent)), literal], ensure_ascii=False)


@lru_cache(maxsize=100_000)
def title_numbers(core: str):
    """Reuse normalized episode markers during all pair/window comparisons."""
    return (tuple(str(int(n)) for n in re.findall(r'\d+', core)),
            tuple(re.findall(r'([零〇一二三四五六七八九十百两]+)\s*[集章部话話季卷]', core)))


def number_conflict(left: str, right: str):
    left_nums, left_parts = title_numbers(left)
    right_nums, right_parts = title_numbers(right)
    return bool((left_nums and right_nums and left_nums != right_nums) or
                (left_parts and right_parts and left_parts != right_parts))


@lru_cache(maxsize=100_000)
def similarity(left: str, right: str) -> tuple[float, str, bool]:
    a, b = title_core(left), title_core(right)
    if not a or not b:
        return 0., '标题信息不足', False
    if a == b:
        meaningful = (len(a.replace(' ', '')) >= 5 and bool(re.search(r'[^\W\d_]', a))
                      and a not in {'video', 'movie', 'script', 'funscript', 'default', 'output', 'untitled', 'sample'})
        return (.98, '规范化标题完全一致', False) if meaningful else (.90, '短标题或纯编号，需要核对', False)
    conflict = number_conflict(a, b)
    seq = (SequenceMatcher(None, a, b, autojunk=False).ratio() + SequenceMatcher(None, b, a, autojunk=False).ratio()) / 2
    ca, cb = Counter(a.split()), Counter(b.split())
    dice = 2 * sum((ca & cb).values()) / (sum(ca.values()) + sum(cb.values()))
    score = min(.94, .75 * seq + .25 * dice)
    if conflict:
        return min(.45, score), '编号或集数冲突', True
    return score, '标题相似，需要核对', False


@dataclass
class LocalFile:
    path: str
    size: int
    mtime_ns: int
    kind: str


@dataclass
class FileGroup:
    key: str
    title: str
    files: list[LocalFile] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    topic_ids: list[int] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    search_hits: dict[str, list[str]] = field(default_factory=dict)

    @property
    def versions(self):
        return version_inventory(self.files)

    @property
    def kind_label(self):
        kinds = {f.kind for f in self.files}
        if {'script', 'video'} <= kinds:
            return 'funscript / 视频'
        return 'funscript' if 'script' in kinds else '视频' if 'video' in kinds else '—'

    @property
    def display_name(self):
        scripts = sorted((Path(f.path) for f in self.files if f.kind == 'script'),
                         key=lambda p: (bool(resource_parts(p).versions), bool(resource_parts(p).axes), p.name.casefold(), str(p)))
        if scripts:
            first = scripts[0]
            return first.name if not resource_parts(first).axes else file_title(first) + '.funscript'
        return self.title


@dataclass
class Topic:
    id: int
    title: str
    author: str
    url: str
    created_at: str = ''
    updated_at: str = ''
    category_id: int | None = None
    tags: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    views: int = 0
    posts_count: int = 0
    body: str = ''
    source_ids: list[str] = field(default_factory=list)
    details_loaded: bool = False
    links: list[str] = field(default_factory=list)
    links_loaded: bool = False
    category_name: str = ''
    resource_links: list[dict] = field(default_factory=list)
    resource_details_loaded: bool = False


@dataclass
class Candidate:
    author: str
    topic_id: int
    title: str
    url: str
    score: float
    reason: str
    conflict: bool = False
    title_score: float = 0.
    body_score: float = 0.
    attachment_score: float = 0.
    coverage_score: float = 0.
    evidence: list[str] = field(default_factory=list)
    verified: bool = False
    eligible: bool = False
    author_support: int = 0
    author_bonus: float = 0.


@dataclass
class Match:
    group: FileGroup
    status: str
    candidates: list[Candidate]
    author: str = ''
    selected: bool = False
    manual: bool = False
    error: str = ''
    selection_note: str = ''


def scan(roots: list[str], target: str, recursive: bool = True,
         stop: Callable[[], None] = lambda: None,
         log: Callable[[str], None] = lambda _: None, *, sync_search: bool = True,
         kinds=None, exclude_roots=()) -> list[FileGroup]:
    groups: dict[str, FileGroup] = {}
    seen: set[str] = set()
    output = Path(target).resolve() if target else None
    excluded = {Path(p).resolve() for p in exclude_roots}
    for folder in roots:
        root = Path(folder).resolve()
        if not root.is_dir():
            raise ValueError(f'素材目录不存在：{root}')
        if root in excluded:
            continue
        # Source = target is allowed. A distinct output subtree is excluded.
        exclude = output if output and output != root and output.is_relative_to(root) else None
        for directory, dirs, names in os.walk(root, followlinks=False):
            stop()
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()
                       and not getattr(Path(directory) / d, 'is_junction', lambda: False)()
                       and d not in {'.state', '.git', '__pycache__'}
                         and (Path(directory) / d).resolve() not in excluded
                       and (exclude is None or (Path(directory) / d).resolve() != exclude)]
            for name in sorted(names):
                stop()
                path = Path(directory) / name
                if path.suffix.lower() not in VIDEO_EXTS | {'.funscript'} or path.is_symlink():
                    continue
                canonical = str(path.resolve())
                identity = os.path.normcase(canonical)
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    stat = path.stat()
                except OSError as exc:
                    log(f'无法读取，跳过：{path}：{exc}')
                    continue
                title = resource_parts(path).work
                kind = 'script' if path.suffix.lower() == '.funscript' else 'video'
                if kinds is not None and kind not in kinds:
                    continue
                core = local_group_key(path)
                # Separate keys also keep search-attempt and scoring caches isolated.
                key = core if sync_search else f'{kind}:{core}'
                group = groups.setdefault(key, FileGroup(key, title))
                group.files.append(LocalFile(canonical, stat.st_size, stat.st_mtime_ns, kind))
                if path.suffix.lower() == '.funscript' and stat.st_size < 32 * 1024 * 1024:
                    try:
                        data = json.loads(path.read_text(encoding='utf-8-sig'))
                        metadata = data.get('metadata', {}) if isinstance(data, dict) else {}
                        if isinstance(metadata, dict):
                            for key_name in ('title', 'original_name'):
                                hint = metadata.get(key_name)
                                if isinstance(hint, str) and hint.strip():
                                    hint = script_hint(hint)
                                    if title_core(hint) not in {title_core(group.title), *(title_core(h) for h in group.hints)}:
                                        group.hints.append(hint)
                            for value in metadata.values():
                                if isinstance(value, str):
                                    for topic_id in TOPIC_URL.findall(value):
                                        if int(topic_id) not in group.topic_ids:
                                            group.topic_ids.append(int(topic_id))
                    except (OSError, ValueError):
                        log(f'脚本元数据不可读，仍按文件名处理：{path.name}')
            if not recursive:
                break
    multi = [g for g in groups.values() if sum(f.kind == 'script' for f in g.files) > 1]
    if multi:
        log(f'已合并 {len(multi)} 组同作品脚本：每组仅匹配一次，保留全部版本及版本内轴文件。')
    if sync_search:
        mixed = sum({f.kind for f in g.files} == {'video', 'script'} for g in groups.values())
        log(f'同步检索已开启：{mixed} 组视频与对应脚本合并匹配。')
    else:
        log('同步检索已关闭：视频与脚本分开匹配，多轴脚本内部仍合组。')
    return sorted(groups.values(), key=lambda g: (not any(f.kind == 'script' for f in g.files), g.title.casefold()))


class TopicIndex:
    """Exact lookups plus a bigram shortlist keep large libraries responsive."""
    def __init__(self, topics: list[Topic]):
        self.topics = {t.id: t for t in topics}
        self.exact: dict[str, set[int]] = {}
        self.grams: dict[str, set[int]] = {}
        self.identifiers: dict[str, set[int]] = {}
        self.body_terms: dict[str, set[int]] = {}
        self.title_gram_sizes: dict[int, int] = {}
        self._shortlist_cache = {}
        self._query_features = {}
        for topic in topics:
            self._postings(topic, add=True)

    def _postings(self, topic, *, add):
        cores = {title_core(clean_download_name(name))
                 for name in [topic.title] + [file_title(Path(n)) for n in topic.filenames]}
        # Long collection posts contain nearly every common character pair.
        # Keep title character pairs separate from meaningful body word hits.
        grams = {text[i:i + 2] for text in cores for i in range(max(0, len(text) - 1))}
        terms = self._terms(topic.body[:24000])
        if add:
            self.title_gram_sizes[topic.id] = len(grams)
        else:
            self.title_gram_sizes.pop(topic.id, None)
        for mapping, keys in ((self.identifiers, topic.source_ids), (self.exact, cores),
                              (self.grams, grams), (self.body_terms, terms)):
            for key in keys:
                if add:
                    mapping.setdefault(key, set()).add(topic.id)
                else:
                    values = mapping.get(key)
                    if values is not None:
                        values.discard(topic.id)
                        if not values:
                            del mapping[key]

    def update(self, topics, changed):
        """Update changed postings instead of rebuilding a full author library."""
        invalidate_all = len(topics) != len(self.topics)
        changed_cores, changed_grams, changed_terms, changed_ids = set(), set(), set(), set()
        for identity in changed if not invalidate_all and self._query_features else ():
            old, new = self.topics.get(identity), topics.get(identity)
            before = self._retrieval_features(old)
            after = self._retrieval_features(new)
            changed_cores.update(before[0] ^ after[0])
            if before[1] != after[1]:
                # Overlap's denominator changes even for unchanged shared grams.
                changed_grams.update(before[1] | after[1])
            if (old.body if old else '') != (new.body if new else ''):
                # Same terms in a different order can change a full-name anchor.
                changed_terms.update(before[2] | after[2])
            changed_ids.update(before[3] ^ after[3])
        last_yield = time.monotonic()
        for position, identity in enumerate(changed):
            if identity in self.topics:
                self._postings(self.topics[identity], add=False)
            if identity in topics:
                self._postings(topics[identity], add=True)
            # Tk briefly releases the GIL for every small draw call. A tight
            # initial index build can otherwise steal it back thousands of
            # times and stretch a pending repaint into several seconds.
            if position % 32 == 0 and time.monotonic() - last_yield >= .025:
                time.sleep(.001)
                last_yield = time.monotonic()
        self.topics = topics
        if invalidate_all:
            # Topic count participates in IDF for every query.
            self._shortlist_cache.clear()
            self._query_features.clear()
        else:
            for key, (cores, grams, terms, identifiers, direct) in list(self._query_features.items()):
                if (cores & changed_cores or grams & changed_grams or terms & changed_terms
                        or identifiers & changed_ids or direct & changed):
                    self._shortlist_cache.pop(key, None)
                    del self._query_features[key]

    @classmethod
    def _retrieval_features(cls, topic):
        if topic is None:
            return (frozenset(),) * 4
        cores = {title_core(clean_download_name(name))
                 for name in [topic.title] + [file_title(Path(n)) for n in topic.filenames]}
        grams = {text[i:i+2] for text in cores for i in range(max(0, len(text)-1))}
        return cores, grams, cls._terms(topic.body[:24000]), set(topic.source_ids)

    @staticmethod
    def _terms(text):
        terms = set()
        for word in title_core(text).split():
            if word in COMMON or word.isdigit():
                continue
            if re.search(rf'[{CJK}]', word):
                terms.update('cjk:' + word[i:i+2] for i in range(len(word)-1))
            elif len(word) >= 4:
                terms.add(word)
        return terms

    def shortlist(self, group: FileGroup) -> list[Topic]:
        from .name_evidence import name_anchor
        key = group.title, tuple(group.hints), tuple(group.topic_ids)
        cached = self._shortlist_cache.get(key)
        if cached is not None:
            return [self.topics[identity] for identity in cached if identity in self.topics]
        exact = set(group.topic_ids)
        counts = Counter()
        body_counts, body_weights = Counter(), Counter()
        query_grams, query_terms = set(), set()
        query_cores, query_identifiers = set(), set()
        smallest_name_grams = float('inf')
        for name in [group.title] + group.hints:
            for identity in source_ids(name):
                query_identifiers.add(identity)
                exact.update(self.identifiers.get(identity, set()))
            core = title_core(clean_download_name(name))
            query_cores.add(core)
            exact.update(self.exact.get(core, set()))
            name_grams = {core[i:i + 2] for i in range(max(0, len(core) - 1))}
            if name_grams:
                smallest_name_grams = min(smallest_name_grams, len(name_grams))
            query_grams.update(name_grams)
            query_terms.update(self._terms(core))
        for gram in query_grams:
            counts.update(self.grams.get(gram, ()))
        # All local title grams present: check complete name spans before the
        # fuzzy budget. A long tagged title must not lose to 32 shorter near hits.
        for identity, hits in counts.items():
            if hits >= smallest_name_grams:
                topic = self.topics[identity]
                if any(name_anchor(clean_download_name(name), topic.title) >= .95
                       for name in [group.title] + group.hints):
                    exact.add(identity)
        for term in query_terms:
            hits = self.body_terms.get(term, ())
            body_counts.update(hits)
            weight = math.log1p(len(self.topics) / max(1, len(hits)))
            for identity in hits:
                body_weights[identity] += weight
        body_minimum = min(2, len(query_terms))
        body_ids = {identity for identity, hits in body_counts.items() if hits >= body_minimum}
        # A complete original name in a collection remains an explicit retrieval
        # anchor even when many near titles compete for the fuzzy budget.
        for name in [group.title] + group.hints:
            core = title_core(clean_download_name(name))
            if core and (len(core.split()) >= 3 or re.search(rf'[{CJK}]{{6,}}', core)):
                anchor = re.compile(r'(?<!\w)' + re.escape(core) + r'(?!\w)')
                for identity in body_ids:
                    if body_counts[identity] == len(query_terms) and anchor.search(title_core(self.topics[identity].body)):
                        exact.add(identity)
        # Title overlap is normalized so verbose titles do not win merely by
        # length. Body retrieval rewards nearby distinctive words; final scoring
        # still verifies the actual passage, attachments, IDs and conflicts.
        ranked = {}
        query_size = len(query_grams)
        for identity, hits in counts.items():
            # A counted hit guarantees both gram sets are nonempty.
            overlap = 2 * hits / (query_size + self.title_gram_sizes[identity])
            if overlap >= .20:
                ranked[identity] = overlap
        total_weight = sum(math.log1p(len(self.topics) / max(1, len(self.body_terms.get(term, ()))))
                           for term in query_terms)
        for identity in body_ids:
            ranked[identity] = max(ranked.get(identity, 0.),
                                   .90 * body_weights[identity] / max(.001, total_weight))
        # Exact matches and explicit references are never truncated.
        # Set/hash iteration must not randomly change the cutoff between runs.
        ids = tuple(sorted(exact | set(heapq.nsmallest(32, ranked, key=lambda identity: (-ranked[identity], identity)))))
        if len(self._shortlist_cache) >= 4096:
            oldest = next(iter(self._shortlist_cache))
            del self._shortlist_cache[oldest]
            self._query_features.pop(oldest, None)
        self._shortlist_cache[key] = ids
        self._query_features[key] = (query_cores, query_grams, query_terms, query_identifiers, set(group.topic_ids))
        return [self.topics[identity] for identity in ids if identity in self.topics]


def candidates_for(group: FileGroup, topics: list[Topic] | TopicIndex) -> list[Candidate]:
    from .evidence import candidate_for
    result = []
    for topic in topics.shortlist(group) if isinstance(topics, TopicIndex) else topics:
        candidate = candidate_for(group, topic)
        if candidate.score >= .35 or candidate.title_score >= .55:
            result.append(candidate)
    return sorted(result, key=lambda c: (-c.score, c.author.casefold(), c.topic_id))


def match_group(group: FileGroup, topics: list[Topic] | TopicIndex, threshold=.85) -> Match:
    return select_match(group, candidates_for(group, topics), threshold)


def select_match(group: FileGroup, candidates: list[Candidate], threshold=.85) -> Match:
    """Apply the same acceptance/ambiguity rules to already computed evidence."""
    if not candidates or candidates[0].score < .50:
        return Match(group, '未匹配', candidates[:8])
    best = candidates[0]
    rivals = [c for c in candidates if c.author.casefold() != best.author.casefold()]
    ambiguous = bool(rivals and best.score - rivals[0].score < .08)
    # Retain the competing author even if one author has many nearly equal titles.
    shown = candidates[:7]
    if rivals and rivals[0] not in shown:
        shown.append(rivals[0])
    if ambiguous:
        return Match(group, '多作者待核对', shown)
    if (best.score >= threshold and not best.conflict
            and ((best.eligible and best.verified) or best.author_support >= 3)):
        return Match(group, '可归类', shown, best.author, True)
    return Match(group, '待核对', shown)


def serialise(value):
    return asdict(value)
