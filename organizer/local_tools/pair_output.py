"""File-group output for local pairing. No matching or scoring happens here."""
import json
import os
from pathlib import Path
import shutil
import tempfile

from ..model import file_title
from ..moves import no_links, publish, safe_author


def identity(path):
    return os.path.normcase(os.path.abspath(path))


def script_members(row, moved=None):
    primary = Path(row['funscript_path'])
    paths = row.get('script_paths')
    if not paths:
        # A manually accepted playback pair may only name one axis. Include its
        # exact work/version siblings, never neighbouring versions or prefixes.
        paths = [p for p in primary.parent.glob('*') if p.is_file()
                 and p.suffix.lower() == '.funscript'
                 and file_title(p).casefold() == file_title(primary).casefold()]
        paths.extend(primary.parent/Path(target).name for p, target in (moved or {}).items()
                     if identity(Path(p).parent) == identity(primary.parent)
                     and file_title(Path(p)).casefold() == file_title(primary).casefold())
    return list({identity(p): Path(p) for p in [primary, *paths]}.values())


def relocated_scripts(rows):
    return {identity(source): target for row in rows if row.get('status') == '已处理'
            for source, target in row.get('moved_scripts', {}).items()}


def available_script(path, moved):
    original = Path(path)
    return original if original.is_file() else Path(moved.get(identity(path), path))


def output_group(row, output, *, move=False, overwrite=False, separate=False, moved=None):
    """Transfer one complete group, restoring sources and overwritten files on error.

    Shared scripts are moved once and copied for later explicitly selected videos.
    The caller owns the batch map; failures never add incomplete destinations.
    """
    moved = moved if moved is not None else {}
    video = Path(row['video_path'])
    scripts = script_members(row, moved)
    stem = file_title(Path(row['funscript_path']))
    sources = [(video, video, stem + video.suffix, move)]
    for original in scripts:
        actual = available_script(original, moved)
        sources.append((original, actual, original.name, move and identity(original) == identity(actual)))
    names = [os.path.normcase(item[2]) for item in sources]
    if len(set(names)) != len(names):
        raise ValueError('本组存在同名脚本，无法放入同一文件夹；请先核对来源。')
    for _, actual, _, _ in sources:
        no_links(actual)
        if not actual.is_file():
            raise FileNotFoundError(f'配对文件已不存在，整组未处理：{actual}')
    output = Path(output).absolute()
    no_links(output)
    output.mkdir(parents=True, exist_ok=True)
    destination = output
    if separate or row.get('multi_match'):
        destination = unique_folder(output, video.stem)
    elif not overwrite and any((output/name).exists() and identity(actual) != identity(output/name)
                               for _, actual, name, _ in sources):
        # A conflict on any axis isolates the whole group, keeping its pairing.
        destination = unique_folder(output, video.stem)
    plans = [(original, actual, destination/name, moving) for original, actual, name, moving in sources]
    for _, _, target, _ in plans:
        no_links(target)
        if target.exists() and not target.is_file():
            raise ValueError(f'目标不是普通文件：{target}')
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.pair-', dir=destination))
    completed, backups = [], []
    try:
        (staging/'recovery.json').write_text(json.dumps([
            dict(source=str(actual), target=str(target), part=f'{n}.part', moving=moving)
            for n, (_, actual, target, moving) in enumerate(plans)
        ], ensure_ascii=False, indent=2), encoding='utf-8')
        for n, (original, actual, target, moving) in enumerate(plans):
            if identity(actual) == identity(target):
                continue
            part = staging / f'{n}.part'
            # Track before copying: a failed copy can leave a partial file.
            record = dict(source=actual, target=target, part=part, moving=moving, staged=False, published=False)
            completed.append(record)
            if moving:
                shutil.move(str(actual), str(part))
            else:
                shutil.copy2(actual, part)
            record['staged'] = True
        for record in completed:
            target = record['target']
            if target.exists():
                if not overwrite:
                    raise FileExistsError(f'目标在整理期间出现同名文件：{target}')
                backup = staging / f'backup-{len(backups)}'
                publish(target, backup)
                backups.append((backup, target))
            publish(record['part'], target)
            record['published'] = True
    except Exception as error:
        recovery_errors = []
        for record in reversed(completed):
            saved = record['target'] if record['published'] else record['part']
            try:
                if saved.exists():
                    if record['moving'] and record['staged']:
                        if record['source'].exists():
                            raise FileExistsError(f'原位置出现新文件，恢复文件保留在 {saved}')
                        shutil.move(str(saved), str(record['source']))
                    else:
                        saved.unlink()
            except OSError as exc:
                recovery_errors.append(str(exc))
        for backup, target in reversed(backups):
            try:
                publish(backup, target)
            except OSError as exc:
                recovery_errors.append(str(exc))
        if recovery_errors:
            raise OSError(f'整理失败，部分文件等待恢复，保留恢复目录 {staging}：' + '；'.join(recovery_errors)) from error
        shutil.rmtree(staging)
        if destination != output:
            try:
                destination.rmdir()
            except OSError:
                pass
        raise
    # Publication is committed. A locked backup must not turn a successful move
    # into a retryable failure (the original paths no longer exist).
    row.pop('output_note', None)
    try:
        shutil.rmtree(staging)
    except OSError as exc:
        row['output_note'] = f'整组已完成，临时备份暂未清理：{staging}（{exc}）'
    row['output_path'] = str(plans[0][2])
    row['output_script_paths'] = [str(p[2]) for p in plans[1:]]
    row['script_paths'] = [str(p) for p in scripts]
    row['moved_scripts'] = {str(original): str(target) for original, _, target, moving in plans[1:] if moving}
    for original, target in row['moved_scripts'].items():
        moved[identity(original)] = target
    row.pop('process_error', None)
    return row['output_path']


def unique_folder(parent, name):
    base = safe_author(name)
    path, number = parent/base, 1
    while path.exists():
        path = parent/f'{base}_{number}'
        number += 1
    return path
