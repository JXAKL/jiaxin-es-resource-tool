"""Search fragments are retrieval hints, never proof of authorship."""
import re
import unicodedata
from functools import lru_cache

BRACKETS = re.compile(r'\[([^\[\]]+)\]|【([^【】]+)】|\(([^()]+)\)')
NOISE = {'source', 'original', 'download', 'iwara', 'youtube', 'funscript', 'script',
         'free', 'paid', 'manual', 'ai', 'vr', '2d', '3d', 'hard', 'easy', 'soft', 'fast', 'slow'}
COMMON = NOISE | {'the', 'and', 'for', 'with', 'from', 'this', 'that', 'video', 'videos',
                  'movie', 'clip', 'dance', 'sample', 'default', 'output', 'untitled', 'filename',
                  'part', 'episode', 'chapter', 'version', 'no', 'full', 'mp4', 'mkv'}
QUALITY = re.compile(r'(?<!\w)(?:\d{3,4}p|[48]k|[hx]26[45]|hevc|avc|\d{2,3}fps)(?!\w)', re.I)
CJK = r'\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af'
RUNS = re.compile(rf'[{CJK}]+|[A-Za-zÀ-ž0-9][A-Za-zÀ-ž0-9\s\x27.:-]*')


def fold(value):
    value = unicodedata.normalize('NFKC', value).casefold()
    return re.sub(r'\s+', ' ', re.sub(r'[^\w]+', ' ', value)).strip()


def opaque_id(value):
    return bool(re.fullmatch(r'[A-Za-z0-9]{12,}', value) and re.search('[a-z]', value)
                and re.search('[A-Z]', value) and re.search('[0-9]', value))


def source_ids(title):
    return [value for match in BRACKETS.finditer(title)
            if opaque_id(value := next(v for v in match.groups() if v is not None).strip())]


@lru_cache(maxsize=50_000)
def clean_download_name(title):
    title = unicodedata.normalize('NFKC', title)
    title = re.sub(r'^\s*(?:iwara|youtube)\s*[-_:]\s*', '', title, flags=re.I)
    def bracket(match):
        value = next(v for v in match.groups() if v is not None).strip()
        return ' ' if value.casefold() in NOISE or opaque_id(value) or QUALITY.fullmatch(value) else match.group(0)
    return QUALITY.sub(' ', BRACKETS.sub(bracket, title)).strip()


@lru_cache(maxsize=50_000)
def fragments(title, limit=12):
    """Bracket fields, scripts/languages, short phrases, then typo-tolerant fallbacks."""
    clean = clean_download_name(title)
    primary, fallback = [], []
    def add(target, text):
        value = fold(text)
        if len(value) >= 3 and not value.isdigit() and value not in COMMON and value not in target:
            target.append(value)
    for identity in source_ids(title):
        add(primary, identity)
    fields = [next(v for v in m.groups() if v is not None) for m in BRACKETS.finditer(clean)]
    fields.extend(re.split(r'\s+[-–—/|]\s+|[_|｜]+', BRACKETS.sub(' ', clean)))
    for field in fields:
        field = re.sub(r'\b(?:no|part|ep|episode|chapter|vol|ver)\.?\s*\d+\b', ' ', field, flags=re.I)
        for run in RUNS.findall(field):
            value = fold(run)
            if not value:
                continue
            if re.search(rf'[{CJK}]', value):
                if len(value) <= 10:
                    add(primary, value)
                else:
                    for start in range(0, len(value), 5):
                        add(primary, value[start:start + 7])
                if len(value) >= 6:
                    for start in range(0, len(value) - 2, 3):
                        add(fallback, value[start:start + 4])
            else:
                words = value.split()
                if len(words) <= 3:
                    add(primary, value)
                else:
                    for start in range(0, len(words), 2):
                        add(primary, ' '.join(words[start:start + 3]))
                for word in words:
                    if len(word) >= 4:
                        add(fallback, word)
    ordered = list(dict.fromkeys(primary + sorted(fallback, key=len, reverse=True)))
    return tuple(ordered[:limit])


def group_fragments(group, limit=12):
    # Round robin prevents one long name from consuming every metadata-title query.
    sources = [list(fragments(name, limit)) for name in [group.title] + group.hints]
    queries = []
    for index in range(limit):
        for source in sources:
            if index < len(source) and source[index] not in queries:
                queries.append(source[index])
                if len(queries) == limit:
                    return queries
    return queries


# Retrieval-only exclusions; these must not alter evidence scoring.
SEARCH_GENERIC = COMMON | {'to', 'of', 'in', 'on', 'at', 'a', 'an', 'love', 'girl', 'girls', 'new', 'best'}


def search_plan(group, limit=6):
    identities = list(dict.fromkeys(identity for name in [group.title] + group.hints for identity in source_ids(name)))
    identity_keys = {fold(identity) for identity in identities}
    strong, weak = [], []
    for query in group_fragments(group):
        if query in identity_keys:
            continue
        useful = [word for word in query.split() if word not in SEARCH_GENERIC and not word.isdigit()]
        if not useful:
            weak.append(query)
        elif re.search(rf'[{CJK}]', query) or len(query.split()) >= 2 or len(useful[0]) >= 5:
            strong.append(query)
        else:
            weak.append(query)
    # Short/common fields can qualify a specific field, never run on their own.
    combined = [f'{query} {strong[0]}' for query in weak[:2]] if strong else []
    return list(dict.fromkeys(identities + strong + combined))[:limit]
