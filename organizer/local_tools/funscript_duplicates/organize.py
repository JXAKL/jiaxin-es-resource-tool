"""Plan mirrored-directory moves, verify saved fingerprints, never overwrite."""
import hashlib
import json
import os
import shutil
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from .core import check_cancel, read_script, Cancelled
from .report_io import validate_report


def safe_path(value):
    path = Path(value)
    if not str(value).strip() or not path.is_absolute() or '..' in path.parts:
        raise ValueError('请使用不含上级跳转的完整路径')
    if os.name == 'nt' and os.path.isreserved(str(path)):
        raise ValueError('整理路径不能使用 Windows 保留名称或设备路径')
    for part in path.parts[1:]:
        if any(char in part for char in '<>:"|?*'):
            raise ValueError('路径含有设备流或非法字符')
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or os.path.isjunction(ancestor):
            raise ValueError('为保护源文件，整理路径不能经过符号链接或目录联接点')
    return path.resolve()


def overlap(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def roots_for_move(result, source, reference, output):
    source, reference, output = map(safe_path, (source, reference, output))
    if overlap(source, reference):
        raise ValueError('移动方和参考方必须是互不包含的两个目录；可选择报告根目录下的两个子目录')
    if overlap(output, source) or overlap(output, reference):
        raise ValueError('输出目录必须与移动方、参考方分开，不能互为父子目录')
    roots = [Path(root) for root in result['folders']]
    if not all(any(side.is_relative_to(root) for root in roots) for side in (source, reference)):
        raise ValueError('移动方和参考方必须在报告记录的扫描范围内')
    if not source.is_dir() or not reference.is_dir():
        raise ValueError('移动方或参考方目录不存在；报告仍可查看，但不能据此移动')
    if output.exists() and not output.is_dir():
        raise ValueError('输出位置不是文件夹')
    return source, reference, output


def set_reference(row, reference_path):
    reference = next((item for item in row['references'] if item['path'] == reference_path), None)
    if reference is None:
        raise ValueError('该参考文件不在此重复组内')
    relative = Path(reference['path']).parent.relative_to(Path(row['reference_root']))
    target = safe_path(str(Path(row['output_root'])/relative/Path(row['source']['path']).name))
    if not target.is_relative_to(Path(row['output_root'])):
        raise ValueError('目标路径越出输出目录')
    row.update(reference=reference, target=str(target), state='就绪', selected=True)
    if target.exists():
        row.update(state='目标已存在（不覆盖）', selected=False)


def build_plan(result, source_root, reference_root, output_root, validator=validate_report):
    result = validator(result)
    source, reference, output = roots_for_move(result, source_root, reference_root, output_root)
    plan, used = [], set()
    for group_index, group in enumerate(result['exact_groups'], 1):
        references = [item for item in group if Path(item['path']).is_relative_to(reference)]
        if not references:
            continue
        for item in group:
            path = Path(item['path'])
            key = os.path.normcase(str(path))
            if not path.is_relative_to(source) or key in used:
                continue
            used.add(key)
            row = dict(group=group_index, source=dict(item), references=[dict(ref) for ref in references],
                       source_root=str(source), reference_root=str(reference), output_root=str(output),
                       reference=None, target='', selected=False, state='需选择参考路径')
            # Multiple copies in one reference folder give the same destination; no naming ambiguity.
            if len({os.path.normcase(str(Path(ref['path']).parent)) for ref in references}) == 1:
                try:
                    set_reference(row, references[0]['path'])
                except ValueError as exc:
                    row['state'] = str(exc)
            plan.append(row)
    targets = Counter(os.path.normcase(row['target']) for row in plan if row['target'])
    for row in plan:
        if row['state'] == '就绪' and targets[os.path.normcase(row['target'])] > 1:
            row.update(state='批内目标重名，请只勾选一项', selected=False)
    return plan


def _verify(record, cancel, reader=read_script):
    path = safe_path(record['path'])
    current = reader(path, cancel)
    if current['full_hash'] != record['full_hash'] or current['byte_hash'] != record['byte_hash']:
        raise ValueError('文件内容已不同于报告，请重新查重：'+str(path))
    return path


def _log(handle, **entry):
    handle.write(json.dumps(dict(time=datetime.now().isoformat(timespec='seconds'), **entry), ensure_ascii=True)+'\n')
    handle.flush()
    os.fsync(handle.fileno())


def _byte_hash(path, cancel=None):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            check_cancel(cancel)
            digest.update(block)
    return digest.hexdigest()


def execute_plan(plan, cancel=None, progress=None, reader=read_script, log_prefix='Funscript'):
    selected = [row for row in plan if row.get('selected')]
    if not selected:
        raise ValueError('请先勾选要移动的项')
    outputs = {row['output_root'] for row in selected}
    if len(outputs) != 1:
        raise ValueError('同一批操作只能有一个专用输出目录')
    # Validate every root boundary before even creating the log/output folder.
    for row in selected:
        roots_for_move(dict(folders=[row['source_root'], row['reference_root']]),
                       row['source_root'], row['reference_root'], row['output_root'])
    output = safe_path(next(iter(outputs)))
    check_cancel(cancel)
    output.mkdir(parents=True, exist_ok=True)
    if log_prefix not in ('Funscript', '视频'):
        raise ValueError('无效的日志类型')
    log_path = output/f'{log_prefix}移动记录_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.jsonl'
    targets = Counter(os.path.normcase(row.get('target', '')) for row in selected)
    sources = Counter(os.path.normcase(row['source']['path']) for row in selected)
    moved = failed = 0
    stopped = False
    with log_path.open('x', encoding='utf-8') as log:
        _log(log, event='开始', output=str(output), selected=len(selected))
        for index, row in enumerate(selected, 1):
            if cancel is not None and cancel.is_set():
                stopped = True
                break
            temporary = None
            published = False
            target = None
            source = None
            try:
                if not row.get('reference') or not row.get('target'):
                    raise ValueError('尚未选择参考路径')
                if targets[os.path.normcase(row['target'])] > 1 or sources[os.path.normcase(row['source']['path'])] > 1:
                    raise ValueError('本次勾选项有目标/源路径冲突，请取消其中一项再执行')
                source = safe_path(row['source']['path'])
                reference = safe_path(row['reference']['path'])
                if not source.is_relative_to(Path(row['source_root'])) or not reference.is_relative_to(Path(row['reference_root'])):
                    raise ValueError('源或参考文件越出所选目录')
                if not any(ref['path'] == row['reference']['path'] and ref['full_hash'] == row['reference']['full_hash']
                           for ref in row['references']):
                    raise ValueError('参考文件不属于预览的重复组')
                if row['source']['full_hash'] != row['reference']['full_hash']:
                    raise ValueError('不是全部数据相同的重复文件，不允许据此移动')
                target = safe_path(row['target'])
                expected = output/reference.parent.relative_to(Path(row['reference_root']))/source.name
                if target != expected or not target.is_relative_to(output):
                    raise ValueError('目标不是参考目录结构内的合法路径')
                if target.exists():
                    raise FileExistsError('目标已存在，不覆盖：'+str(target))
                if progress:
                    progress(index-1, len(selected), '正在完整核验源文件：'+source.name)
                _verify(row['source'], cancel, reader)
                if progress:
                    progress(index-1, len(selected), '正在完整核验参考文件：'+reference.name)
                _verify(row['reference'], cancel, reader)
                check_cancel(cancel)
                safe_path(str(target.parent))
                target.parent.mkdir(parents=True, exist_ok=True)
                safe_path(str(target.parent))
                temporary = target.parent/('.dedup-move-'+uuid.uuid4().hex+'.tmp')
                _log(log, event='准备移动', source=str(source), reference=str(reference), target=str(target),
                     byte_hash=row['source']['byte_hash'])
                copied = hashlib.sha256()
                before = source.stat()
                copy_bytes, last_notice = 0, 0.
                with source.open('rb') as src, temporary.open('xb') as dst:
                    for block in iter(lambda: src.read(1024*1024), b''):
                        check_cancel(cancel)
                        dst.write(block)
                        copied.update(block)
                        copy_bytes += len(block)
                        if progress and time.monotonic()-last_notice >= .5:
                            progress(index-1, len(selected), f'正在写入副本 {copy_bytes/max(before.st_size, 1):.0%}：{source.name}')
                            last_notice = time.monotonic()
                    dst.flush()
                    os.fsync(dst.fileno())
                if copied.hexdigest() != row['source']['byte_hash']:
                    raise ValueError('复制过程中源文件发生变化，保留原文件')
                shutil.copystat(source, temporary)
                check_cancel(cancel)
                safe_path(str(target))
                # Windows rename refuses an existing destination. POSIX link also publishes exclusively.
                if os.name == 'nt':
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
                    temporary.unlink()
                temporary = None
                published = True
                _log(log, event='副本写入完成，待移除源文件', source=str(source), target=str(target))
                check_cancel(cancel)
                after = source.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError('源文件在搬移期间发生变化；保留源文件和已写入副本')
                safe_path(str(source))
                if progress:
                    progress(index-1, len(selected), '移除源文件前，正在完整校验源文件和目标副本…')
                if _byte_hash(source, cancel) != row['source']['byte_hash'] or _byte_hash(target, cancel) != row['source']['byte_hash']:
                    raise ValueError('删除源文件前校验失败；保留源文件和副本')
                check_cancel(cancel)
                source.unlink()
                moved += 1
                row.update(state='已移动', selected=False)
                _log(log, event='已移动', source=str(source), target=str(target))
            except Cancelled:
                stopped = True
                row['state'] = '已停止；源文件保留' + ('，已写入目标副本' if published else '')
                _log(log, event=row['state'], source=row['source']['path'], target=row.get('target'))
                break
            except Exception as exc:
                failed += 1
                row['state'] = ('保留源文件和目标副本：' if published and source is not None and source.exists() else '未完成：')+str(exc)
                _log(log, event=row['state'], source=row['source']['path'], target=row.get('target'))
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()  # Only our unique, known temporary file; never a user script.
            if progress:
                progress(index, len(selected), row['state'])
        _log(log, event='停止' if stopped else '结束', moved=moved, failed=failed)
    return dict(moved=moved, failed=failed, stopped=stopped, log_path=str(log_path))


def plan_summary(result, source, reference):
    source, reference = Path(source), Path(reference)
    counts = dict(cross=0, source_only=0, reference_only=0, outside=0)
    for group in result['exact_groups']:
        a = any(Path(item['path']).is_relative_to(source) for item in group)
        b = any(Path(item['path']).is_relative_to(reference) for item in group)
        counts['cross' if a and b else 'source_only' if a else 'reference_only' if b else 'outside'] += 1
    return (f"报告 {len(result['exact_groups'])} 组；两方之间 {counts['cross']} 组，"
            f"仅移动方 {counts['source_only']} 组，仅参考方 {counts['reference_only']} 组，其他范围 {counts['outside']} 组。")
