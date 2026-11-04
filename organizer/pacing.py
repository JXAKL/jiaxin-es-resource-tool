"""Conservative, restart-persistent pacing for the tool's own forum requests."""
import json
import math
import time
from email.utils import parsedate_to_datetime


def nonnegative_seconds(value, label):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f'{label}须为大于或等于 0 的有限秒数。')
    return number


def request_limit(value):
    try:
        number = int(value)
        valid = number >= 0 and float(value) == number
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise ValueError('每分钟请求上限须为非负整数；0 表示不限。')
    return number


def retry_seconds(value, now=None, minimum=120):
    now = time.time() if now is None else now
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - now
        except (ValueError, TypeError, OverflowError):
            seconds = minimum
    return max(minimum, seconds) if math.isfinite(seconds) else minimum


class RequestPacer:
    def __init__(self, path, interval=8, search_interval=15, per_minute=6):
        self.path = path
        self.interval = nonnegative_seconds(interval, '请求间隔')
        self.search_interval = nonnegative_seconds(search_interval, '搜索间隔')
        self.per_minute = request_limit(per_minute)
        try:
            self.data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.data = {}

    def delay(self, endpoint, now):
        deadlines = [self.data.get('cooldown', 0)]
        if 'finished' in self.data:
            deadlines.append(self.data['finished'] + self.interval)
        if endpoint == '/search.json' and 'search' in self.data:
            deadlines.append(self.data['search'] + self.search_interval)
        recent = [t for t in self.data.get('recent', []) if t > now - 60]
        if self.per_minute and len(recent) >= self.per_minute:
            deadlines.append(sorted(recent)[-self.per_minute] + 60)
        return max(0., max(deadlines) - now)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data), encoding='utf-8')
        temporary.replace(self.path)

    def started(self, endpoint, now):
        # Retain the entire rolling minute so changed limits apply after restart.
        self.data['recent'] = [t for t in self.data.get('recent', []) if t > now - 60] + [now]
        if endpoint == '/search.json':
            self.data['search'] = now
        self.save()

    def finished(self, now):
        self.data['finished'] = now
        self.save()

    def cooldown(self, seconds, now):
        self.data['cooldown'] = max(self.data.get('cooldown', 0), now + seconds)
        self.save()
