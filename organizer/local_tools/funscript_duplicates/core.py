"""Recursive semantic JSON deduplication. Never renames, moves or deletes inputs."""
import hashlib
import json
import os
import threading
from collections import defaultdict
from decimal import Decimal, DecimalException
from pathlib import Path


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'JSON 存在重复字段：{key}')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f'JSON 包含非有限数值：{value}')


def number_key(value):
    # Do not Decimal.normalize(): its ambient precision can silently round long values.
    sign, digits, exponent = value.as_tuple()
    if not any(digits):
        return '0'
    digits = list(digits)
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    return f'{sign}:{"".join(map(str, digits))}:{exponent}'


def fingerprint(value, cancel=None):
    digest = hashlib.sha256()

    def feed(item):
        check_cancel(cancel)
        if item is None:
            digest.update(b'n;')
        elif isinstance(item, bool):
            digest.update(b't;' if item else b'f;')
        elif isinstance(item, Decimal):
            digest.update(('d'+number_key(item)+';').encode('ascii'))
        elif isinstance(item, str):
            digest.update(b's')
            digest.update(json.dumps(item, ensure_ascii=True).encode('ascii'))
        elif isinstance(item, list):
            digest.update(b'[')
            for child in item:
                feed(child)
            digest.update(b']')
        elif isinstance(item, dict):
            digest.update(b'{')
            for key in sorted(item):
                feed(key)
                feed(item[key])
            digest.update(b'}')
        else:
            raise ValueError('无法识别的 JSON 数据类型')
    feed(value)
    return digest.hexdigest()


def read_script(path, cancel=None):
    path = Path(path)
    check_cancel(cancel)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(raw) != after.st_size:
        raise ValueError('读取过程中发生变化，请重新扫描')
    check_cancel(cancel)
    try:
        data = json.loads(raw, parse_int=Decimal, parse_float=Decimal,
                          parse_constant=invalid_constant, object_pairs_hook=unique_object)
    except DecimalException as exc:
        raise ValueError('JSON 数值超出可解析范围') from exc
    if not isinstance(data, dict) or not isinstance(data.get('actions'), list):
        raise ValueError('不是有效 Funscript：根对象需包含 actions 数组')
    previous_at, previous_pos = None, None
    for action in data['actions']:
        check_cancel(cancel)
        if not isinstance(action, dict):
            raise ValueError('动作记录不是对象')
        at, pos = action.get('at'), action.get('pos')
        if (not isinstance(at, Decimal) or not isinstance(pos, Decimal) or
            not at.is_finite() or not pos.is_finite() or at < 0 or not 0 <= pos <= 100):
            raise ValueError('动作时间/位置无效（需非负时间、0—100 位置）')
        if previous_at is not None and (at < previous_at or (at == previous_at and pos != previous_pos)):
            raise ValueError('动作时间倒序，或同一时间有冲突位置；不作重复判定')
        previous_at, previous_pos = at, pos
    complete = fingerprint(data, cancel)
    # Conservative curve group: preserve EVERY field except metadata, including control flags.
    control = fingerprint({key: value for key, value in data.items() if key != 'metadata'}, cancel)
    return dict(path=str(path.absolute()), name=path.name, size=after.st_size,
                full_hash=complete, control_hash=control, byte_hash=hashlib.sha256(raw).hexdigest(),
                actions=len(data['actions']), last_at=str(previous_at) if previous_at is not None else '',
                note='空动作脚本，请核对用途' if not data['actions'] else '')


def scan_folder(folder, cancel=None, progress=None):
    return scan_folders([folder], cancel, progress, scan_mode='single')


def scan_folders(folders, cancel=None, progress=None, scan_mode='multiple'):
    if isinstance(folders, (str, os.PathLike)):
        folders = [folders]
    roots, root_keys = [], set()
    for folder in folders:
        check_cancel(cancel)
        if not str(folder).strip():
            raise ValueError('文件夹路径不能为空')
        root = Path(folder).resolve()
        if not root.is_dir():
            raise ValueError(f'文件夹不存在或无法访问：{folder}')
        key = os.path.normcase(str(root))
        if key not in root_keys:
            roots.append(root)
            root_keys.add(key)
    if not roots:
        raise ValueError('请至少添加一个有效文件夹')
    cancel = cancel if cancel is not None else threading.Event()
    records, issues = [], []
    visited_dirs, visited_files = set(), set()
    seen = 0

    def walk_error(error):
        issues.append(dict(path=str(error.filename), error=str(error)))

    def directories():
        for root in roots:
            check_cancel(cancel)
            yield from os.walk(root, followlinks=False, onerror=walk_error)

    for directory, dirs, filenames in directories():
        check_cancel(cancel)
        directory_key = os.path.normcase(os.path.abspath(directory))
        if directory_key in visited_dirs:
            dirs[:] = []
            continue
        visited_dirs.add(directory_key)
        # Junctions/reparse links can leave the chosen tree or create loops.
        for name in list(dirs):
            child = os.path.join(directory, name)
            if os.path.islink(child) or os.path.isjunction(child):
                dirs.remove(name)
                issues.append(dict(path=child, error='跳过目录链接/联接点，避免重复扫描或越出所选目录'))
        dirs.sort(key=str.casefold)
        for name in sorted(filenames, key=str.casefold):
            check_cancel(cancel)
            if Path(name).suffix.casefold() != '.funscript':
                continue
            path = Path(directory)/name
            file_key = os.path.normcase(os.path.abspath(path))
            if file_key in visited_files:
                continue
            visited_files.add(file_key)
            seen += 1
            try:
                if path.is_symlink():
                    raise ValueError('跳过文件链接，避免重复扫描同一目标')
                records.append(read_script(path, cancel))
            except (OSError, ValueError, TypeError, RecursionError) as exc:
                issues.append(dict(path=str(path), error=str(exc)))
            if progress and (seen == 1 or seen % 20 == 0):
                progress(seen, len(records), str(path))
    check_cancel(cancel)
    full, controls = defaultdict(list), defaultdict(list)
    for record in records:
        full[record['full_hash']].append(record)
        controls[record['control_hash']].append(record)
    exact_groups = [group for group in full.values() if len(group) > 1]
    metadata_groups = [group for group in controls.values()
                       if len({record['full_hash'] for record in group}) > 1]
    for groups in (exact_groups, metadata_groups):
        for group in groups:
            group.sort(key=lambda r: r['path'].casefold())
        groups.sort(key=lambda group: group[0]['path'].casefold())
    return dict(folder=str(roots[0]) if len(roots) == 1 else '', folders=[str(root) for root in roots],
                scan_mode=scan_mode, scanned=seen, valid=len(records), issues=issues,
                exact_groups=exact_groups, metadata_groups=metadata_groups)


def report_text(result):
    roots = result.get('folders', [result['folder']])
    mode = '多文件夹联合查重' if result.get('scan_mode') == 'multiple' else '单文件夹查重'
    lines = ['Funscript 查重报告（只读扫描，不删除或移动）', f'扫描模式：{mode}',
             f'扫描目录（{len(roots)} 个；包含子目录，重叠路径去重）：',
             *[f'  {root}' for root in roots],
             f"扫描 {result['scanned']} 个文件；有效 {result['valid']} 个；问题/跳过 {len(result['issues'])} 项。",
             '全部数据比较：忽略磁盘文件名、JSON 排版/对象字段顺序；数值 1 与 1.0 等价。',
             '数组顺序及所有字段值均保留。仅元数据不同组只忽略顶层 metadata，不是完全重复。',
             '报告反映扫描时的文件内容；两种分组可能重叠，不应相加统计或据此盲删。', '']
    for key, title in (('exact_groups', '全部数据相同（完全重复）'),
                       ('metadata_groups', '动作与控制数据相同，仅 metadata 不同（需核对）')):
        lines.append(f"=== {title}：{len(result[key])} 组 ===")
        for index, group in enumerate(result[key], 1):
            lines.append(f"组 {index:04d} · {len(group)} 份 · 动作点 {group[0]['actions']}")
            for record in group:
                lines.append(f"  {record['path']} | {record['size']} 字节 | {record['note']}")
                lines.append(f"    全数据 SHA256：{record['full_hash']} | 原字节 SHA256：{record['byte_hash']}")
            lines.append('')
    lines.append('=== 读取问题 / 跳过项 ===')
    lines.extend(f"{item['path']} | {item['error']}" for item in result['issues'])
    from .report_io import reload_block
    return '\n'.join(lines)+'\n'+reload_block(result)
