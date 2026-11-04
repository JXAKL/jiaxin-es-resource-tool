"""Duration windows, indexed title candidates, then explainable file information."""
import math
import time
from collections import defaultdict, deque

from .names import profile, compare_profiles
from ..funscript_duplicates.core import check_cancel

DEFAULTS = dict(duration_seconds=2., duration_percent=.5, name_threshold=90.)


def options(values=None):
    values = dict(DEFAULTS, **(values or {}))
    for key, low, high in [('duration_seconds', 0, 60), ('duration_percent', 0, 10), ('name_threshold', 50, 100)]:
        value = float(values[key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} 超出范围 {low}～{high}')
        values[key] = value
    return {key: values[key] for key in DEFAULTS}


def duration_limit(a, b, settings):
    return max(settings['duration_seconds'], min(a, b)*settings['duration_percent']/100)


def file_information(reference, item):
    parts = []
    delta = abs(reference['duration']-item['duration'])
    parts.append(f'时长差 {delta:.3f} 秒')
    parts.append(f"名称 {item['name_score']:.1f}%：{item['name_reason']}")
    ratio = min(reference['size'], item['size'])/max(reference['size'], item['size'])
    parts.append(f'文件大小接近度 {ratio:.1%}')
    for key, title in [('container', '封装'), ('video_codec', '编码'), ('audio_codec', '音频')]:
        a, b = reference.get(key), item.get(key)
        if a or b:
            parts.append(f'{title}：{a or "未知"} → {b or "未知"}')
    if reference.get('width') and item.get('width'):
        parts.append(f"画面：{reference['width']}×{reference.get('height', '?')} → {item['width']}×{item.get('height', '?')}")
    if reference.get('fps') or item.get('fps'):
        parts.append(f"帧率：{reference.get('fps', '未知')} → {item.get('fps', '未知')}")
    return '；'.join(parts)+'；时长和名称对应不代表画面完全相同'


def similar_groups(records, settings=None, cancel=None, progress=None):
    check_cancel(cancel)
    settings = options(settings)
    ordered = sorted((item for item in records if not item.get('error') and
                      isinstance(item.get('duration'), (int, float)) and item['duration'] > 0),
                     key=lambda item: (item['duration'], item['path'].casefold()))
    groups, active, inverted, expiry = [], {}, defaultdict(set), deque()
    shapes, identities = defaultdict(set), defaultdict(set)
    compared = 0
    last_yield = time.monotonic()
    # Only reference files enter the index. Every member must match its group's
    # reference directly; A~B and B~C never silently imply A~C.
    for index, item in enumerate(ordered):
        check_cancel(cancel)
        if time.monotonic()-last_yield >= .02:
            # Give Tk's main thread an uninterrupted chance to render/resize
            # even when thousands of short metadata comparisons stay in Python.
            time.sleep(.001)
            last_yield = time.monotonic()
        current = profile(item['path'])
        grams = set(current['grams']) | {'id:'+value for value in current['source_ids']}
        cutoff = item['duration']-max(settings['duration_seconds'], item['duration']*settings['duration_percent']/100)
        while expiry and expiry[0][0] < cutoff:
            _, old = expiry.popleft()
            previous, old_grams = active.pop(old)
            shape = previous['shape']
            identity = (shape, previous['numbers'])
            shapes[shape].discard(old)
            identities[identity].discard(old)
            if not shapes[shape]:
                del shapes[shape]
            if not identities[identity]:
                del identities[identity]
            for gram in old_grams:
                inverted[gram].discard(old)
                if not inverted[gram]:
                    del inverted[gram]
        candidates = set()
        for gram in grams:
            candidates.update(inverted.get(gram, ()))
        if current['numbers'] and current['shape'].replace('#', '').strip():
            # Safe pruning: identical surrounding text but different standalone
            # number sets is a scoped_number_conflict. Different surrounding
            # tags must reach the shared scorer, not a broad catalog-code filter.
            same_shape = shapes.get(current['shape'], set())
            same_numbers = identities.get((current['shape'], current['numbers']), set())
            candidates.difference_update(same_shape - same_numbers)
        best = None
        for group_index in sorted(candidates):
            check_cancel(cancel)
            if time.monotonic()-last_yield >= .02:
                time.sleep(.001)
                last_yield = time.monotonic()
            reference = groups[group_index][0]
            delta = item['duration']-reference['duration']
            if delta > duration_limit(item['duration'], reference['duration'], settings)+1e-9:
                continue
            compared += 1
            evidence = compare_profiles(active[group_index][0], current)
            if evidence['conflict'] or evidence['score']*100+1e-9 < settings['name_threshold']:
                continue
            rank = (evidence['score'], -delta, -group_index)
            if best is None or rank > best[0]:
                best = rank, group_index, evidence
        record = dict(item)
        if best is None:
            group_index = len(groups)
            record.update(name_score=100., name_reason='此组参考文件', duration_delta=0., note='组内各项均与此参考文件直接比较')
            groups.append([record])
            active[group_index] = current, grams
            shapes[current['shape']].add(group_index)
            identities[(current['shape'], current['numbers'])].add(group_index)
            expiry.append((item['duration'], group_index))
            for gram in grams:
                inverted[gram].add(group_index)
        else:
            _, group_index, evidence = best
            reference = groups[group_index][0]
            record.update(name_score=round(evidence['score']*100, 3), name_reason=evidence['reason'],
                          duration_delta=abs(reference['duration']-item['duration']))
            record['note'] = file_information(reference, record)
            groups[group_index].append(record)
        if progress and (index % 100 == 0 or index == len(ordered)-1):
            progress(index+1, len(ordered), compared)
    return [group for group in groups if len(group) > 1], compared
