"""Work families and version/axis identity, without changing playback pairing.

Only explicit, recognized suffix fields are peeled. Episode numbers, source
identifiers and unknown bracket contents are never guessed away. A family is
an inventory unit, not proof that every edit has the same playback timing.
"""
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .script_axes import AXIS_FIELD
from .names import opaque_id

_WORDS = (r'max(?:imum)?|min(?:imum)?|hard|hardcore|easy|soft|fast|slow|intense|gentle|'
          r'normal|default|original|alt(?:ernate|ernative)?|light|medium|strong|'
          r'轻度|强力|慢速|快速|普通|原版|增强版|简易版|完整版|差分(?:版)?|'
          r'高强度|低强度|中强度')
_NUMBER = r'\d+(?:\.\d+)*'
_VARIANT = re.compile(
    r'(?:' + _WORDS + r')(?:[ _-]*(?:version|ver|版))?'
    r'|(?:v(?:er(?:sion)?)?\.?|rev(?:ision)?\.?|版本)[ _-]*' + _NUMBER +
    r'|(?:alt|variant|差分)[ _-]*[a-z0-9]+'
    r'|(?:(?:speed|intensity|速度|强度)[ _:-]*)?\d+(?:\.\d+)?\s*(?:%|x|倍)', re.I)
_BRACKET = re.compile(r'\s*(?:\[([^\[\]]+)\]|\(([^()]+)\)|\{([^{}]+)\}|【([^【】]+)】)\s*$')
_PLAIN = re.compile(r'(?P<sep>[._-]\s*|\s+-\s+)(?P<label>[^\[\](){}【】]+?)\s*$')
_SPACED = re.compile(r'\s+(?P<label>(?:v(?:er(?:sion)?)?\.?|rev(?:ision)?\.?|版本)[ _-]*' + _NUMBER +
                     r'|\d{3,4}p(?:\d{2,3}fps)?|\d{2,3}fps|[248]k|[hx]26[45]|hevc|avc)\s*$', re.I)
_TECH = re.compile(r'(?:\d{3,4}p(?:\d{2,3}fps)?|\d{2,3}fps|[248]k|[hx]26[45]|hevc|avc|source)', re.I)


@dataclass(frozen=True)
class NameParts:
    work: str
    title: str  # Axis-free, with original version fields kept.
    versions: tuple[str, ...]
    axes: tuple[str, ...]

    @property
    def version(self):
        return ' / '.join(re.sub(r'[ _-]+', ' ', v).casefold() for v in self.versions) or '默认版本'


@lru_cache(maxsize=100_000)
def split_stem(stem, script=True, *, normalize=True):
    original = (unicodedata.normalize('NFKC', stem) if normalize else stem).strip()
    value, kept, titles, versions, axes = original, [], [], [], []
    while value:
        axis = AXIS_FIELD.search(value) if script else None
        # .V2/.v2 are TCode channels. Explicit lowercase bracketed (v2)
        # denotes a revision; uppercase [V2] remains an axis. Peel one field
        # at a time so an axis after (v2) cannot swallow the version too.
        if axis and re.fullmatch(r'\s*[\[({【]\s*v\d\s*[\])}】]\s*', axis.group()):
            axis = None
        if axis and axis.start() and value[:axis.start()].strip():
            axes.insert(0, axis.group().strip())
            value = value[:axis.start()].rstrip()
            continue
        match = _BRACKET.search(value)
        if match:
            label = next(s for s in match.groups() if s is not None).strip()
        else:
            # Search all delimiter starts: v1.2 contains a dot of its own.
            match = next((m for d in re.finditer(r'[._-]', value) if (m := _PLAIN.match(value, d.start()))
                          and (_VARIANT.fullmatch(m['label'].strip()) or _TECH.fullmatch(m['label'].strip()))), None)
            match = match or _SPACED.search(value)
            label = match['label'].strip() if match else ''
        if not match or not value[:match.start()].strip():
            break
        if _VARIANT.fullmatch(label):
            versions.insert(0, label)
            titles.insert(0, value[match.start():])
        elif _TECH.fullmatch(label) or opaque_id(label):
            kept.insert(0, value[match.start():])
            titles.insert(0, value[match.start():])
        else:
            break
        value = value[:match.start()].rstrip()
    return NameParts(value + ''.join(kept), value + ''.join(titles), tuple(versions), tuple(axes))


def parts(path):
    path = Path(path)
    return split_stem(path.stem, path.suffix.lower() == '.funscript')


def version_inventory(files):
    """Keep every version and its channels; never pick a largest file here."""
    groups = {}
    for file in files:
        name = parts(file.path)
        identity = (file.kind, name.version)
        item = groups.setdefault(identity, dict(kind=file.kind, version=name.version, files=[]))
        item['files'].append(dict(path=file.path, axes=list(name.axes)))
    return sorted(groups.values(), key=lambda v: (v['kind'], v['version'] != '默认版本', v['version']))


def version_summary(versions):
    return '；'.join(('脚本' if v['kind'] == 'script' else '视频') + ' ' + v['version'] +
                    f"（{len(v['files'])} 文件）" for v in versions)
