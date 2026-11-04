"""Explainable post verification; query hit counts never increase acceptance score."""
import re
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from .model import Candidate, file_title, similarity, title_core, number_conflict
from .names import COMMON, CJK, clean_download_name, source_ids
from .name_evidence import distinctive, name_anchor, normal_name, scoped_number_conflict


@lru_cache(maxsize=16_000)
def words(text):
    return tuple(dict.fromkeys(w for w in re.findall(r'[^\W_]+', title_core(text))
                              if w not in COMMON and (len(w) >= 2 or w.isdigit())))


@lru_cache(maxsize=4096)
def coverage(name, text):
    target = title_core(text)
    tokens = words(name)
    if not tokens:
        return 0.
    # Keep common target words: a misspelling such as "dnace" must still be
    # comparable to "dance", even though exact generic local words carry no weight.
    available, lengths = target_words(target)
    score = weight_sum = 0.
    for token in tokens:
        weight = min(8, max(2, len(token)))
        weight_sum += weight
        cjk = bool(re.search(rf'[{CJK}]', token))
        if token in available or (cjk and token in target):
            score += weight
            continue
        if token.isdigit() or len(token) < 4:
            continue
        best = max((word_score(token, other) for length in range(len(token) - 2, len(token) + 3)
                    for other in lengths.get(length, ())), default=0.)
        if best >= .80:
            score += weight * best
    return score / weight_sum if weight_sum else 0.


@lru_cache(maxsize=8192)
def target_words(text):
    available = frozenset(re.findall(r'[^\W_]+', text))
    lengths = {}
    for word in available:
        lengths.setdefault(len(word), []).append(word)
    return available, lengths


@lru_cache(maxsize=65536)
def word_score(left, right):
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    # These are upper bounds: skipping a pair below .8 preserves the old score.
    if matcher.real_quick_ratio() < .8 or matcher.quick_ratio() < .8:
        return 0.
    value = matcher.ratio()
    return value if value >= .8 else 0.


@lru_cache(maxsize=8192)
def lexical_features(text):
    return Counter(text), Counter(text.split())


def lexical_bound(left, right):
    """Upper bound for model.similarity, used only to avoid irrelevant work."""
    if not left or not right:
        return 0.
    if left == right:
        return .98
    # The character multiset bounds both directional SequenceMatcher scores.
    chars_left, words_left = lexical_features(left)
    chars_right, words_right = lexical_features(right)
    seq = 2 * sum(min(count, chars_right.get(ch, 0)) for ch, count in chars_left.items()) / (len(left) + len(right))
    dice = 2 * sum(min(count, words_right.get(w, 0)) for w, count in words_left.items()) / (sum(words_left.values()) + sum(words_right.values()))
    return min(.45 if number_conflict(left, right) else .94, .75 * seq + .25 * dice)


@lru_cache(maxsize=128)
def body_windows(body, size):
    windows = []
    seen = set()
    for passage in re.split(r'[\n。！？!?;；]+', body[:24000]):
        value = title_core(passage)
        if not value:
            continue
        tokens = value.split()
        values = [value] if len(tokens) <= size else [
            ' '.join(tokens[i:i + size]) for i in range(0, len(tokens), max(1, size // 2))]
        for window in values:
            if window not in seen:
                seen.add(window)
                windows.append((window, passage.strip()[:180]))
    return tuple(windows)


def body_similarity(name, body, stop=lambda: None):
    """Compare nearby passages, not arbitrary keyword co-occurrence over a long post."""
    if not body:
        return 0., ''
    normal = title_core(name)
    if not normal:
        return 0., ''
    best, excerpt, best_index = 0., '', float('inf')
    size = max(4, len(normal.split()) + 3)
    windows = body_windows(body, size)
    # .98 is the maximum title score; all non-identical windows are <= .94.
    # Find exact windows first so a later coverage hit cannot hide one.
    for index, (window, passage) in enumerate(windows):
        if index % 32 == 0:
            stop()
        if window == normal:
            value = similarity(name, window)[0]
            if value > .96:
                return value, passage
    pending = []
    for index, (window, passage) in enumerate(windows):
        if index % 16 == 0:
            stop()
        covered = coverage(name, window) * .96
        if covered > best:
            best, excerpt, best_index = covered, passage, index
        # No exact window remains; complete coverage is the global maximum.
        if covered >= .96:
            return covered, passage
        pending.append((lexical_bound(normal, window), index, window, passage))
    # Start from the strongest upper bounds so a good match eliminates most
    # alignments. Ties retain the original passage order, as in the old loop.
    for position, (bound, index, window, passage) in enumerate(sorted(pending, key=lambda row: (-row[0], row[1]))):
        if bound + 1e-12 < best:
            break
        if position % 16 == 0:
            stop()
        lexical = similarity(name, window)[0]
        if lexical > best or (lexical == best and index < best_index):
            best, excerpt, best_index = lexical, passage, index
    return best, excerpt


def informative(name):
    tokens = [w for w in words(name) if not w.isdigit()]
    return bool(tokens and len(''.join(tokens)) >= 5)


@lru_cache(maxsize=65_536)
def name_similarity(name, target):
    score, reason, old_conflict = similarity(name, target)
    conflict = old_conflict and scoped_number_conflict(name, target)
    anchor = name_anchor(name, target)
    if old_conflict and not conflict:
        # Recover the ordinary lexical score when an unrelated serial/count in
        # a decorated title was previously mistaken for this work's episode.
        a, b = title_core(name), title_core(target)
        if a and b:
            seq = (SequenceMatcher(None, a, b, autojunk=False).ratio()
                   + SequenceMatcher(None, b, a, autojunk=False).ratio()) / 2
            ca, cb = Counter(a.split()), Counter(b.split())
            dice = 2 * sum((ca & cb).values()) / (sum(ca.values()) + sum(cb.values()))
            score = min(.94, .75 * seq + .25 * dice)
    if not conflict:
        score = max(score, anchor)
    return score, reason, conflict


def body_name_anchor(name, body, stop, *, scan_windows=True):
    if not body or not distinctive(name):
        return 0., ''
    normal, content = normal_name(name), normal_name(body[:24000])
    # Sliding windows can cut a long name in two. Locate a complete phrase first
    # and validate only its immediate context; unrelated collection entries do
    # not contribute numbers or keywords to this evidence.
    found = re.search(r'(?<!\w)' + re.escape(normal) + r'(?!\w)', content) if normal in content else None
    if found:
        window = content[found.start():found.end()] + ' ' + ' '.join(content[found.end():].split()[:3])
        score = name_anchor(name, window, passage=True)
        if score >= .95:
            return score, content[max(0, found.start() - 20):found.end() + 60]
    if not scan_windows:
        return 0., ''
    best, excerpt = 0., ''
    for index, (window, passage) in enumerate(body_windows(body, max(4, len(title_core(name).split()) + 3))):
        if index % 32 == 0:
            stop()
        value = name_anchor(name, window, passage=True)
        if value > best:
            best, excerpt = value, passage
        if best >= .97:
            break
    return best, excerpt


def candidate_for(group, topic, stop=lambda: None):
    names = [clean_download_name(n) for n in [group.title] + group.hints]
    title_scores = [name_similarity(name, topic.title) for name in names]
    title_score, _, title_conflict = max(title_scores, key=lambda s: s[0])
    attachments = [clean_download_name(file_title(Path(n))) for n in topic.filenames]
    file_scores = [(name_similarity(name, filename), filename) for name in names for filename in attachments]
    file_result, file_name = max(file_scores, key=lambda s: s[0][0], default=((0., '', False), ''))
    attachment_score, _, file_conflict = file_result
    body_score, excerpt = max((body_similarity(name, topic.body, stop) for name in names), key=lambda s: s[0], default=(0., ''))
    combined = '\n'.join([topic.title, topic.body] + attachments)
    covered = max((coverage(name, combined) for name in names), default=0.)
    local_ids = {identity for name in [group.title] + group.hints for identity in source_ids(name)}
    id_match = bool(local_ids & set(topic.source_ids))
    direct = topic.id in group.topic_ids
    # A title's explicit episode conflict must not be overruled by a broad body hit.
    conflict = title_conflict or (file_conflict and attachment_score >= .40 and title_score < .97)
    strong_attachment = attachment_score >= .95 and any(informative(n) for n in names)
    title_anchor = max((name_anchor(name, topic.title) for name in names), default=0.)
    strong_title = title_anchor >= .95
    anchor_score, anchor_excerpt = max((body_name_anchor(name, topic.body, stop, scan_windows=body_score >= .90)
                                       for name in names), key=lambda row: row[0], default=(0., ''))
    body_anchor = anchor_score >= .95
    # 96% token coverage alone is not 96% name evidence. Reserve the >=95%
    # band for a whole name in a nearby passage, including its distinguishing words.
    body_score = max(body_score, anchor_score) if body_anchor else min(body_score, .94)
    if body_anchor:
        excerpt = anchor_excerpt
    score = .60 * max(title_score, attachment_score) + .25 * body_score + .15 * covered
    if strong_attachment:
        score = max(score, attachment_score)
    if body_anchor:
        score = max(score, anchor_score)
    if strong_title and topic.details_loaded:
        score = max(score, title_anchor)
    if id_match:
        score = max(score, .99)
    if direct:
        score = 1.
    if conflict:
        score = min(score, .45)
    supported = (direct or id_match or strong_title or strong_attachment or body_anchor or
                 (max(title_score, attachment_score) >= .72 and body_score >= .70 and covered >= .70))
    supported = supported and any(informative(n) for n in names) and not conflict
    if (direct or id_match) and not conflict:
        supported = True
    evidence = []
    if excerpt and body_score >= .5:
        evidence.append('正文对应片段：' + excerpt)
    if file_name and attachment_score >= .5:
        evidence.append('附件名称：' + file_name)
    if id_match:
        evidence.append('原文件来源编号与首帖媒体链接一致')
    if direct:
        evidence.append('脚本元数据直接指向此主题')
    if strong_title:
        evidence.append('标题完整名称吻合，附加标签不稀释名称依据')
    if body_anchor:
        evidence.append('首帖相邻片段包含完整名称，保留全部名称词与编号')
    reason = f'标题 {title_score:.0%} / 正文 {body_score:.0%} / 附件 {attachment_score:.0%} / 线索覆盖 {covered:.0%}'
    if conflict:
        reason += '；编号或集数冲突'
    elif not topic.details_loaded:
        reason += '；尚未核实首帖内容'
    elif not supported:
        reason += '；证据不足，需核对'
    return Candidate(topic.author, topic.id, topic.title, topic.url, min(1., score), reason, conflict,
                     title_score, body_score, attachment_score, covered, evidence,
                     topic.details_loaded, supported)
