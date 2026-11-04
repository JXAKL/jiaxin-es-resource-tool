"""Whole-name evidence within decorated titles; extra text is not a penalty.

Unlike partial/fuzzy ratios, a strong span must account for every local token.
Numbers are compared only when the two names describe the same work.
"""
from collections import Counter
from functools import lru_cache
import re

from .model import title_core
from .names import COMMON, CJK

EDITION = frozenset({'extended', 'remix', 'alternate', 'preview', 'trailer', 'teaser',
                     'compilation', 'combined', 'sequel'})
NUMBER = re.compile(r'(?<![a-z0-9])\d+(?![a-z0-9])', re.I)
EPISODE = re.compile(r'(?<!\w)(ep(?:isode)?|part|chapter|vol(?:ume)?|no)\s*(\d+)\b'
                     r'|(?:第\s*)?(\d+)\s*([集章部话話季卷])', re.I)


@lru_cache(maxsize=32_768)
def normal_name(text):
    # 01 and 1 are the same episode, but digits inside creator IDs stay intact.
    return NUMBER.sub(lambda m: str(int(m[0])), title_core(text))


@lru_cache(maxsize=32_768)
def tokens(text):
    return tuple(text.split())


@lru_cache(maxsize=16_384)
def distinctive(name):
    core = normal_name(name)
    lexical = [w for w in tokens(core) if not w.isdigit()]
    useful = [w for w in lexical if w not in COMMON]
    return bool(useful and ((len(lexical) >= 2 and len(''.join(lexical)) >= 10)
                           or (len(lexical) == 1 and len(lexical[0]) >= 12)
                           or re.search(rf'[{CJK}]{{6,}}', core)))


@lru_cache(maxsize=32_768)
def number_fields(text):
    fields = {}
    for marker, value, cjk_value, cjk_marker in EPISODE.findall(text):
        kind = 'volume' if marker in {'vol', 'volume'} or cjk_marker == '卷' else 'season' if cjk_marker == '季' else 'episode'
        fields.setdefault(kind, set()).add(value or cjk_value)
    return fields


@lru_cache(maxsize=32_768)
def scoped_number_conflict(name, target):
    """Ignore unrelated collection counts/creator digits, retain episode changes."""
    left, right = normal_name(name), normal_name(target)
    ln, rn = set(NUMBER.findall(left)), set(NUMBER.findall(right))
    lp, rp = number_fields(left), number_fields(right)
    labelled_conflict = any(not lp[k] <= rp[k] for k in lp.keys() & rp.keys())
    # Chinese episode markers written as characters also retain their identity.
    chinese = r'[零〇一二三四五六七八九十百两]+\s*[集章部话話季卷]'
    lc, rc = set(re.findall(chinese, left)), set(re.findall(chinese, right))
    if not ((ln and rn and not ln <= rn) or labelled_conflict or (lc and rc and not lc <= rc)):
        return False
    a, b = NUMBER.sub(' ', left), NUMBER.sub(' ', right)
    a, b = re.sub(chinese, ' ', a), re.sub(chinese, ' ', b)
    a, b = ' '.join(a.split()), ' '.join(b.split())
    if not a or not b:
        return False
    # The shared text must identify the work, not just an incidental numeric ID.
    if a == b or (len(a.replace(' ', '')) >= 6 and a in b):
        return True
    aw = [w for w in a.split() if w not in COMMON]
    bw = set(b.split())
    return len(aw) >= 2 and sum(len(w) for w in aw if w in bw) >= .85 * sum(map(len, aw))


@lru_cache(maxsize=65_536)
def name_anchor(name, target, *, passage=False):
    """Return a strong score only for complete, distinctive name evidence.

    Exact spans allow wrappers. A reordered name must fit a short window, keep
    a two-word phrase and contain all tokens (including numbers/common words).
    Body callers pass one bounded passage; never pool words across a whole post.
    """
    if not distinctive(name):
        return 0.
    left, right = normal_name(name), normal_name(target)
    if not right:
        return 0.
    if left == right:
        return .98
    wanted, available = tokens(left), tokens(right)
    extra = set(available) - set(wanted)
    if not passage and extra & EDITION:
        return 0.
    pattern = r'(?<!\w)' + re.escape(left) + r'(?!\w)'
    found = re.search(pattern, right)
    if found:
        # An unnumbered base name does not identify a numbered sequel.
        following = right[found.end():].lstrip()
        if not NUMBER.search(left) and re.match(r'(?:(?:ep(?:isode)?|part|chapter|vol|no)\s+)?\d+\b', following):
            return 0.
        if not passage and scoped_number_conflict(left, right):
            return 0.
        return .97
    if scoped_number_conflict(left, right):
        return 0.
    # A bracketed creator plus a one-word work may have a game/series name
    # inserted between them. Require that explicit creator field on both sides;
    # two unstructured words in prose are not equivalent evidence.
    if not passage and len(wanted) == 2 and all(len(w) >= 4 and w not in COMMON for w in wanted):
        field = re.match(r'^\s*[\[(【]([^\])】]+)[\])】]', name)
        if field and normal_name(field[1]) == wanted[0]:
            same_field = any(normal_name(m[1]) == wanted[0]
                             for m in re.finditer(r'[\[(【]([^\])】]+)[\])】]', target))
            if same_field:
                for i, word in enumerate(available):
                    if word == wanted[0] and wanted[1] in available[i + 1:i + 5]:
                        return .96
    # Reordering creator/work fields is common, but two isolated keywords are
    # insufficient; keep those as retrieval hints rather than proof.
    if len(wanted) < 3 or len([w for w in wanted if w not in COMMON and not w.isdigit()]) < 2:
        return 0.
    required = Counter(wanted)
    if not required.keys() <= set(available):
        return 0.
    width = len(wanted) + min(4, len(wanted) // 2)
    phrases = set(zip(wanted, wanted[1:]))
    for start, word in enumerate(available):
        if word not in required:
            continue
        window = available[start:start + width]
        if required <= Counter(window) and phrases.intersection(zip(window, window[1:])):
            return .96
    return 0.
