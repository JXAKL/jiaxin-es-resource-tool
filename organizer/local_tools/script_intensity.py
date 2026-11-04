"""Versioned, deterministic script-motion grading (not physiological calibration)."""
import json
import math
import os
import hashlib
from bisect import bisect_left, bisect_right

VERSION = 'motion-v2-active'
RELATIVE_VERSION = 'motion-v3-batch'
DEFAULT_AVG_REFERENCE = 200.0
DEFAULT_MAX_REFERENCE = 600.0
DEFAULT_MAX_GAP = 10.0


def grade_script(path, avg_reference=DEFAULT_AVG_REFERENCE, max_reference=DEFAULT_MAX_REFERENCE,
                 max_gap=DEFAULT_MAX_GAP):
    for value in (avg_reference, max_reference):
        if not math.isfinite(value) or value <= 0:
            raise ValueError('速度参考值必须是大于零的有限数字')
    if not math.isfinite(max_gap) or max_gap < 0:
        raise ValueError('长间隔阈值必须是非负有限数字，0 表示关闭')
    with open(path, encoding='utf-8-sig') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError('脚本根节点必须是对象')
    actions = data.get('actions')
    if not isinstance(actions, list) or len(actions) < 2:
        raise ValueError('动作点不足，无法评分')
    points = {}
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError('无效动作记录')
        at, pos = action.get('at'), action.get('pos')
        if (isinstance(at, bool) or isinstance(pos, bool) or
            not isinstance(at, (int,float)) or not isinstance(pos, (int,float)) or
            not math.isfinite(at) or not math.isfinite(pos) or at < 0 or not 0 <= pos <= 100):
            raise ValueError('动作时间/位置无效')
        if at in points and points[at] != pos:
            raise ValueError('同一时间出现不同位置，瞬时速度无法定义')
        points[at] = float(pos)
    ordered = sorted(points.items())
    if len(ordered) < 2 or ordered[-1][0] <= 0:
        raise ValueError('有效时间点不足')
    last = ordered[-1][0]/1000
    duration = last
    duration_source = '最后动作时间（包含从 0 秒开始的等待）'
    notes = []
    metadata = data.get('metadata') or {}
    raw_duration = metadata.get('duration') if isinstance(metadata, dict) else None
    if raw_duration is not None:
        try:
            declared = float(raw_duration)
            if not math.isfinite(declared) or declared < last:
                raise ValueError()
            duration = declared
            duration_source = 'metadata.duration（含起止等待）'
        except (TypeError, ValueError):
            notes.append('metadata.duration 无效或早于最后动作，改用最后动作时间')
    distance = peak = 0.
    active_distance = active_duration = gap_duration = 0.
    gap_count = 0
    movement_count = reversals = 0
    previous_direction = 0
    for (a, x), (b, y) in zip(ordered, ordered[1:]):
        delta = abs(y-x)
        seconds = (b-a)/1000
        speed = delta/seconds
        distance += delta
        peak = max(peak, speed)
        if delta:
            if max_gap and seconds > max_gap:
                gap_count += 1
                gap_duration += seconds
            else:
                active_distance += delta
                active_duration += seconds
            movement_count += 1
            direction = 1 if y>x else -1
            if previous_direction and direction != previous_direction:
                reversals += 1
            previous_direction = direction
    average = distance/duration
    # Integrate distance and time over the SAME eligible moving segments.
    active_average = active_distance/active_duration if active_duration else 0.
    coverage = active_duration/duration
    score = 80*min(active_average/avg_reference, 1) + 20*min(peak/max_reference, 1)
    error = ''
    if distance == 0:
        folder = '00_静止'
    elif not active_duration:
        folder = '99_无法评估'
        score = None
        error = '所有移动段均被长间隔规则排除；请核对脚本或调整阈值'
    elif score < 20:
        folder = '01_低强度'
    elif score < 40:
        folder = '02_较低强度'
    elif score < 60:
        folder = '03_中等强度'
    elif score < 80:
        folder = '04_较高强度'
    else:
        folder = '05_高强度'
    if peak > max_reference:
        notes.append('最大速度超过参考上限，峰值项已封顶；可核对是否有异常尖峰')
    if gap_count:
        notes.append(f'排除 {gap_count} 段疑似空白长间隔（{gap_duration:.2f} 秒）；如为真实慢动作请调整阈值')
    if active_duration and (active_duration < 10 or coverage < .2):
        notes.append('有效运动不足 10 秒或占比低于 20%；分数仅代表运动段，不代表全片持续强度')
    return dict(version=VERSION, score=score, folder=folder, average=average, maximum=peak,
                absolute_score=score, absolute_folder=folder, basis='绝对强度',
                active_average=active_average, active_duration=active_duration, active_distance=active_distance,
                coverage=coverage, excluded_duration=max(0., duration-active_duration),
                gap_duration=gap_duration, gap_count=gap_count, max_gap=max_gap,
                duration=duration, duration_source=duration_source, action_count=len(actions),
                unique_points=len(ordered), movement_count=movement_count, reversals=reversals,
                distance=distance, avg_reference=avg_reference, max_reference=max_reference,
                notes='；'.join(notes), error=error)


def grade_rows(rows, avg_reference=DEFAULT_AVG_REFERENCE, max_reference=DEFAULT_MAX_REFERENCE,
               max_gap=DEFAULT_MAX_GAP):
    cache = {}
    for row in rows:
        path = row['funscript_path']
        if path not in cache:
            try:
                cache[path] = grade_script(path, avg_reference, max_reference, max_gap)
            except (ValueError, TypeError, OSError, KeyError) as exc:
                cache[path] = dict(folder='99_无法评估', score=None, error=str(exc), version=VERSION)
        row['intensity'] = dict(cache[path])
    return rows


def path_key(path):
    return os.path.normcase(os.path.realpath(path))


def metric_signature(stats):
    return tuple(stats.get(key) for key in ('active_average', 'maximum', 'duration', 'distance',
                                          'active_duration', 'avg_reference', 'max_reference', 'max_gap', 'error'))


def build_cohort(rows):
    """One vote per physical script path, not per matched video. Invalid/static excluded."""
    unique = {path_key(row['funscript_path']): row['intensity'] for row in rows}
    eligible = {path: stats for path, stats in unique.items()
                if stats.get('score') is not None and stats.get('active_duration', 0) > 0}
    averages = sorted(round(s['active_average'], 9) for s in eligible.values())
    peaks = sorted(round(s['maximum'], 9) for s in eligible.values())
    signatures = {path: metric_signature(stats) for path, stats in unique.items()}
    serial = json.dumps(sorted(signatures.items()), ensure_ascii=True, separators=(',', ':'))
    return dict(averages=averages, peaks=peaks, signatures=signatures,
                count=len(eligible), id=hashlib.sha256(serial.encode()).hexdigest()[:16])


def percentile(value, sorted_values):
    """Midrank percentile: ties share rank; an all-equal cohort yields 50, not 0/100."""
    value = round(value, 9)
    count = len(sorted_values)
    if count < 2:
        return 50.
    low, high = bisect_left(sorted_values, value), bisect_right(sorted_values, value)
    rank = (low+high-1)/2 if high > low else low-.5
    return max(0., min(100., 100*rank/(count-1)))


def apply_relative(row, cohort, strict=False):
    stats = row['intensity']
    if strict and cohort['signatures'].get(path_key(row['funscript_path'])) != metric_signature(stats):
        raise ValueError('脚本数据或评分参数已改变，请在「评分规则 / 设置」应用并刷新整批评分后再处理。')
    stats['absolute_score'] = stats.get('absolute_score', stats.get('score'))
    stats['absolute_folder'] = stats.get('absolute_folder', stats.get('folder'))
    stats['score'] = stats['absolute_score']
    stats['folder'] = stats['absolute_folder']
    stats['cohort_count'], stats['cohort_id'] = cohort['count'], cohort['id']
    stats['basis'] = '批内相对'
    stats['version'] = RELATIVE_VERSION
    if stats['score'] is None or not stats.get('active_duration'):
        return
    notes = stats.get('notes', '')
    if cohort['count'] < 2:
        stats['basis'] = '绝对强度（批内样本不足）'
        stats['notes'] = notes + '；有效运动脚本不足 2 份，回退绝对分'
        return
    a = percentile(stats['active_average'], cohort['averages'])
    p = percentile(stats['maximum'], cohort['peaks'])
    score = .8*a + .2*p
    stats.update(score=score, average_percentile=a, maximum_percentile=p)
    tiers = ['01_低强度', '02_较低强度', '03_中等强度', '04_较高强度', '05_高强度']
    stats['folder'] = tiers[min(int(score//20), 4)]
    if cohort['count'] < 5:
        notes += '；批内样本少于 5 份，相对分档较粗，建议扩大同作者样本'
    if all(values[-1]-values[0] <= max(values[-1], 1e-9)*.05
           for values in (cohort['averages'], cohort['peaks'])):
        notes += '；本批速度差异不超过 5%，相对名次差异不代表明显的实际强度差异'
    stats['notes'] = notes.strip('；')


def grade_batch(rows, avg_reference=DEFAULT_AVG_REFERENCE, max_reference=DEFAULT_MAX_REFERENCE,
                max_gap=DEFAULT_MAX_GAP, relative=True):
    # Fresh consistent parameters for the whole population; preserve processed records/results.
    fresh = [dict(row) for row in rows]
    grade_rows(fresh, avg_reference, max_reference, max_gap)
    cohort = build_cohort(fresh) if relative else None
    for original, graded in zip(rows, fresh):
        if original.get('status') == '已处理':
            continue
        if cohort is not None:
            apply_relative(graded, cohort)
        original['intensity'] = graded['intensity']
    return cohort
