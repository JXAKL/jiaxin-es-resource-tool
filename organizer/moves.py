from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path


def safe_author(name: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:100]
    if not clean or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', clean, re.I):
        clean = 'author_' + clean
    if clean != name:
        clean += '_' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]
    return clean


def no_links(path: Path):
    for part in [path] + list(path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError(f'为防止路径转向，跳过符号链接或目录联接：{part}')


def digest(path: Path, stop=lambda: None) -> str:
    result = hashlib.sha256()
    with path.open('rb') as file:
        while True:
            stop()
            chunk = file.read(4 * 1024 * 1024)
            if not chunk:
                break
            result.update(chunk)
    return result.hexdigest()


def append(file: Path, entry: dict):
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def publish(source: Path, target: Path):
    """Atomic no-clobber publication on Windows and POSIX."""
    if os.name == 'nt':
        os.rename(source, target)  # Windows refuses an existing target.
    else:
        os.link(source, target)
        source.unlink()


def relocate(source: Path, target: Path, expected_hash: str, stop=lambda: None):
    no_links(source)
    no_links(target)
    if target.exists():
        raise FileExistsError(f'目标已存在：{target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    no_links(target.parent)
    try:
        publish(source, target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, 'winerror', None) != 17:
            raise
    # A separate file is copied and verified before its final name is published.
    partial = target.with_name('.organizer-' + uuid.uuid4().hex + '.partial')
    before = source.stat()
    try:
        hash_copy = hashlib.sha256()
        with source.open('rb') as src, partial.open('xb') as dst:
            while True:
                stop()
                chunk = src.read(4 * 1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                hash_copy.update(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or hash_copy.hexdigest() != expected_hash:
            raise ValueError('复制期间源文件发生变化，已保留原文件')
        if digest(partial, stop) != expected_hash:
            raise ValueError('目标校验失败，已保留原文件')
        shutil.copystat(source, partial)
        publish(partial, target)
        source.unlink()
    finally:
        if partial.exists():
            partial.unlink()


def execute(matches, destination: Path, journals: Path, log=lambda _: None, stop=lambda: None):
    # Do not resolve away directory links before inspecting them.
    no_links(destination.absolute())
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    journal = journals / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.jsonl')
    moved, skipped, errors = 0, 0, []
    for match in matches:
        stop()
        if not match.selected or not match.author:
            continue
        author_dir = destination / safe_author(match.author)
        if not author_dir.resolve().is_relative_to(destination):
            raise ValueError('作者目录超出目标范围')
        jobs, seen = [], set()
        try:
            for file in match.group.files:
                source = Path(file.path)
                target = author_dir / source.name
                no_links(source)
                no_links(target)
                if source.resolve() == target.resolve():
                    skipped += 1
                    continue
                identity = str(target).casefold()
                if target.exists() or identity in seen:
                    raise FileExistsError(f'同名冲突，整组保留原位：{target}')
                seen.add(identity)
                stat = source.stat()
                if (stat.st_size, stat.st_mtime_ns) != (file.size, file.mtime_ns):
                    raise ValueError(f'文件在扫描后发生变化，请重新分析：{source}')
                jobs.append((source, target, file))
            for source, target, file in jobs:
                stop()
                log(f'校验并移动：{source.name} → {match.author}')
                checksum = digest(source, stop)
                stat = source.stat()
                if (stat.st_size, stat.st_mtime_ns) != (file.size, file.mtime_ns):
                    raise ValueError(f'校验时文件发生变化：{source}')
                operation = dict(id=uuid.uuid4().hex, source=str(source), target=str(target),
                                 sha256=checksum, size=stat.st_size, author=match.author)
                append(journal, dict(operation, event='planned'))
                relocate(source, target, checksum, stop)
                append(journal, dict(id=operation['id'], event='done'))
                moved += 1
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            log(f'未完成：{exc}')
    return dict(journal=str(journal) if journal.exists() else '', moved=moved, skipped=skipped, errors=errors)


def undo(journal: Path, log=lambda _: None, stop=lambda: None):
    records, undone = {}, set()
    for line in journal.read_text(encoding='utf-8').splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # Power failure may leave an incomplete final line.
        if entry.get('event') == 'planned':
            records[entry['id']] = entry
        elif entry.get('event') == 'undone':
            undone.add(entry['id'])
    restored, errors = 0, []
    for identity, entry in reversed(list(records.items())):
        stop()
        if identity in undone:
            continue
        source, target = Path(entry['source']), Path(entry['target'])
        try:
            no_links(source)
            no_links(target)
            # Covers a crash after rename but before the 'done' journal entry.
            if not target.exists():
                if source.exists() and digest(source, stop) == entry['sha256']:
                    append(journal, dict(id=identity, event='undone'))
                    continue
                raise FileNotFoundError(f'找不到可恢复文件：{target}')
            if source.exists():
                raise FileExistsError(f'原路径已被占用，未覆盖：{source}')
            if target.stat().st_size != entry['size'] or digest(target, stop) != entry['sha256']:
                raise ValueError(f'归类后文件内容有变化，未移动：{target}')
            relocate(target, source, entry['sha256'], stop)
            append(journal, dict(id=identity, event='undone'))
            restored += 1
            log(f'已恢复：{source.name}')
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            log(str(exc))
    return dict(restored=restored, errors=errors)
