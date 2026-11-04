"""Bounded local logs and factual network aggregates; no cookies or response bodies."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

ZONE = timezone(timedelta(hours=8))


def append_log(path, text, limit=5 * 1024 * 1024):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size >= limit:
        for i in (2, 1):
            old = path.with_name(path.name + f'.{i}')
            if old.exists():
                old.replace(path.with_name(path.name + f'.{i + 1}'))
        path.replace(path.with_name(path.name + '.1'))
    with path.open('a', encoding='utf-8') as stream:
        stream.write(text + '\n')


def endpoint_kind(endpoint):
    if endpoint.startswith('/t/'):
        return '/t/{topic}.json'
    if endpoint.startswith('/u/'):
        return '/u/{author}.json'
    return endpoint.split('?')[0]


def response_shape(text):
    lower = text[:4000].lower() if isinstance(text, str) else ''
    return [word for word in ('gateway timeout', 'cloudflare', 'nginx', 'overload', 'captcha') if word in lower]


class NetworkDiagnostics:
    def __init__(self, folder):
        self.folder = folder
        self.path = folder / 'network-summary.json'
        try:
            self.stats = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.stats = {}
        self.stats.setdefault('requests', 0)
        self.stats.setdefault('retries', 0)
        self.stats.setdefault('recoveries', 0)
        self.stats.setdefault('web_successes', 0)
        self.stats.setdefault('web_failures', 0)
        for key in ('endpoints', 'hours', 'outcomes', 'latency_bins', 'recent_rate_bins', 'query_failures', 'transports'):
            self.stats.setdefault(key, {})

    def event(self, event, endpoint, **fields):
        now = datetime.now(ZONE)
        row = dict(time=now.isoformat(timespec='milliseconds'), event=event,
                   endpoint=endpoint_kind(endpoint), **fields)
        try:
            append_log(self.folder / 'requests.jsonl', json.dumps(row, ensure_ascii=False))
            if event == 'request':
                self.stats['requests'] += 1
                outcome = str(fields.get('error') or fields.get('status', 'unknown'))
                failed = bool(fields.get('error')) or int(fields.get('status') or 0) >= 400
                self.stats['outcomes'][outcome] = self.stats['outcomes'].get(outcome, 0) + 1
                elapsed = fields.get('elapsed_seconds', 0)
                latency = next((name for upper, name in ((5, '<5s'), (15, '5–15s'), (30, '15–30s'), (40, '30–40s')) if elapsed < upper), '>=40s')
                rate = fields.get('requests_last_minute', 0)
                rate_bin = '<=6' if rate <= 6 else '7–15' if rate <= 15 else '>15'
                for key, value in (('endpoints', row['endpoint']), ('hours', now.strftime('%H')),
                                   ('latency_bins', latency), ('recent_rate_bins', rate_bin),
                                   ('transports', fields.get('transport', 'api'))):
                    counts = self.stats[key].setdefault(value, {'total': 0, 'failed': 0})
                    counts['total'] += 1
                    counts['failed'] += int(failed)
                if failed:
                    self.stats['last_failure'] = row
                    query = fields.get('query_id')
                    if query:
                        samples = self.stats['query_failures']
                        if query not in samples and len(samples) >= 500:
                            samples.pop(next(iter(samples)))
                        samples[query] = samples.get(query, 0) + 1
            elif event == 'retry':
                self.stats['retries'] += 1
            elif event == 'recovered':
                self.stats['recoveries'] += 1
                self.stats['last_recovery'] = row
            elif event == 'web_success':
                self.stats['web_successes'] += 1
            elif event == 'web_failed':
                self.stats['web_failures'] += 1
            self.stats['updated'] = row['time']
            self.folder.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.stats, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
            self.write_summary()
        except OSError:
            # A diagnostic write failure must not turn a successful request into a retry.
            pass

    def write_summary(self):
        s = self.stats
        lines = ['网络观察汇总（北京时间）', '',
                 f'请求 {s["requests"]} 次；自动重试 {s["retries"]} 次；恢复 {s["recoveries"]} 次。',
                 f'网页备用成功 {s["web_successes"]} 次；不可用 {s["web_failures"]} 次。',
                 '统计来自本版本开始记录的请求；失败率包含登录验证和不可见资源，不等同于限流率。',
                 '相关性不等于原因；相同接口或时段失败较多，仍不能单独证明服务器过载或请求过快。', '']
        for title, key in (('HTTP 状态 / 网络错误', 'outcomes'), ('接口', 'endpoints'), ('读取方式（新版本开始统计）', 'transports'),
                           ('小时（00–23）', 'hours'), ('请求耗时', 'latency_bins'), ('发起时最近一分钟请求数', 'recent_rate_bins')):
            lines.append(title)
            for name, count in sorted(s[key].items()):
                if isinstance(count, dict):
                    lines.append(f'  {name}: {count["failed"]}/{count["total"]} 次失败 ({count["failed"] / count["total"]:.1%})')
                else:
                    lines.append(f'  {name}: {count} 次')
            lines.append('')
        if s['query_failures']:
            lines += ['重复失败的查询摘要（最多保留 500 项，不显示原搜索词）']
            lines += [f'  {key}: {count} 次失败' for key, count in sorted(s['query_failures'].items(), key=lambda item: -item[1])[:10]]
            lines.append('')
        # Old records lack request totals and durations; do not mix them into rates.
        legacy = {}
        for path in self.folder.glob('http-*.json'):
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                key = f'{row.get("endpoint", "?")} / HTTP {row.get("status", "?")}'
                legacy[key] = legacy.get(key, 0) + 1
            except (OSError, ValueError):
                pass
        if legacy:
            lines.extend(['旧版诊断（仅异常样本，无法计算失败率）', *[f'  {k}: {v} 次' for k, v in sorted(legacy.items())], ''])
        lines += ['requests.jsonl 为逐次事件记录；日志按 5 MB 轮转，保留当前及 3 份历史。',
                  '不记录 Cookie、认证头、会话、完整网址或响应正文。查询仅记录不可逆摘要以关联重复失败。']
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.folder / f'summary-{uuid.uuid4().hex}.tmp'
        try:
            temporary.write_text('\n'.join(lines), encoding='utf-8')
            temporary.replace(self.folder / '网络规律汇总.txt')
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def query_id(url):
        query = url.partition('?')[2]
        return hashlib.sha256(query.encode()).hexdigest()[:16] if query else ''
