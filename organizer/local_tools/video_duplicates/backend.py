"""Video-specific report identity and verification, reusing guarded move infrastructure."""
import json
import math
import os
from pathlib import Path

from .core import VIDEO_EXTENSIONS, read_video, scan_folders, scan_folder
from ..funscript_duplicates.report_io import validate_report as validate_common, _unique
from ..funscript_duplicates.organize import build_plan as common_plan, execute_plan as common_execute
from ..funscript_duplicates.report_io import _path
from ..name_match import profile as legacy_profile, compare_profiles as legacy_compare
from .names import profile, compare_profiles
from .similarity import options, duration_limit, file_information

START = '=== 视频查重可重载数据 v1 ==='
END = '=== 视频查重可重载数据结束 ==='


def _media_fields(item, required=False):
    duration = item.get('duration')
    if (required or duration is not None) and (type(duration) not in (int, float) or
            not math.isfinite(duration) or duration <= 0):
        raise ValueError('报告包含无效的视频时长')
    for field in ('width', 'height', 'fps'):
        value = item.get(field)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError('无效的视频画面/帧率信息')
    for field in ('container', 'video_codec', 'audio_codec'):
        if item.get(field) is not None and not isinstance(item[field], str):
            raise ValueError('无效的视频封装/编码信息')


def validate_report(result):
    if not isinstance(result, dict) or result.get('kind') != 'video' or result.get('algorithm') not in (
            'size-sample-full-sha256-v1', 'duration-name-info-and-sha256-v2', 'duration-name-info-and-sha256-v3'):
        raise ValueError('不是本工具支持的视频查重报告')
    result = validate_common(result, extensions=VIDEO_EXTENSIONS)
    make_profile, compare = (profile, compare_profiles) if result['algorithm'].endswith('-v3') else (legacy_profile, legacy_compare)
    if result['metadata_groups']:
        raise ValueError('视频精确查重报告不能包含近似/元数据分组')
    for group in result['exact_groups']:
        if len({record['size'] for record in group}) != 1:
            raise ValueError('视频重复组包含不同大小的文件')
        for record in group:
            if record.get('hash_kind') != 'full' or record['full_hash'] != record['byte_hash'] or record['size'] <= 0:
                raise ValueError('视频报告未记录完整文件指纹，不能据此移动')
            _media_fields(record)
    result['settings'] = options(result.get('settings'))
    similar = result.setdefault('similar_groups', [])
    if not isinstance(similar, list):
        raise ValueError('时长/名称分组无效')
    known = {os.path.normcase(item['path']): (item['size'], item.get('duration'))
             for group in result['exact_groups'] for item in group}
    for group in similar:
        if not isinstance(group, list) or len(group) < 2:
            raise ValueError('时长/名称分组至少需要两个文件')
        paths = set()
        for index, item in enumerate(group):
            if not isinstance(item, dict):
                raise ValueError('时长/名称文件记录无效')
            path = Path(_path(item.get('path')))
            identity = os.path.normcase(str(path))
            if identity in paths or path.suffix.casefold() not in VIDEO_EXTENSIONS or not any(
                    path.is_relative_to(Path(root)) for root in result['folders']):
                raise ValueError('时长/名称记录路径重复、类型错误或越出扫描范围')
            paths.add(identity)
            _media_fields(item, required=True)
            duration = item.get('duration')
            if type(item.get('size')) is not int or item['size'] <= 0:
                raise ValueError('时长/名称记录缺少有效时长或文件大小')
            signature = (item['size'], duration)
            if identity in known and (known[identity][0] != item['size'] or
                                      known[identity][1] is not None and known[identity][1] != duration):
                raise ValueError('同一路径在报告中有矛盾的时长/大小')
            known[identity] = signature
            item['name'] = path.name
            if index == 0:
                item.update(name_score=100., name_reason='此组参考文件', duration_delta=0., note='组内各项均与此参考文件直接比较')
                reference_profile = make_profile(path)
            else:
                evidence = compare(reference_profile, make_profile(path))
                delta = abs(duration-group[0]['duration'])
                if delta > duration_limit(duration, group[0]['duration'], result['settings'])+1e-9 or evidence['conflict'] or evidence['score']*100+1e-9 < result['settings']['name_threshold']:
                    raise ValueError('时长/名称分组不符合报告阈值或存在编号冲突')
                item.update(name_score=round(evidence['score']*100, 3), name_reason=evidence['reason'], duration_delta=delta)
                item['note'] = file_information(group[0], item)
    media_issues = result.setdefault('media_issues', [])
    if not isinstance(media_issues, list) or any(not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('error'), str) for item in media_issues):
        raise ValueError('视频头问题记录无效')
    return result


def report_text(result):
    result = validate_report(result)
    mode = '单文件夹' if result['scan_mode'] == 'single' else '多文件夹联合'
    settings = result['settings']
    lines = ['视频查重报告（时长 → 名称 → 文件信息；另做完整文件校验）', f'扫描模式：{mode}',
             '扫描目录（含子目录，重叠路径去重）：', *['  '+root for root in result['folders']],
             f"枚举 {result['scanned']} 个视频扩展名文件；正常枚举/未发现读取错误 {result['valid']} 个；问题 {len(result['issues'])} 项。",
             f"时长/名称相近 {len(result['similar_groups'])} 组；完整读取校验 {result.get('hashed_files', 0)} 个；完全重复 {len(result['exact_groups'])} 组。",
             f"时长差不超过 max({settings['duration_seconds']:g} 秒, 较短视频时长×{settings['duration_percent']:g}%)；名称至少 {settings['name_threshold']:g}%。",
             '先按时长窗口筛选，再比名称（保留编号/集数冲突），最后列出大小、封装、编码、画面与帧率差异。',
             '相近组的每项均直接对比第一项；不将链式相似合并。不做画面识别，相近组需核对，不进入完全重复整理。',
             '完整文件查重独立保留：同大小→分段排除→完整 SHA256。改名或无法读取时长仍可查出完全重复。',
             '统计不代表视频可播放性检查；硬链接可能共享存储空间，文件数不等于可释放磁盘空间。', '']
    lines.append('=== 时长与名称相近（需核对） ===')
    for index, group in enumerate(result['similar_groups'], 1):
        lines.append(f'相近组 {index:04d} · {len(group)} 份 · 第一项为比对参考')
        for item in group:
            lines.append(f"  {item['path']} | {item['duration']:.3f} 秒 | {item['size']} 字节 | 名称 {item['name_score']:.1f}%")
            lines.append('    '+item['note'])
        lines.append('')
    lines.append('=== 完整文件相同（完全重复） ===')
    for index, group in enumerate(result['exact_groups'], 1):
        lines.append(f"组 {index:04d} · {len(group)} 份 · 每份 {group[0]['size']} 字节")
        lines.append('  完整 SHA256：'+group[0]['full_hash'])
        lines.extend('  '+record['path'] for record in group)
        lines.append('')
    lines.append('=== 读取问题 / 跳过项 ===')
    lines.extend(item['path']+' | '+item['error'] for item in result['issues'])
    lines.append('=== 视频头读取问题（不影响完整文件查重） ===')
    lines.extend(item['path']+' | '+item['error'] for item in result['media_issues'])
    payload = dict(format='video-duplicates', version=2, result=result)
    return '\n'.join(lines)+'\n\n'+START+'\n'+json.dumps(payload, ensure_ascii=True, separators=(',', ':'))+'\n'+END+'\n'


def load_report(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    if START not in text or END not in text:
        raise ValueError('请选择本工具导出的完整视频查重 TXT 报告；Funscript 报告不能在这里使用')
    payload = json.loads(text.split(START, 1)[1].rsplit(END, 1)[0].strip(), object_pairs_hook=_unique)
    if not isinstance(payload, dict) or payload.get('format') != 'video-duplicates' or payload.get('version') not in (1, 2):
        raise ValueError('视频查重报告格式/版本不受支持')
    result = validate_report(payload.get('result'))
    result['loaded_from'] = str(Path(path).absolute())
    return result


def build_plan(result, source, reference, output):
    return common_plan(result, source, reference, output, validator=validate_report)


def execute_plan(plan, cancel=None, progress=None):
    for row in plan:
        if row.get('selected'):
            for record in (row['source'], row.get('reference')):
                if record is None or record.get('hash_kind') != 'full' or Path(record['path']).suffix.casefold() not in VIDEO_EXTENSIONS:
                    raise ValueError('只能移动有完整文件指纹的视频，请重新预览')
    return common_execute(plan, cancel, progress, reader=read_video, log_prefix='视频')
