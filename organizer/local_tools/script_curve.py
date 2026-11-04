"""Small, dependency-free script interpolation for visual playback review."""
import json
import math
from bisect import bisect_left, bisect_right


def interpolate(at, times, values):
    index = bisect_right(times, at)
    if index == 0:
        return values[0]
    if index >= len(times):
        return values[-1]
    left = index - 1
    return values[left] + (values[index] - values[left]) * (at - times[left]) / (times[index] - times[left])


def load_script(path):
    with open(path, encoding='utf-8-sig') as file:
        data = json.load(file)
    points = {}
    for action in data.get('actions', []):
        at, pos = float(action['at']) / 1000, float(action['pos'])
        if not math.isfinite(at) or not math.isfinite(pos) or at < 0 or not 0 <= pos <= 100:
            raise ValueError('脚本含无效时间或位置数据')
        points[at] = pos
    if len(points) < 2:
        raise ValueError('脚本至少需要两个不同时间的动作点')
    times = sorted(points)
    positions = [points[at] for at in times]
    try:
        duration = float((data.get('metadata') or {}).get('duration'))
        if not math.isfinite(duration) or duration <= 0:
            duration = None
    except (TypeError, ValueError):
        duration = None
    travelled = [0.]
    gaps = []
    for i in range(1, len(times)):
        delta = abs(positions[i] - positions[i-1])
        travelled.append(travelled[-1] + delta)
        if times[i] - times[i-1] > 10 and delta:
            gaps.append((times[i-1], times[i]))
    return dict(path=str(path), times=times, positions=positions, duration=duration,
                last_action=times[-1], travelled=travelled, gaps=gaps)


def script_speed(script, times, step):
    ts, travelled = script['times'], script['travelled']
    gaps = script['gaps']
    starts = [a for a, b in gaps]
    values = []
    for at in times:
        left, right = at-step/2, at+step/2
        gap = bisect_left(starts, right) - 1
        invalid = left < ts[0] or right > ts[-1] or gap >= 0 and gaps[gap][1] > left
        values.append(math.nan if invalid else (interpolate(right, ts, travelled)-interpolate(left, ts, travelled))/step)
    return values


def curve_points(script, start, end, offset):
    times = script['times']
    a, b = max(start-offset, times[0]), min(end-offset, times[-1])
    if a > b:
        return []
    points = sorted(set([a, b] + times[bisect_left(times, a):bisect_right(times, b)]))
    return [(at+offset, interpolate(at, times, script['positions'])) for at in points]


def percentile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered)-1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high]-ordered[low]) * (index-low)
