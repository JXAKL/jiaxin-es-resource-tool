"""Duration -> title -> media information, plus full-byte identity verification."""
import hashlib
import os
import time
from collections import defaultdict
from pathlib import Path

from ..funscript_duplicates.core import Cancelled, check_cancel
from .media_probe import probe_videos
from .similarity import similar_groups, options

VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts', '.mts', '.m2ts', '.mpg', '.mpeg', '.vob', '.3gp', '.ogv'}
CHUNK = 4*1024*1024
SAMPLE = 64*1024


def stat_key(stat):
    # On this Windows Python runtime path.stat and fstat can expose different ctime
    # semantics (creation vs metadata change). Identity/length/write time agree.
    identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return identity if os.name == 'nt' else identity+(stat.st_ctime_ns,)


def read_video(path, cancel=None, progress=None, expected=None, sample=False):
    path = Path(path)
    check_cancel(cancel)
    if path.is_symlink():
        raise ValueError('跳过文件链接')
    before = path.stat()
    if before.st_size <= 0:
        raise ValueError('空文件，不能作为有效视频重复项')
    if expected is not None and stat_key(before) != expected:
        raise ValueError('枚举后文件发生变化，请重新查重')
    digest = hashlib.sha256()
    count, last_notice = 0, 0.
    with path.open('rb') as handle:
        if stat_key(os.fstat(handle.fileno())) != stat_key(before):
            raise ValueError('打开过程中文件发生变化')
        if sample:
            offsets = sorted({0, max(0, before.st_size//2-SAMPLE//2), max(0, before.st_size-SAMPLE)})
            for offset in offsets:
                check_cancel(cancel)
                handle.seek(offset)
                digest.update(offset.to_bytes(8, 'big'))
                digest.update(handle.read(SAMPLE))
        else:
            for block in iter(lambda: handle.read(CHUNK), b''):
                check_cancel(cancel)
                digest.update(block)
                count += len(block)
                if progress and time.monotonic()-last_notice >= .3:
                    progress(count, before.st_size)
                    last_notice = time.monotonic()
        if stat_key(os.fstat(handle.fileno())) != stat_key(before):
            raise ValueError('读取过程中文件发生变化')
    if stat_key(path.stat()) != stat_key(before) or (not sample and count != before.st_size):
        raise ValueError('读取过程中路径/内容发生变化')
    check_cancel(cancel)
    signature = digest.hexdigest()
    return dict(path=str(path.absolute()), name=path.name, size=before.st_size, full_hash=signature,
                byte_hash=signature, actions=0, note='', hash_kind='sample' if sample else 'full',
                stat_key=stat_key(before))


def scan_folder(folder, cancel=None, progress=None, settings=None):
    return scan_folders([folder], cancel, progress, scan_mode='single', settings=settings)


def scan_folders(folders, cancel=None, progress=None, scan_mode='multiple', settings=None):
    settings = options(settings)
    if isinstance(folders, (str, os.PathLike)):
        folders = [folders]
    roots, root_keys = [], set()
    for folder in folders:
        check_cancel(cancel)
        if not str(folder).strip() or not Path(folder).is_dir():
            raise ValueError(f'目录不存在或无法访问：{folder}')
        root = Path(folder).resolve()
        key = os.path.normcase(str(root))
        if key not in root_keys:
            roots.append(root)
            root_keys.add(key)
    if not roots:
        raise ValueError('请至少选择一个目录')
    seen_dirs, seen_files = set(), set()
    records, issues = {}, []
    scanned = 0
    last_notice = 0.

    def notify(message, force=False):
        nonlocal last_notice
        if progress and (force or time.monotonic()-last_notice >= .3):
            progress(scanned, len(records), message)
            last_notice = time.monotonic()

    def failure(path, exc):
        records.pop(str(path), None)
        issues.append(dict(path=str(path), error=str(exc)))

    for root in roots:
        for directory, dirs, names in os.walk(root, followlinks=False,
                                             onerror=lambda exc: failure(exc.filename, exc)):
            check_cancel(cancel)
            key = os.path.normcase(os.path.abspath(directory))
            if key in seen_dirs:
                dirs[:] = []
                continue
            seen_dirs.add(key)
            for name in list(dirs):
                child = Path(directory)/name
                if child.is_symlink() or os.path.isjunction(child):
                    dirs.remove(name)
                    failure(child, '跳过目录链接/联接点')
            dirs.sort(key=str.casefold)
            for name in sorted(names, key=str.casefold):
                check_cancel(cancel)
                path = Path(directory)/name
                if path.suffix.casefold() not in VIDEO_EXTENSIONS:
                    continue
                key = os.path.normcase(str(path))
                if key in seen_files:
                    continue
                seen_files.add(key)
                scanned += 1
                try:
                    if path.is_symlink():
                        raise ValueError('跳过文件链接')
                    stat = path.stat()
                    if stat.st_size <= 0:
                        raise ValueError('空文件，不纳入重复分组')
                    records[str(path)] = dict(path=str(path), name=path.name, size=stat.st_size, stat_key=stat_key(stat))
                except (OSError, ValueError) as exc:
                    failure(path, exc)
                notify(f'正在枚举目录：{directory}')
    notify(f'枚举完成：{scanned} 个视频；开始读取时长与视频头（不播放）…', True)
    media_issues = []
    try:
        metadata = probe_videos(records, cancel, lambda count, total, path:
            notify(f'读取时长 / 文件信息 {count}/{total}：{Path(path).name}'))
    except (OSError, ValueError) as exc:
        metadata = {path: dict(error=f'读取视频头失败：{exc}；仍执行完整文件查重') for path in records}
    for path, record in records.items():
        check_cancel(cancel)
        info = metadata.get(path, dict(error='视频头未返回有效信息'))
        record.update(info)
        if info.get('error'):
            media_issues.append(dict(path=path, error=info['error']))
    notify('按时长窗口筛选，再核对名称与文件信息…', True)
    near_groups, compared = similar_groups(records.values(), settings, cancel, lambda count, total, pairs:
        notify(f'时长 → 名称 → 文件信息 {count}/{total}；已核对 {pairs} 对'))
    sizes = defaultdict(list)
    for record in records.values():
        sizes[record['size']].append(record)
    size_unique = sum(len(group) == 1 for group in sizes.values())
    candidates = [item for group in sizes.values() if len(group) > 1 for item in group]
    samples = defaultdict(list)
    notify(f'枚举完成：{scanned} 个视频；{len(candidates)} 个同大小候选，开始分段排除…', True)
    sampled = 0
    for index, record in enumerate(candidates, 1):
        check_cancel(cancel)
        try:
            sample = read_video(record['path'], cancel, expected=record['stat_key'], sample=True)
            samples[(sample['size'], sample['full_hash'])].append(record)
            sampled += 1
        except (OSError, ValueError) as exc:
            failure(record['path'], exc)
        notify(f'分段排除 {index}/{len(candidates)}：{Path(record["path"]).name}')
    full_candidates = [item for group in samples.values() if len(group) > 1 for item in group]
    full_groups = defaultdict(list)
    hashed = hashed_bytes = 0
    for index, record in enumerate(full_candidates, 1):
        check_cancel(cancel)
        notify(f'完整校验 {index}/{len(full_candidates)}：{Path(record["path"]).name}', True)
        try:
            full = read_video(record['path'], cancel, expected=record['stat_key'], progress=lambda count, total:
                notify(f'完整校验 {index}/{len(full_candidates)} · {count/total:.1%} · {count/1024**3:.2f}/{total/1024**3:.2f} GiB'))
            full.update({key: value for key, value in record.items() if key not in full and key != 'error'})
            full_groups[(full['size'], full['full_hash'])].append(full)
            hashed += 1
            hashed_bytes += full['size']
        except (OSError, ValueError) as exc:
            failure(record['path'], exc)
    # Recheck candidate metadata before returning a snapshot, without re-reading the whole library.
    for group in full_groups.values():
        for record in group[:]:
            check_cancel(cancel)
            try:
                if stat_key(Path(record['path']).stat()) != tuple(record['stat_key']):
                    raise ValueError('扫描期间文件已变化，不作为重复项')
            except (OSError, ValueError) as exc:
                group.remove(record)
                failure(record['path'], exc)
    groups = [sorted(group, key=lambda r: r['path'].casefold()) for group in full_groups.values() if len(group) > 1]
    groups.sort(key=lambda group: group[0]['path'].casefold())
    identities = {item['path']: index for index, group in enumerate(groups) for item in group}
    filtered = []
    for group in near_groups:
        stable = True
        for record in group:
            check_cancel(cancel)
            try:
                if stat_key(Path(record['path']).stat()) != tuple(record['stat_key']):
                    raise ValueError('扫描期间文件已变化，不作为时长/名称重复项')
            except (OSError, ValueError) as exc:
                stable = False
                failure(record['path'], exc)
        # Fully identical groups have their own confirmed category, without
        # counting the same group a second time as a duration/title suspicion.
        if stable and (any(item['path'] not in identities for item in group) or
                       len({identities.get(item['path']) for item in group}) > 1):
            filtered.append(group)
    check_cancel(cancel)
    return dict(kind='video', algorithm='duration-name-info-and-sha256-v3', folders=[str(root) for root in roots],
                folder=str(roots[0]) if len(roots) == 1 else '', scan_mode=scan_mode,
                scanned=scanned, valid=len(records), issues=issues, exact_groups=groups, metadata_groups=[],
                similar_groups=filtered, settings=settings, compared_pairs=compared, media_issues=media_issues,
                size_unique_files=size_unique, sampled_files=sampled, hashed_files=hashed, hashed_bytes=hashed_bytes)
