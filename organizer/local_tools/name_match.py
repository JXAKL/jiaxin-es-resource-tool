"""Explainable filename evidence; scores are similarities, not match probabilities."""
import difflib
import re
import unicodedata
from collections import Counter
from pathlib import Path
from ..model import file_title
from ..resource_variants import split_stem
from ..names import clean_download_name, source_ids
from ..name_evidence import name_anchor, scoped_number_conflict, distinctive

TECH = re.compile(r'(?<!\w)(?:360p|480p|720p|1080p|1440p|2160p|4320p|4k|8k|h264|h265|x264|x265|hevc|avc|\d{2,3}fps)(?!\w)', re.I)
VARIANT = re.compile(r'(?:\s*[\[({【](?:hard|easy|soft|fast|slow|intense|gentle|轻度|强力|慢速|快速)[\])}】]|[ _.-]+(?:hard|easy|soft|fast|slow|intense|gentle|轻度|强力|慢速|快速))\s*$', re.I)


def clean(text):
    return re.sub(r'\s+', ' ', re.sub(r'[_\W]+', ' ', text, flags=re.UNICODE)).strip()


def profile(path):
    path = Path(path)
    raw_stem = split_stem(path.stem, normalize=False).title if path.suffix.lower() == '.funscript' else path.stem
    stem = file_title(path)
    folded = unicodedata.normalize('NFKC', stem).casefold()
    # Keep bracket contents: a bracket may contain the entire title or episode number.
    normalized = clean(folded)
    variant_base = unicodedata.normalize('NFKC', clean_download_name(stem)).casefold()
    while VARIANT.search(variant_base):
        variant_base = VARIANT.sub('', variant_base)
    core = clean(TECH.sub(' ', variant_base.replace('_', ' ')))
    variant_key = core or normalized
    numbers = tuple(str(int(x)) for x in re.findall(r'\d+', core))
    chinese_parts = tuple(re.findall(r'(?:第\s*)?([零〇一二三四五六七八九十百两]+)\s*[集章部话話季卷]', core))
    codes = tuple((prefix, str(int(number))) for prefix, number in
                  re.findall(r'(?<![a-z0-9])([a-z]{2,12})\s*(\d{2,})(?!\d)', core))
    tokens = Counter(re.findall(r'[^\W\d_]+|\d+', core, re.UNICODE))
    chars = core.replace(' ', '')
    grams = Counter(chars[i:i+2] for i in range(len(chars)-1)) or Counter(chars)
    return dict(raw=raw_stem.casefold(), normalized=normalized, core=core, variant_key=variant_key,
                numbers=numbers, parts=chinese_parts, codes=codes, tokens=tokens, grams=grams,
                title=stem, anchor_title=clean_download_name(stem), source_ids=frozenset(source_ids(stem)),
                versions=tuple(re.findall(r'(?<!\w)(?:v|ver|version)\s*(\d+)\b', core)))


def dice(a, b):
    size = sum(a.values())+sum(b.values())
    return 2*sum((a & b).values())/size if size else 0.


def compare_profiles(a, b):
    def result(score, reason, conflict=False):
        explicit = bool(a['source_ids'] & b['source_ids'])
        meaningful = distinctive(a['core']) or distinctive(b['core']) or bool(
            re.search(r'[\u3040-\u30ff\u3400-\u9fff]{4,}', a['core']))
        return dict(score=score, reason=reason, conflict=conflict,
                    strong=score >= .95 and not conflict and (meaningful or explicit))
    if a['source_ids'] and b['source_ids'] and a['source_ids'].isdisjoint(b['source_ids']):
        return result(.45, '两份文件的来源编号不同', True)
    if a['raw'] and a['raw'] == b['raw']:
        return result(1., '去后缀完整同名（忽略大小写）')
    if a['normalized'] and a['normalized'] == b['normalized']:
        return result(.99, '全半角、大小写或分隔符规范化后相同')
    if not a['core'] or not b['core']:
        return result(0., '缺少可比较的有效标题')
    if a['core'] == b['core']:
        return result(.97, '标题相同，仅白名单画质/编码标签或强度变体后缀不同')
    conflict = bool((a['numbers'] and b['numbers'] and a['numbers'] != b['numbers'] and
                     (scoped_number_conflict(a['core'], b['core']) or scoped_number_conflict(b['core'], a['core']))) or
                    (a['parts'] and b['parts'] and a['parts'] != b['parts']) or
                    (a['versions'] and b['versions'] and a['versions'] != b['versions']) or
                    (a['codes'] and b['codes'] and not set(a['codes']) <= set(b['codes'])
                     and not set(b['codes']) <= set(a['codes'])))
    # Reuse complete-name alignment from author matching, without weakening the
    # local catalog-code/version checks or silently discarding bracket contents.
    anchor = max(name_anchor(a['anchor_title'], b['anchor_title']), name_anchor(b['anchor_title'], a['anchor_title']))
    if not conflict and a['source_ids'] & b['source_ids']:
        return result(.99, '文件来源编号完全一致（区分大小写）')
    if anchor >= .95 and not conflict:
        return result(anchor, '完整名称片段对应；附加标签不稀释，名称词与编号完整保留')
    sequence = (difflib.SequenceMatcher(None, a['core'], b['core'], autojunk=False).ratio()
                + difflib.SequenceMatcher(None, b['core'], a['core'], autojunk=False).ratio())/2
    token_score, gram_score = dice(a['tokens'], b['tokens']), dice(a['grams'], b['grams'])
    score = min(.94, .55*sequence + .25*token_score + .20*gram_score)
    if a['tokens'] == b['tokens']:
        score = .94
    reason = f'标题字符 {sequence:.0%} / 词项 {token_score:.0%} / 相邻字符 {gram_score:.0%}'
    if conflict:
        score = min(score, .45)
        reason += '；编号/集数/版本数字冲突，名称分上限 45'
    else:
        # A long shared identifier can bridge a filename that only contains its catalog ID.
        shared_ids = set(a['numbers']) & set(b['numbers'])
        long_id = any(len(number) >= 4 for number in shared_ids)
        if long_id and (not re.search(r'[^\W\d_]', a['core']) or not re.search(r'[^\W\d_]', b['core'])):
            score = max(score, .86)
            reason += '；完整长数字编号一致，但标题信息不完整，仍需核对'
    return result(score, reason, conflict)


def compare_names(path_a, path_b):
    return compare_profiles(profile(path_a), profile(path_b))


def name_similarity(path_a, path_b):
    return compare_names(path_a, path_b)['score']


def normalize_name(path):
    return profile(path)['normalized']
