"""Explicit batch selection for multi-author results; never moves files."""
from collections import Counter
from math import isclose, isfinite


def matches_in_view(matches, view):
    """Use the same scope for preview rows and batch actions."""
    for index, match in enumerate(matches):
        if view == '可归类' and match.status not in {'可归类', '人工指定'}:
            continue
        if view == '待核对' and '核对' not in match.status:
            continue
        if view == '未匹配' and match.status != '未匹配':
            continue
        if view == '搜索失败' and match.status != '搜索失败':
            continue
        yield index, match


def can_choose_highest(match):
    return (match.status == '多作者待核对'
            and not (match.selected or match.author or match.manual or match.error))


def choose_highest_authors(matches, threshold):
    counts = Counter(selected=0, tied=0, below_threshold=0, insufficient=0, preserved=0)
    for match in matches:
        if match.selected or match.author or match.manual:
            counts['preserved'] += 1
            continue
        if not can_choose_highest(match):
            continue
        candidates = sorted((c for c in match.candidates if isfinite(c.score)),
                            key=lambda c: (-c.score, c.author.casefold(), c.topic_id))
        if not candidates:
            counts['insufficient'] += 1
            continue
        best = candidates[0]
        if any(c.author.casefold() != best.author.casefold() and isclose(c.score, best.score, abs_tol=1e-9)
               for c in candidates[1:]):
            counts['tied'] += 1
        elif best.score < threshold:
            counts['below_threshold'] += 1
        elif best.conflict or not best.author or not ((best.verified and best.eligible) or best.author_support >= 3):
            counts['insufficient'] += 1
        else:
            match.author = best.author
            match.selected = match.manual = True
            match.status = '人工指定'
            match.selection_note = f'一键取最高分：{best.author} · {best.score * 100:.1f} 分'
            counts['selected'] += 1
    return dict(counts)
