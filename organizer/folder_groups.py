"""Count media groups in immediate subfolders and move only reviewed folders."""
import errno
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from .model import VIDEO_EXTS, local_group_key
from .moves import no_links, digest, relocate, append, undo


class Cancelled(Exception):
    pass


@dataclass
class FolderRow:
    source: str
    groups: int = 0
    paired: int = 0
    video_only: int = 0
    script_only: int = 0
    files: int = 0
    snapshot: dict = field(default_factory=dict)
    error: str = ''
    selected: bool = False


def threshold_value(value):
    try:
        result = int(str(value).strip())
    except ValueError:
        raise ValueError('组数阈值需要填写非负整数。') from None
    if result < 0:
        raise ValueError('组数阈值不能小于 0。')
    return result


def inventory(folder, stop=lambda: None):
    no_links(folder)
    if not folder.is_dir():
        raise ValueError(f'文件夹不存在：{folder}')
    files, directories = {}, []
    def failed(error):
        raise error
    for directory, dirs, names in os.walk(folder, followlinks=False, onerror=failed):
        stop()
        for name in dirs + names:
            no_links(Path(directory) / name)
        directories.extend((Path(directory)/name).relative_to(folder).as_posix() for name in dirs)
        for name in names:
            stop()
            path = Path(directory) / name
            if not path.is_file():
                raise ValueError(f'包含特殊文件，未统计：{path}')
            stat = path.stat()
            files[path.relative_to(folder).as_posix()] = [stat.st_size, stat.st_mtime_ns]
    return {'files': dict(sorted(files.items())), 'dirs': sorted(directories)}


def summarize(folder, snapshot):
    groups = {}
    for name in snapshot['files']:
        path = Path(name)
        if path.suffix.lower() not in VIDEO_EXTS | {'.funscript'}:
            continue
        key = local_group_key(path)
        groups.setdefault(key, set()).add('script' if path.suffix.lower() == '.funscript' else 'video')
    return FolderRow(str(folder), len(groups), sum(len(v)==2 for v in groups.values()),
                     sum(v=={'video'} for v in groups.values()), sum(v=={'script'} for v in groups.values()),
                     len(snapshot['files']), snapshot)


def scan_folders(root, destination='', stop=lambda: None, progress=lambda *_: None):
    root = Path(root).absolute(); no_links(root); root = root.resolve()
    if not root.is_dir():
        raise ValueError('请选择存在的素材根目录。')
    target = Path(destination).absolute() if destination else None
    if target:
        no_links(target); target = target.resolve()
        if target == root:
            raise ValueError('目标目录不能与扫描根目录相同。')
    rows = []
    children = sorted(root.iterdir(), key=lambda p:p.name.casefold())
    for child in children:
        stop()
        if not child.is_dir() and not child.is_symlink():
            continue
        row = FolderRow(str(child))
        progress(f'正在统计：{child.name}')
        try:
            no_links(child)
            if target and (target == child or target.is_relative_to(child)):
                raise ValueError('此文件夹包含目标目录，保留原位。')
            row = summarize(child, inventory(child, stop))
            if target and (target / child.name).exists():
                row.error = '目标已有同名项目，保留原位；不会覆盖或合并。'
        except (OSError, ValueError) as exc:
            row.error = str(exc)
        rows.append(row)
        progress(len(rows), child.name)
    return rows


def eligible(row, threshold, paired_only=False, include_zero=False):
    count = row.paired if paired_only else row.groups
    return not row.error and bool(row.snapshot) and count < threshold and (include_zero or count > 0)


def _empty_directories(root, names):
    # Only remove known empty directories. New/unexpected files are preserved.
    no_links(root)
    for relative in sorted(names, key=lambda n:len(Path(n).parts), reverse=True):
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('目录超出范围')
        no_links(path)
        if path.exists():
            path.rmdir()
    if root.exists():
        root.rmdir()


def execute(rows, root, destination, threshold, journals, *, paired_only=False, include_zero=False,
            stop=lambda: None, progress=lambda *_: None):
    threshold = threshold_value(threshold)
    root, target = Path(root).absolute(), Path(destination).absolute()
    no_links(root); no_links(target)
    root, target = root.resolve(), target.resolve()
    if root == target:
        raise ValueError('目标目录不能与扫描根目录相同。')
    selected = [r for r in rows if r.selected and eligible(r, threshold, paired_only, include_zero)]
    for row in selected:
        source = Path(row.source)
        no_links(source)
        if source.parent.resolve() != root or target.is_relative_to(source.resolve()):
            raise ValueError('移动范围已改变，或目标位于待移动文件夹内部；请重新扫描。')
    journal = Path(journals) / ('folders-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.jsonl')
    result = {'journal': str(journal), 'moved':0, 'errors':[], 'stopped':False}
    if not selected:
        return result
    target.mkdir(parents=True, exist_ok=True)
    append(journal, {'event':'folder_batch', 'version':1, 'root':str(root), 'target':str(target)})
    for row in selected:
        source = Path(row.source); destination = target / source.name
        try:
            stop(); no_links(source); no_links(destination)
            if destination.exists():
                raise FileExistsError(f'目标同名文件夹已存在，未覆盖：{destination}')
            if inventory(source, stop) != row.snapshot:
                raise ValueError(f'扫描后内容发生变化，请重新扫描：{source.name}')
            progress(f'移动文件夹：{source.name}')
            identity = uuid.uuid4().hex
            append(journal, {'event':'folder_planned','id':identity,'source':str(source),
                             'target':str(destination),'snapshot':row.snapshot})
            try:
                os.rename(source, destination)
            except OSError as exc:
                if exc.errno != errno.EXDEV and getattr(exc,'winerror',None) != 17:
                    raise
                append(journal, {'event':'folder_cross_volume','id':identity})
                destination.mkdir(exist_ok=False)
                append(journal, {'event':'folder_target_created','id':identity,
                                 'inode':destination.stat().st_ino})
                for relative in row.snapshot['dirs']:
                    (destination / relative).mkdir(parents=True, exist_ok=True)
                for name, expected in row.snapshot['files'].items():
                    stop()
                    src, dst = source / name, destination / name
                    no_links(src); no_links(dst)
                    checksum = digest(src, stop)
                    stat = src.stat()
                    if [stat.st_size, stat.st_mtime_ns] != expected:
                        raise ValueError(f'复制前文件已变化：{src}')
                    operation = {'id':uuid.uuid4().hex,'source':str(src),'target':str(dst),
                                 'size':stat.st_size,'sha256':checksum}
                    append(journal, {**operation,'event':'planned'})
                    progress(f'跨盘校验并移动：{source.name} / {name}')
                    relocate(src, dst, checksum, stop)
                    append(journal, {'event':'done','id':operation['id']})
                _empty_directories(source, row.snapshot['dirs'])
            append(journal, {'event':'folder_done','id':identity})
            result['moved'] += 1
        except Cancelled:
            result['stopped'] = True
            break
        except (OSError, ValueError) as exc:
            result['errors'].append(str(exc)); progress(str(exc))
    return result


def undo_folders(journal, stop=lambda: None, progress=lambda *_: None):
    journal = Path(journal)
    records = []
    for line in journal.read_text(encoding='utf-8').splitlines():
        try: records.append(json.loads(line))
        except ValueError: pass
    if not records or records[0].get('event') != 'folder_batch':
        raise ValueError('请选择本功能生成的文件夹移动记录。')
    plans = [r for r in records if r.get('event')=='folder_planned']
    _validate_journal(records, plans)
    cross = {r['id'] for r in records if r.get('event')=='folder_cross_volume'}
    owned = {r['id']:r['inode'] for r in records if r.get('event')=='folder_target_created'}
    restored = {r['id'] for r in records if r.get('event')=='folder_undone'}
    for row in plans:
        target = Path(row['target'])
        if row['id'] in cross and row['id'] not in restored and target.exists():
            if row['id'] not in owned or target.stat().st_ino != owned[row['id']]:
                raise ValueError(f'目标文件夹身份无法确认，未执行撤销：{target}')
    result = undo(journal, progress, stop)
    result['folders'] = 0
    for row in reversed(plans):
        stop()
        if row['id'] in restored:
            continue
        source, target = Path(row['source']), Path(row['target'])
        try:
            no_links(source); no_links(target)
            if row['id'] in cross:
                if target.exists() and (row['id'] not in owned or target.stat().st_ino != owned[row['id']]):
                    raise ValueError(f'目标文件夹身份无法确认，未清理：{target}')
                # File-level undo validates checksums; preserve any conflicts.
                source.mkdir(parents=True, exist_ok=True)
                for name in row['snapshot']['dirs']:
                    (source/name).mkdir(parents=True, exist_ok=True)
                if target.exists():
                    _empty_directories(target, row['snapshot']['dirs'])
            elif target.exists():
                if source.exists():
                    raise FileExistsError(f'原文件夹位置已被占用：{source}')
                if inventory(target, stop) != row['snapshot']:
                    raise ValueError(f'移动后内容发生变化，未自动撤销：{target}')
                source.parent.mkdir(parents=True, exist_ok=True)
                os.rename(target, source)
            elif not source.exists():
                raise FileNotFoundError(f'原路径与目标路径均不存在：{source}')
            append(journal, {'event':'folder_undone','id':row['id']})
            result['folders'] += 1
        except (OSError, ValueError) as exc:
            result['errors'].append(str(exc))
    return result


def _validate_journal(records, plans):
    """Reject malformed or out-of-scope paths before any undo mutation."""
    try:
        root, target = Path(records[0]['root']), Path(records[0]['target'])
        if not root.is_absolute() or not target.is_absolute() or root == target:
            raise ValueError()
        no_links(root); no_links(target)
        allowed = set()
        for row in plans:
            src, dst = Path(row['source']), Path(row['target'])
            if not src.is_absolute() or src.name in ('.','..') or src.parent != root or dst != target / src.name or target.is_relative_to(src):
                raise ValueError()
            no_links(src); no_links(dst)
            snapshot = row['snapshot']
            for name in list(snapshot['files']) + snapshot['dirs']:
                part = Path(name)
                if not name or part.is_absolute() or part.drive or '..' in part.parts or ':' in name:
                    raise ValueError()
            allowed.update((str(src/name), str(dst/name)) for name in snapshot['files'])
        for row in records:
            if row.get('event') == 'planned' and (row['source'], row['target']) not in allowed:
                raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError('移动记录包含无效路径或超出原扫描范围，未执行撤销。') from None
