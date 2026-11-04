"""Video names use author matching evidence; indexing must not change acceptance."""
import re
from pathlib import Path

from ...evidence import name_similarity
from ...model import file_title
from ...names import clean_download_name, source_ids
from ...name_evidence import normal_name, NUMBER, EDITION, scoped_number_conflict


def profile(path):
    stem = file_title(Path(path))
    title = clean_download_name(stem)
    core = normal_name(title)
    chars = core.replace(' ', '')
    # Only attached codes have independent identity. A loose "word 123" may
    # instead be an unrelated collection count in a decorated title.
    codes = {}
    for prefix, value in re.findall(r'(?<!\w)([a-z]{2,12})[-]?(\d{2,})(?!\w)', title.casefold()):
        codes.setdefault(prefix, set()).add(str(int(value)))
    return dict(raw=Path(path).stem.casefold(), title=title, core=core,
                source_ids=frozenset(source_ids(stem)),
                grams=set(chars[i:i+2] for i in range(len(chars)-1)) or set(chars),
                shape=NUMBER.sub('#', core), numbers=frozenset(NUMBER.findall(core)),
                versions=frozenset(str(int(n)) for n in re.findall(r'(?<!\w)(?:v|ver|version)\s*(\d+)\b', core)),
                editions=frozenset(core.split()) & EDITION, codes=codes)


def compare_profiles(a, b):
    def result(score, reason, conflict=False):
        return dict(score=score, reason=reason, conflict=conflict)

    if a['source_ids'] and b['source_ids'] and a['source_ids'].isdisjoint(b['source_ids']):
        return result(.45, '来源编号不同', True)
    if a['raw'] and a['raw'] == b['raw']:
        return result(1., '去扩展名完整同名（忽略大小写）')
    if not a['core'] or not b['core']:
        return result(0., '缺少可比较的有效标题')
    # Use both directions: either filename may contain extra creator/site tags.
    # Check conflicts first, so an identical source ID cannot hide episode edits.
    conflict = (scoped_number_conflict(a['title'], b['title']) or
                scoped_number_conflict(b['title'], a['title']) or
                bool(a['versions'] and b['versions'] and a['versions'] != b['versions']) or
                a['editions'] != b['editions'] or
                any(a['codes'][key].isdisjoint(b['codes'][key]) for key in a['codes'].keys() & b['codes'].keys()))
    if conflict:
        return result(.45, '集数、作品编号或版本冲突', True)
    if a['source_ids'] & b['source_ids']:
        return result(.99, '来源编号一致，且无集数/版本冲突')
    score, reason, _ = max((name_similarity(a['title'], b['title']),
                            name_similarity(b['title'], a['title'])), key=lambda row: row[0])
    if score >= .95:
        reason = '复用匹配规则：完整名称对应，附加标签不稀释；词序和前导零规范化'
    else:
        reason = '复用匹配规则：' + reason
    return result(score, reason)
