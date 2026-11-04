"""Reloadable reports, including legacy human-readable TXT reports."""
import json
import os
import re
from pathlib import Path

START = '=== 可重载数据 v1 ==='
END = '=== 可重载数据结束 ==='


def _path(value):
    if not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('报告包含非绝对路径或上级路径跳转')
    return value


def validate_report(data, extensions=None):
    extensions = {'.funscript'} if extensions is None else extensions
    if not isinstance(data, dict):
        raise ValueError('报告数据不是对象')
    roots = data.get('folders') or [data.get('folder')]
    if not isinstance(roots, list) or not roots:
        raise ValueError('报告缺少扫描目录')
    roots = list(dict.fromkeys(_path(root) for root in roots))
    result = dict(data, folders=roots, folder=roots[0] if len(roots) == 1 else '')
    seen_paths = {}
    for key in ('exact_groups', 'metadata_groups'):
        groups = result.get(key)
        if not isinstance(groups, list):
            raise ValueError('报告缺少查重分组')
        for group in groups:
            if not isinstance(group, list) or len(group) < 2:
                raise ValueError('查重组至少需要两个不同文件')
            paths = set()
            for item in group:
                if not isinstance(item, dict):
                    raise ValueError('文件记录无效')
                path = Path(_path(item.get('path')))
                if path.suffix.casefold() not in extensions or not any(path.is_relative_to(Path(root)) for root in roots):
                    raise ValueError('报告文件不在记录的扫描目录内，或不是支持的文件类型')
                identity = os.path.normcase(os.path.abspath(path))
                if identity in paths:
                    raise ValueError('同一个查重组重复列出了同一路径')
                paths.add(identity)
                for field in ('full_hash', 'byte_hash'):
                    if not isinstance(item.get(field), str) or not re.fullmatch('[0-9a-f]{64}', item[field]):
                        raise ValueError('报告缺少有效 SHA256 指纹，不能安全使用')
                signature = (item['full_hash'], item['byte_hash'])
                if identity in seen_paths and seen_paths[identity] != signature:
                    raise ValueError('同一路径在报告中有互相矛盾的指纹')
                seen_paths[identity] = signature
                for field in ('size', 'actions'):
                    if type(item.get(field)) is not int or item[field] < 0:
                        raise ValueError('报告文件大小或动作数无效')
                item['name'] = path.name
                item.setdefault('note', '')
                if not isinstance(item['note'], str):
                    raise ValueError('报告备注不是文本')
            if key == 'exact_groups' and len({item['full_hash'] for item in group}) != 1:
                raise ValueError('完全重复组中的内容指纹不一致')
    for field in ('scanned', 'valid'):
        if type(result.get(field)) is not int or result[field] < 0:
            raise ValueError('报告统计无效')
    issues = result.get('issues', [])
    if not isinstance(issues, list) or any(not isinstance(i, dict) or not isinstance(i.get('path'), str)
                                           or not isinstance(i.get('error'), str) for i in issues):
        raise ValueError('报告问题记录无效')
    result['issues'] = issues
    result['scan_mode'] = 'multiple' if result.get('scan_mode') == 'multiple' or len(roots) > 1 else 'single'
    return result


def _unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError('报告 JSON 包含重复字段')
        data[key] = value
    return data


def load_report(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    if START in text:
        payload = text.split(START, 1)[1]
        if END not in payload:
            raise ValueError('报告数据段不完整')
        envelope = json.loads(payload.rsplit(END, 1)[0].strip(), object_pairs_hook=_unique)
        if not isinstance(envelope, dict) or envelope.get('format') != 'funscript-duplicates' or envelope.get('version') != 1:
            raise ValueError('不支持的报告版本')
        result = validate_report(envelope.get('result'))
    else:
        result = parse_legacy(text)
    result['loaded_from'] = str(Path(path).absolute())
    return result


def parse_legacy(text):
    if not text.startswith('Funscript 查重报告'):
        raise ValueError('不是本工具的查重报告')
    result = dict(folders=[], exact_groups=[], metadata_groups=[], issues=[])
    roots_pending = False
    section = None
    current = None
    pending = None
    expected_count = None
    section_counts = {}
    found_issues = False
    def finish_group():
        if pending is not None or (current is not None and len(current) != expected_count):
            raise ValueError('旧版报告的文件记录不完整，请使用完整报告')
    for line in text.splitlines():
        if line.startswith('扫描目录：'):
            result['folders'].append(line.split('：', 1)[1])
        elif line.startswith('扫描目录（'):
            roots_pending = True
        elif roots_pending and line.startswith('  '):
            result['folders'].append(line[2:])
        else:
            roots_pending = False
        counts = re.match(r'扫描 (\d+) 个文件；有效 (\d+) 个', line)
        if counts:
            result['scanned'], result['valid'] = map(int, counts.groups())
        if line.startswith('=== '):
            finish_group()
            current = None
            if '全部数据相同' in line:
                section = 'exact_groups'
            elif '仅 metadata 不同' in line:
                section = 'metadata_groups'
            elif '读取问题' in line:
                section = 'issues'
                found_issues = True
            else:
                section = None
            if section in ('exact_groups', 'metadata_groups'):
                count = re.search(r'：(\d+) 组 ===$', line)
                if not count or section in section_counts:
                    raise ValueError('旧版报告分组标题无效或重复')
                section_counts[section] = int(count[1])
            continue
        group = re.match(r'组 \d+ · (\d+) 份 · 动作点 (\d+)$', line)
        if group and section in ('exact_groups', 'metadata_groups'):
            finish_group()
            current = []
            expected_count, actions = map(int, group.groups())
            result[section].append(current)
            continue
        file_line = re.match(r'^  (.+) \| (\d+) 字节 \| (.*)$', line)
        if file_line and current is not None:
            if pending is not None:
                raise ValueError('旧版报告缺少文件指纹')
            filename, size, note = file_line.groups()
            pending = dict(path=filename, size=int(size), actions=actions, note=note)
            continue
        hashes = re.match(r'^    全数据 SHA256：([0-9a-f]{64}) \| 原字节 SHA256：([0-9a-f]{64})$', line)
        if hashes and pending is not None:
            pending.update(full_hash=hashes[1], byte_hash=hashes[2])
            current.append(pending)
            pending = None
        elif section == 'issues' and ' | ' in line:
            filename, error = line.split(' | ', 1)
            result['issues'].append(dict(path=filename, error=error))
    finish_group()
    if not found_issues or set(section_counts) != {'exact_groups', 'metadata_groups'} or any(
            len(result[key]) != count for key, count in section_counts.items()):
        raise ValueError('旧版报告缺少完整分组或结尾，请选择完整报告')
    result['scan_mode'] = 'multiple' if '多文件夹联合查重' in text or len(result['folders']) > 1 else 'single'
    return validate_report(result)


def reload_block(result):
    return '\n'+START+'\n'+json.dumps(dict(format='funscript-duplicates', version=1, result=result),
                                          ensure_ascii=True, separators=(',', ':'))+'\n'+END+'\n'
