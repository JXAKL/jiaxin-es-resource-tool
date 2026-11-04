from __future__ import annotations

import html
import json
import re
import threading
import time
import uuid
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlparse, parse_qs

from .diagnostics import NetworkDiagnostics, response_shape
from .model import Topic, VIDEO_EXTS
from .names import opaque_id
from .pacing import RequestPacer, retry_seconds, nonnegative_seconds

BASE = 'https://discuss.eroscripts.com'


class Cancelled(Exception):
    pass


class ForumError(Exception):
    pass


class ConnectionFailure(ForumError):
    """A connection failure is not evidence of a missing topic or author."""


def connection_code(error: Exception) -> str | None:
    text = str(error)
    if 'Execution context was destroyed' in text or 'Cannot find context with specified id' in text:
        return 'NAVIGATION_CONTEXT_LOST'
    if 'Page crashed' in text or 'page crashed' in text:
        return 'PAGE_CRASHED'
    if 'Target page, context or browser has been closed' in text or type(error).__name__ == 'TargetClosedError':
        return 'BROWSER_CLOSED'
    found = re.search(r'net::(ERR_[A-Z_]+)', text)
    transient = {
        'ERR_CONNECTION_CLOSED', 'ERR_CONNECTION_RESET', 'ERR_CONNECTION_ABORTED',
        'ERR_CONNECTION_REFUSED', 'ERR_CONNECTION_TIMED_OUT', 'ERR_TIMED_OUT',
        'ERR_NAME_NOT_RESOLVED', 'ERR_NETWORK_CHANGED', 'ERR_INTERNET_DISCONNECTED',
        'ERR_EMPTY_RESPONSE', 'ERR_PROXY_CONNECTION_FAILED', 'ERR_TUNNEL_CONNECTION_FAILED',
        'ERR_SOCKS_CONNECTION_FAILED', 'ERR_HTTP2_PROTOCOL_ERROR',
        'ERR_CONTENT_LENGTH_MISMATCH', 'ERR_ABORTED',
    }
    if found:
        return found.group(1) if found.group(1) in transient else None
    if type(error).__name__ == 'TimeoutError' or 'AbortError' in text:
        return 'TIMEOUT'
    if 'Failed to fetch' in text or 'NetworkError when attempting to fetch' in text:
        return 'FETCH_CONNECTION_FAILED'
    if 'interrupted by another navigation' in text:
        return 'NAVIGATION_INTERRUPTED'
    return None


class SearchResponseError(ForumError):
    """A search response is invalid or explicitly reports failure, not zero hits."""


class SearchTooBroad(ForumError):
    """A valid but unselective query; do not paginate or treat it as zero hits."""


class ServiceUnavailable(ConnectionFailure):
    pass


class TopicUnavailable(ForumError):
    pass


class AuthorAPIUnavailable(ForumError):
    """An author endpoint exhausted its bounded API probes."""


class AuthorActivityUnavailable(AuthorAPIUnavailable):
    """A per-author activity failure must not block the entire batch."""


def service_disabled(data):
    summary = data.get('grouped_search_result') or {}
    error = data.get('errors') or data.get('error') or (summary.get('error') if isinstance(summary, dict) else '')
    text = str(error or '').lower()
    return bool(('search' in text and 'disabled' in text) or 'extreme load' in text
                or 'too many requests' in text or 'rate limit' in text)


def search_page(data: dict) -> tuple[list[dict], bool]:
    if not isinstance(data, dict):
        raise SearchResponseError('搜索响应不是 JSON 对象')
    summary = data.get('grouped_search_result')
    if summary is not None and not isinstance(summary, dict):
        raise SearchResponseError('搜索状态字段格式异常')
    summary = summary or {}
    error = data.get('errors') or data.get('error') or summary.get('error')
    if error or data.get('success') is False or data.get('failed') is True:
        raise SearchResponseError(f'网站搜索返回错误：{str(error or data.get("message") or "请求失败")[:300]}')
    more_value = summary.get('more_full_page_results', False)
    more = more_value is True or str(more_value).lower() in {'true', '1'}
    topics = data.get('topics')
    if topics is None:
        # Topics are side-loaded through SearchPostSerializer. No post hits may
        # therefore mean there is no topics key at all in a valid response.
        recognised = isinstance(summary.get('term'), str) or isinstance(summary.get('post_ids'), list)
        no_ids = not summary.get('post_ids')
        if data.get('posts') == [] and recognised and no_ids and not more:
            return [], False
        shape = ', '.join(f'{k}:{type(v).__name__}' for k, v in sorted(data.items()))
        raise SearchResponseError(f'无法确认搜索结果，未作为空结果处理。返回字段：{shape[:300]}')
    if not isinstance(topics, list):
        raise SearchResponseError('搜索 topics 字段不是数组')
    if not topics and (data.get('posts') or summary.get('post_ids') or more):
        raise SearchResponseError('搜索响应包含命中记录或后续页，但缺少对应主题')
    result = []
    for topic in topics:
        if (not isinstance(topic, dict) or not isinstance(topic.get('id'), int)
                or isinstance(topic.get('id'), bool) or topic['id'] <= 0
                or not isinstance(topic.get('title'), str) or not topic['title'].strip()):
            raise SearchResponseError('搜索主题记录缺少有效 ID 或标题')
        result.append({'id': topic['id'], 'title': html.unescape(topic['title'])})
    return result, more


class AttachmentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.names = set()
        self.anchor = False
        self.ignored = []
        self.text = []
        self.source_ids = set()
        self.links = set()

    def add(self, value):
        value = unquote(html.unescape(value)).strip()
        name = value.replace('\\', '/').rsplit('/', 1)[-1].split('?', 1)[0]
        if Path(name).suffix.lower() in VIDEO_EXTS | {'.funscript'} and len(name) < 300:
            self.names.add(name)

    def handle_starttag(self, tag, attrs):
        void = tag in {'br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'wbr'}
        classes = dict(attrs).get('class') or ''
        if self.ignored or tag in {'blockquote', 'script', 'style'} or (tag == 'aside' and 'quote' in classes.split()):
            if not void:
                self.ignored.append(tag)
            return
        if tag in {'p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'tr'}:
            self.text.append('\n')
        if tag == 'a':
            self.anchor = True
            for key, value in attrs:
                if key in {'href', 'title', 'data-orig-filename', 'download'} and value:
                    self.add(value)
                if key == 'href' and value:
                    from .links import normalize
                    if normalized := normalize(value):
                        self.links.add(normalized)
                    parsed = urlparse(html.unescape(value))
                    if parsed.scheme in {'http', 'https'}:
                        for part in unquote(parsed.path).split('/'):
                            if opaque_id(part):
                                self.source_ids.add(part)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag in self.ignored:
                reverse_index = self.ignored[::-1].index(tag)
                del self.ignored[len(self.ignored) - reverse_index - 1:]
            return
        if tag == 'a':
            self.anchor = False
        if tag in {'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'tr'}:
            self.text.append('\n')

    def handle_data(self, data):
        if self.ignored:
            return
        self.text.append(data + ' ')
        from .links import urls
        self.links.update(urls(data))
        if self.anchor:
            self.add(data)

    def body(self):
        return '\n'.join(line for raw in ''.join(self.text).splitlines()
                         if (line := re.sub(r'\s+', ' ', raw).strip()))[:24000]


def topic_from_json(data: dict) -> Topic:
    posts = data.get('post_stream', {}).get('posts', [])
    first = next((p for p in posts if p.get('post_number') == 1), None)
    if not first or not first.get('username') or not data.get('id') or not data.get('title'):
        raise ForumError('主题数据缺少首帖作者或标题，不能作为归类依据')
    parser = AttachmentParser()
    parser.feed(first.get('cooked') or '')
    tags = [str(t.get('name', '')) if isinstance(t, dict) else str(t) for t in data.get('tags', [])]
    from .resource_info import extract_resources
    return Topic(int(data['id']), html.unescape(data['title']), first['username'],
                 f"{BASE}/t/{int(data['id'])}", data.get('created_at') or first.get('created_at') or '',
                 data.get('last_posted_at') or '', data.get('category_id'), tags,
                 sorted(parser.names), data.get('views') or 0, data.get('posts_count') or 0,
                 parser.body(), sorted(parser.source_ids), True, sorted(parser.links), True,
                 str(data.get('category_name') or ''), extract_resources(first.get('cooked') or '', BASE), True)


def author_from_input(value: str) -> str:
    value = value.strip()
    if '://' in value:
        parsed = urlparse(value)
        if parsed.scheme != 'https' or parsed.netloc.lower() != 'discuss.eroscripts.com':
            raise ValueError('请输入 EroScripts 作者主页或用户名')
        match = re.match(r'^/u/([^/]+)', parsed.path)
        if not match:
            raise ValueError('作者主页应包含 /u/用户名/')
        value = unquote(match.group(1))
    if not re.fullmatch(r'[\w.\-]{1,100}', value, re.UNICODE):
        raise ValueError('作者用户名格式无效')
    return value


class BrowserClient:
    """One browser/context per worker; no API key or password is required."""
    def __init__(self, state_dir: Path, stop: threading.Event, emit,
                 resume: threading.Event | None = None, headful=True, interval=8,
                 legacy_state: Path | None = None, browser_channel='chrome',
                 search_interval=15, per_minute=6, cooldown_seconds=120):
        self.state_dir, self.stop, self.emit = state_dir, stop, emit
        self.resume = resume
        self.headful, self.interval = headful, nonnegative_seconds(interval, '请求间隔')
        self.cooldown_seconds = nonnegative_seconds(cooldown_seconds, '异常冷却')
        if browser_channel not in ('chrome', 'msedge', 'chromium'):
            raise ValueError('请选择 Chrome、Edge 或 Chromium')
        self.browser_channel = browser_channel
        self.pacer = RequestPacer(state_dir / 'network-policy.json', self.interval, search_interval, per_minute)
        self._missing = set()
        self._network_lock = None
        self.legacy_state = legacy_state
        self.playwright = self.browser = self.context = self.page = None
        self.last_request = 0.
        self._session_confirmed = False
        self._failures = 0
        self._failure_started = None
        self._request_recent = 0
        self.diagnostics = NetworkDiagnostics(state_dir / "diagnostics")
        self._web_reader = None
        self._web_until = {}
        self._browser_restarts = []
        self._last_session_save = 0.

    def read_web(self, endpoint, params=None):
        from .web_reader import WebReader, WebAuthentication, WebRateLimited
        for attempt in range(2):
            try:
                if self._web_reader is None:
                    self._web_reader = WebReader(self, BASE)
                return self._web_reader.read(endpoint, params)
            except WebAuthentication:
                if attempt:
                    raise
                self.login()
            except WebRateLimited as exc:
                self.pacer.cooldown(retry_seconds(exc.seconds, minimum=self.cooldown_seconds), time.time())
                raise
            except Exception as exc:
                if attempt or not self.recover_browser(exc):
                    raise

    def _try_web(self, endpoint, params=None):
        if self.page is None or not (endpoint == '/search.json' or re.fullmatch(r'/(?:t/\d+|u/[\w.%-]+)\.json', endpoint)):
            return None
        self.check()
        self.emit('log', f'接口备用：尝试读取浏览器正常网页（{endpoint}）……')
        try:
            data = self.read_web(endpoint, params)
        except Cancelled:
            raise
        except Exception as exc:
            self.check()
            self.diagnostics.event('web_failed', endpoint, error=type(exc).__name__)
            self.emit('log', f'网页备用暂不可用：{type(exc).__name__}；保留进度。')
            self._web_until.pop(self._web_key(endpoint), None)
            return None
        if self._author_endpoint(endpoint):
            self._web_until.pop(self._web_key(endpoint), None)
        elif self._web_until.get(self._web_key(endpoint), 0) <= time.monotonic():
            self._web_until[self._web_key(endpoint)] = time.monotonic() + 600
        self.diagnostics.event('web_success', endpoint)
        self.emit('log', '作者主页网页读取成功；下次采集仍先重试接口。' if self._author_endpoint(endpoint)
                  else '网页读取成功，继续当前任务；同类读取暂用网页，稍后自动检查接口恢复。')
        self._recovered(endpoint)
        return data

    @staticmethod
    def _web_key(endpoint):
        return '/t/' if endpoint.startswith('/t/') else endpoint

    @staticmethod
    def _author_endpoint(endpoint):
        return endpoint == '/user_actions.json' or bool(re.fullmatch(r'/u/[\w.%-]+\.json', endpoint))

    def _get_author_api(self, endpoint, params=None):
        """Probe afresh on every call; a past web result never suppresses the API."""
        url = BASE + endpoint + ('?' + urlencode(params) if params else '')
        failures, begun = 0, time.monotonic()
        while failures < 3:
            self.check()
            result = None
            try:
                result = self.fetch(url, endpoint)
                status = result['status']
                if status == 429:
                    self.service_pause(endpoint, status, '网站要求限流',
                                       retry_seconds(result.get('retry'), minimum=self.cooldown_seconds))
                    continue
                if status in (401, 403) or (status == 200 and result['text'].lstrip().startswith('<')):
                    self._session_confirmed = False
                    self.emit('log', f'作者接口需要登录或验证（{endpoint}），请在浏览器完成后继续。')
                    self.login()
                    continue
                if status == 200:
                    try:
                        data = json.loads(result['text'])
                    except ValueError:
                        data = None
                    valid = (isinstance(data, dict) and not data.get('errors') and not data.get('error')
                             and data.get('success') is not False and not service_disabled(data))
                    if valid and endpoint == '/user_actions.json':
                        valid = isinstance(data.get('user_actions'), list) and all(isinstance(row, dict) for row in data['user_actions'])
                    elif valid:
                        valid = (isinstance(data.get('user'), dict) and isinstance(data['user'].get('username'), str)
                                 and bool(data['user']['username'].strip()))
                    if valid:
                        if failures:
                            self.emit('log', f'作者接口已恢复：{endpoint}，第 {failures + 1} 次尝试成功。')
                            self.diagnostics.event('recovered', endpoint, attempts=failures,
                                                   recovery_seconds=round(time.monotonic()-begun, 3))
                        self._web_until.pop(self._web_key(endpoint), None)
                        self._missing.clear()
                        self._session_confirmed = True
                        self._recovered(endpoint)
                        return data
                    reason = '作者接口数据结构异常'
                elif status == 404 or status == 408 or status >= 500:
                    reason = f'作者接口 HTTP {status}'
                else:
                    raise ForumError(f'网站返回 HTTP {status}：{endpoint}')
            except ConnectionFailure as exc:
                reason = f'作者接口连接失败（{exc}）'
            failures += 1
            server_wait = retry_seconds(result.get('retry'), minimum=0) if result else 0
            if server_wait:
                self.pacer.cooldown(server_wait, time.time())
            if failures < 3:
                first_wait = min(30., self.cooldown_seconds)
                delay = first_wait if failures == 1 else first_wait / 3
                self.pacer.cooldown(max(delay, server_wait), time.time())
                delay = self.pacer.delay(endpoint, time.time())
                self.emit('log', f'{reason}；等待 {delay:.1f} 秒后重试接口（第 {failures + 1}/3 次），可停止。')
                self.diagnostics.event('retry', endpoint, reason=reason, attempt=failures, wait_seconds=round(delay, 3))
                self.wait(delay)
                self.check()
        self.emit('log', f'作者接口连续 3 次未成功，开始网页备用读取：{endpoint}。下轮仍先检查接口。')
        if endpoint != '/user_actions.json':
            alternate = self._try_web(endpoint, params)
            if alternate is not None:
                return alternate
            raise AuthorAPIUnavailable(f'{reason}；作者主页网页也未读取成功')
        raise AuthorActivityUnavailable(f'{reason}；已尝试 3 次，改用作者首帖网页检索')

    def pace(self, endpoint):
        self.check()
        delay = self.pacer.delay(endpoint, time.time())
        if delay >= 1:
            self.emit('log', f'请求节流：等待 {delay:.0f} 秒后读取 {endpoint}；可停止。')
        self.wait(delay)
        self.check()
        now = time.time()
        self.pacer.started(endpoint, now)
        self._request_recent = sum(t > now - 60 for t in self.pacer.data.get('recent', []))

    def _retry(self, endpoint, reason, base=3, minimum=0):
        self.check()
        self._failures += 1
        if self._failure_started is None:
            self._failure_started = time.monotonic()
        delay = max(minimum, base, min(600, max(1, base) * 2 ** min(self._failures - 1, 10)))
        self.pacer.cooldown(delay, time.time())
        delay = max(delay, self.pacer.data.get('cooldown', 0) - time.time())
        self.diagnostics.event('retry', endpoint, reason=reason, attempt=self._failures, wait_seconds=round(delay, 3))
        self.emit('log', f'{reason}；自动重试第 {self._failures} 次，等待 {delay:.0f} 秒，无需确认；可随时停止。')
        remaining = delay
        while remaining > 0:
            step = min(30, remaining)
            self.wait(step)
            self.check()
            remaining -= step
            if remaining > 0:
                self.emit('log', f'自动恢复等待中：还剩 {remaining:.0f} 秒，随后继续 {endpoint}。')

    def _recovered(self, endpoint):
        if self._failures:
            elapsed = max(0, time.monotonic() - self._failure_started)
            self.diagnostics.event('recovered', endpoint, attempts=self._failures, recovery_seconds=round(elapsed, 3))
            self.emit('log', f'连接已自动恢复：{endpoint}，经历 {self._failures} 次重试，用时 {elapsed:.1f} 秒。')
        self._failures, self._failure_started = 0, None
        if self._session_confirmed and self.context and time.monotonic() - self._last_session_save >= 60:
            try:
                self.save_session()
                self._last_session_save = time.monotonic()
            except Exception:
                pass  # Keep the previous atomic checkpoint on a dying browser.

    def recover_browser(self, error, *, restore_home=True):
        code = connection_code(error)
        if code not in {'NAVIGATION_CONTEXT_LOST', 'PAGE_CRASHED', 'BROWSER_CLOSED'}:
            return False
        self.check()
        if code == 'NAVIGATION_CONTEXT_LOST':
            now = time.monotonic()
            self._navigation_losses = [t for t in getattr(self, '_navigation_losses', []) if now-t < 60]
            if len(self._navigation_losses) >= 3:
                raise ConnectionFailure('页面一分钟内连续跳转中断 3 次；已保留进度，请稍后继续。')
            self._navigation_losses.append(now)
            self.emit('log', '页面跳转使读取中断；等待页面稳定后继续，已完成匹配保留。')
            try:
                self.page.wait_for_load_state('domcontentloaded', timeout=15000)
                self.wait(max(1, self.interval))
                return True
            except Exception as exc:
                if connection_code(exc) not in {'PAGE_CRASHED', 'BROWSER_CLOSED'}:
                    raise ConnectionFailure('页面未稳定，已保留进度，请稍后继续') from exc
                code = connection_code(exc)
        now = time.monotonic()
        self._browser_restarts = [t for t in self._browser_restarts if now-t < 600]
        if len(self._browser_restarts) >= 3:
            raise ConnectionFailure('浏览器 10 分钟内已恢复 3 次，暂停重启；已完成匹配和索引保留。')
        self._browser_restarts.append(now)
        self.diagnostics.event('browser_recovery', '/', reason=code, attempt=len(self._browser_restarts))
        self.emit('log', '浏览器页面已退出或崩溃，稍后恢复同一浏览器并继续；可停止。')
        self.wait(max(5, self.interval))
        self.check()
        try:
            self.save_session()
        except Exception:
            pass
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            options = dict(headless=not self.headful)
            if self.browser_channel == 'chromium':
                options['executable_path'] = self.playwright.chromium.executable_path
            else:
                options['channel'] = self.browser_channel
            self.browser = self.playwright.chromium.launch(**options)
            state = self.state_dir / 'session.json'
            self.context = self.browser.new_context(**({'storage_state':str(state)} if state.exists() else {}), locale='zh-CN')
            self.page = self.context.new_page()
            self._web_reader = None
            if restore_home:
                self.open_home()
        except Cancelled:
            raise
        except Exception as exc:
            raise ConnectionFailure('浏览器自动恢复未成功；已完成结果保留，请检查浏览器后继续') from exc
        return True

    def _record_request(self, endpoint, started, result=None, error=None, url=''):
        result = result or {}
        if result.get('status') == 200 and endpoint != '/' and isinstance(result.get('text'), str):
            if result['text'].lstrip().startswith('<'):
                error = 'LOGIN_OR_HTML'
            else:
                try:
                    data = json.loads(result['text'])
                    if isinstance(data, dict) and service_disabled(data):
                        error = 'SEARCH_DISABLED'
                except ValueError:
                    error = 'INVALID_JSON'
        headers = result.get('headers', {})
        headers = {k: str(v)[:200] for k, v in headers.items()
                   if k in ('server', 'content-type', 'retry-after', 'cf-ray', 'cf-cache-status')} if isinstance(headers, dict) else {}
        self.diagnostics.event('request', endpoint, status=result.get('status'), error=error,
            elapsed_seconds=round(max(0, time.monotonic() - started), 3), browser=self.browser_channel,
            requests_last_minute=self._request_recent,
            retry_streak=self._failures, headers=headers, response_signals=response_shape(result.get('text', '')),
            query_id=self.diagnostics.query_id(url))

    def service_pause(self, endpoint, status, reason, seconds=None):
        self._retry(endpoint, f'{reason}（HTTP {status}）', base=max(1, self.cooldown_seconds), minimum=seconds or 0)
        self._missing.clear()

    def _wait_for_user(self, message):
        if not self.headful or self.resume is None:
            raise ConnectionFailure(message + ' 请在网络恢复后重新分析，或使用“仅用本地索引”。')
        self.check()
        self.resume.clear()
        self.page.bring_to_front()
        self.emit('login', message)
        while not self.resume.wait(.25):
            self.check()
        self.check()
        self.emit('resume_done', None)

    def open_home(self):
        """Retry transport/service failures until recovery or Stop; retain the browser."""
        attempt = 0
        while True:
            self.check()
            attempt += 1
            self.emit('log', f'连接网站首页 {BASE}/（第 {attempt} 次）……')
            self.pace('/')
            started = time.monotonic()
            failure = None
            try:
                response = self.page.goto(BASE, wait_until='domcontentloaded', timeout=30000)
            except Exception as exc:
                code = connection_code(exc)
                self._record_request('/', started, error=code or type(exc).__name__)
                self.check()
                if self.recover_browser(exc, restore_home=False):
                    continue
                if not code:
                    raise
                failure = code
            finally:
                self.pacer.finished(time.time())
            if failure:
                self.emit('log', f'首页连接中断：{failure}')
                self._retry('/', failure)
                continue
            self.check()
            status = getattr(response, 'status', None)
            headers = getattr(response, 'headers', {})
            headers = headers if isinstance(headers, dict) else {}
            self._record_request('/', started, {'status': status if isinstance(status, int) else 200, 'headers': headers})
            if status in (401, 403):
                self.login()
                continue
            if isinstance(status, int) and status >= 400:
                self.service_pause('/', status, '网站首页异常', retry_seconds(headers.get('retry-after'), minimum=self.cooldown_seconds))
                continue
            self._recovered('/')
            self.emit('log', '网站首页已响应，开始检查会话或读取数据。')
            return response

    def check(self):
        if self.stop.is_set():
            raise Cancelled('已停止；已采集的索引已保存')

    def wait(self, seconds):
        if self.stop.wait(max(0, seconds)):
            self.check()

    def __enter__(self):
        from .runtime import configure_bundled_browser
        configure_bundled_browser()
        from playwright.sync_api import sync_playwright
        self.state_dir.mkdir(parents=True, exist_ok=True)
        # Prevent two tool windows from independently doubling the request rate.
        if __import__('sys').platform == 'win32':
            import msvcrt
            self._network_lock = (self.state_dir / 'network.lock').open('a+b')
            self._network_lock.seek(0)
            try:
                msvcrt.locking(self._network_lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self._network_lock.close()
                self._network_lock = None
                raise ConnectionFailure('另一个工具窗口正在使用网站连接。请先停止那个窗口的联网任务。')
        try:
            self.playwright = sync_playwright().start()
        except Exception:
            self.close(save=False)
            raise
        errors = []
        for channel in (None if self.browser_channel == 'chromium' else self.browser_channel,):
            try:
                if self.browser_channel == 'chromium':
                    self.browser = self.playwright.chromium.launch(
                        executable_path=self.playwright.chromium.executable_path, headless=not self.headful)
                else:
                    self.browser = self.playwright.chromium.launch(channel=channel, headless=not self.headful)
                self.emit('log', f'使用浏览器：{self.browser_channel}（工具独立会话，与日常浏览器的登录状态不同）。')
                break
            except Exception as exc:
                errors.append(str(exc).splitlines()[0])
        if not self.browser:
            self.close(save=False)
            raise ForumError(f'无法启动所选浏览器 {self.browser_channel}。请在界面选择已安装的浏览器；不会自动切换。\n' + '\n'.join(errors))
        state = self.state_dir / 'session.json'
        if not state.exists() and self.legacy_state and self.legacy_state.exists():
            state = self.legacy_state
        kwargs = {'storage_state': str(state)} if state.exists() else {}
        try:
            self.context = self.browser.new_context(**kwargs, locale='zh-CN')
            self.page = self.context.new_page()
            self.open_home()
        except Exception:
            self.close(save=False)
            raise
        return self

    def save_session(self):
        if not self.context or not self._session_confirmed:
            return
        # Do not replace a working session after an unsuccessful browser startup.
        temporary = self.state_dir / f'.session-{uuid.uuid4().hex}.tmp'
        try:
            self.context.storage_state(path=str(temporary))
            temporary.replace(self.state_dir / 'session.json')
        finally:
            temporary.unlink(missing_ok=True)

    def search_diagnostic(self, data: dict, query: str, page: int, error: str):
        """Keep schema information only; never dump cookies, headers or full bodies."""
        def shape(value):
            if isinstance(value, dict):
                return {str(k): shape(v) for k, v in value.items()}
            if isinstance(value, list):
                return {'type': 'list', 'count': len(value), 'first_item_type': type(value[0]).__name__ if value else None}
            return type(value).__name__
        folder = self.state_dir / 'diagnostics'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'search-{time.time_ns()}.json'
        path.write_text(json.dumps({'query': query, 'page': page, 'error': error, 'response_shape': shape(data)},
                                   ensure_ascii=False, indent=2), encoding='utf-8')
        self.emit('log', f'搜索诊断已保存：{path}')

    def close(self, save=True):
        if save:
            try:
                self.save_session()
            except Exception as exc:
                self.emit('log', f'登录状态未能保存，旧文件仍保留：{type(exc).__name__}')
        # A cleanup error must not replace the useful original connection error.
        for resource, method in ((self.browser, 'close'), (self.playwright, 'stop')):
            if resource:
                try:
                    getattr(resource, method)()
                except Exception as exc:
                    self.emit('log', f'浏览器清理提示：{type(exc).__name__}')
        if self._network_lock:
            self._network_lock.close()
            self._network_lock = None

    def __exit__(self, *_):
        self.close()

    def login(self):
        if not self.headful or self.resume is None:
            raise ForumError('网站要求登录或浏览器验证。请启用“显示浏览器”，点击“登录网站”后重试。')
        self._wait_for_user('请在浏览器中完成登录或验证，然后点击工具中的“已完成，继续”。')
        if not self.page.url.startswith(BASE + '/'):
            self.open_home()

    def fetch(self, url: str, operation: str) -> dict:
        while True:
            self.check()
            self.pace(operation)
            self.last_request = time.monotonic()
            result, failure = None, None
            try:
                result = self.page.evaluate("""async url => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), 40000);
                    try {
                        const r = await fetch(url, {credentials: 'same-origin',
                            headers: {'Accept':'application/json'}, signal: controller.signal});
                        const headers = {};
                        for (const key of ['server','content-type','retry-after','cf-ray','cf-cache-status']) {
                            const value = r.headers.get(key); if (value !== null) headers[key] = value;
                        }
                        return {status:r.status, retry:r.headers.get('retry-after'), headers, text:await r.text()};
                    } finally { clearTimeout(timer); }
                }""", url)
                return result
            except Exception as exc:
                failure = connection_code(exc) or type(exc).__name__
                self.check()
                if self.recover_browser(exc):
                    continue
                if not connection_code(exc):
                    raise
            finally:
                self.pacer.finished(time.time())
                self._record_request(operation, self.last_request, result, failure, url)
            self.emit('log', f'读取 {operation} 时连接中断：{failure}')
            if self._author_endpoint(operation):
                raise ConnectionFailure(failure)
            params = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
            alternate = self._try_web(operation, params)
            if alternate is not None:
                return {'status': 200, 'text': json.dumps(alternate)}
            self._retry(operation, failure)

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        if not endpoint.startswith('/') or endpoint.startswith('//'):
            raise ValueError('只允许同站相对地址')
        if self._author_endpoint(endpoint):
            return self._get_author_api(endpoint, params)
        url = BASE + endpoint + ('?' + urlencode(params) if params else '')
        while True:
            self.check()
            if self._web_until.get(self._web_key(endpoint), 0) > time.monotonic():
                alternate = self._try_web(endpoint, params)
                if alternate is not None:
                    return alternate
            # Browser fetch uses the same origin and session as the visible website.
            result = self.fetch(url, endpoint)
            status = result['status']
            if status == 408:
                alternate = self._try_web(endpoint, params)
                if alternate is not None:
                    return alternate
            if status in (408, 429):
                self.service_pause(endpoint, status, '网站请求超时或要求限流',
                                   retry_seconds(result.get('retry'), minimum=self.cooldown_seconds))
                continue
            if status in (401, 403) or (status == 200 and result['text'].lstrip().startswith('<')):
                self._session_confirmed = False
                self.emit('log', f'工具会话需要登录或验证（{endpoint}，HTTP {status}），暂停等待手动处理。')
                self.login()
                continue
            if status >= 500:
                if result.get('retry'):
                    self.pacer.cooldown(retry_seconds(result['retry'], minimum=self.cooldown_seconds), time.time())
                alternate = self._try_web(endpoint, params)
                if alternate is not None:
                    return alternate
                self.service_pause(endpoint, status, '网站服务异常',
                                   retry_seconds(result.get('retry'), minimum=self.cooldown_seconds))
                continue
            if status == 404:
                self._missing.add(endpoint)
                if endpoint.startswith(('/t/', '/u/')) and not result['text'].lstrip().startswith('<') and len(self._missing) < 3:
                    raise TopicUnavailable(f'此主题或用户不存在或不可见（HTTP 404）：{endpoint}')
                alternate = self._try_web(endpoint, params)
                if alternate is not None:
                    return alternate
                self.service_pause(endpoint, status, '接口返回异常 404 或连续多个资源不可见，需要检查工具会话')
                continue
            if status != 200:
                raise ForumError(f'网站返回 HTTP {status}：{endpoint}')
            try:
                data = json.loads(result['text'])
            except ValueError as exc:
                alternate = self._try_web(endpoint, params)
                if alternate is not None:
                    return alternate
                raise ForumError(f'网站没有返回有效 JSON：{endpoint}') from exc
            if isinstance(data, dict) and service_disabled(data):
                self.service_pause(endpoint, status, '网站负载过高或搜索被停用')
                alternate = self._try_web(endpoint, params)
                if alternate is not None:
                    return alternate
                continue
            if not isinstance(data, dict) or data.get('errors'):
                alternate = self._try_web(endpoint, params)
                if alternate is not None:
                    return alternate
                raise ForumError(f'网站接口返回错误：{endpoint}')
            if endpoint == '/search.json':
                try:
                    search_page(data)
                except SearchResponseError:
                    alternate = self._try_web(endpoint, params)
                    if alternate is not None:
                        return alternate
            self._missing.clear()
            self._recovered(endpoint)
            if endpoint == '/session/current.json':
                self._session_confirmed = bool((data.get('current_user') or {}).get('username'))
            else:
                self._session_confirmed = True
            return data


class Forum:
    def __init__(self, client, store, emit=lambda *_: None, refresh=False):
        self.client, self.store, self.emit, self.refresh = client, store, emit, refresh
        self.visited = set()
        self.loaded_topics, self.missing_topics = set(), set()
        self.search_results = {}

    def topic(self, topic_id: int, fresh=False) -> Topic:
        if topic_id in self.missing_topics:
            raise TopicUnavailable(f'本轮已确认主题 {topic_id} 不可见，跳过重复请求')
        cached = self.store.topic(topic_id)
        if cached and cached.details_loaded and (not fresh or topic_id in self.loaded_topics):
            return cached
        try:
            topic = topic_from_json(self.client.get(f'/t/{int(topic_id)}.json'))
        except TopicUnavailable:
            self.missing_topics.add(topic_id)
            raise
        if cached and not topic.created_at:
            topic.created_at = cached.created_at
        self.store.upsert(topic)
        self.loaded_topics.add(topic_id)
        return topic

    def search(self, text: str, pages=2, max_hits=None) -> list[dict]:
        # Punctuation is removed so filenames cannot inject Discourse search operators.
        words = re.sub(r'[^\w\s\-]', ' ', text, flags=re.UNICODE)
        words = re.sub(r'\s+', ' ', words).strip()[:180]
        if len(words) < 3:
            return []
        # Search content as well as titles; verify against the original post later.
        query = words
        cache_key = f'precise-v2:{pages}:{max_hits}:{query}'
        cached = self.search_results.get(cache_key)
        if cached is None and not self.refresh:
            cached = self.store.search(cache_key)
        if cached is not None:
            if isinstance(cached, dict) and cached.get('too_broad'):
                raise SearchTooBroad(cached['message'])
            return cached
        found = {}
        for page in range(1, pages + 1):
            data = self.client.get('/search.json', {'q': query, 'page': page})
            if service_disabled(data):
                raise ServiceUnavailable('网站负载过高或搜索被停用，已停止整轮搜索；请稍后再试。')
            try:
                topics, more = search_page(data)
            except SearchResponseError as exc:
                if hasattr(self.client, 'search_diagnostic'):
                    self.client.search_diagnostic(data, query, page, str(exc))
                raise
            for topic in topics:
                found[topic['id']] = topic
            if max_hits is not None and (len(found) > max_hits or more):
                message = f'首批 {len(found)} 个结果' + ('且仍有后续页' if more else '')
                self.search_results[cache_key] = {'too_broad': True, 'message': message}
                self.store.save_search(cache_key, self.search_results[cache_key])
                raise SearchTooBroad(message)
            if not more or not topics:
                break
        results = list(found.values())
        self.search_results[cache_key] = results
        self.store.save_search(cache_key, results)
        return results

    def author(self, author: str) -> str:
        author = author_from_input(author)
        if author.casefold() in self.visited:
            return author
        saved = self.store.author(author)
        if saved and saved['complete'] and not self.refresh and time.time() - saved['updated'] < 7 * 86400:
            self.visited.add(author.casefold())
            self.emit('log', f'复用作者索引：{author}（无需请求主页）')
            return author
        if saved and not saved['complete'] and saved.get('error'):
            self.emit('log', f'作者 {author} 上次采集未完成；本轮先重试接口，成功后从第 {saved["offset"]} 条继续。')
        try:
            profile = self.client.get(f'/u/{quote(author, safe="")}.json')
        except (TopicUnavailable, AuthorAPIUnavailable):
            return self._author_web(author, saved['offset'] if saved else 0)
        username = profile.get('user', {}).get('username')
        if not username:
            raise ForumError('作者主页未返回有效用户名')
        self.store.save_profile(username, profile)
        key = username.casefold()
        if key in self.visited:
            return username
        saved = self.store.author(username)
        if saved and saved['complete'] and not self.refresh and time.time() - saved['updated'] < 7 * 86400:
            self.visited.add(key)
            self.emit('log', f'复用作者索引：{username}')
            return username
        offset = saved['offset'] if saved and not saved['complete'] and not self.refresh else 0
        seen_pages = set()
        self.emit('log', f'采集作者 {username} 的主题，从第 {offset} 条继续。')
        self.store.author_progress(username, offset)
        try:
            while True:
                self.client.check()
                data = self.client.get('/user_actions.json', {'username': username, 'offset': offset, 'filter': '4'})
                rows = data.get('user_actions')
                if not isinstance(rows, list):
                    raise ForumError('作者列表数据结构不符，未标记为采集完成')
                if not rows:
                    self.visited.add(key)
                    self.store.author_progress(username, offset, complete=True)
                    self.emit('log', f'作者 {username} 采集完成，共读取 {offset} 条主题记录。')
                    return username
                signature = tuple((r.get('topic_id'), r.get('post_id')) for r in rows)
                if signature in seen_pages:
                    raise ForumError('作者分页重复，已停止，避免把不完整列表误判为完成')
                seen_pages.add(signature)
                for row in rows:
                    # Filter 4 is NEW_TOPIC; never attribute a reply to the topic owner.
                    if row.get('action_type') != 4 or row.get('post_number', 1) != 1:
                        continue
                    if row.get('username', '').casefold() != key:
                        continue
                    if not row.get('topic_id') or not row.get('title'):
                        raise ForumError('作者主题记录缺少 ID 或标题')
                    topic_id = int(row['topic_id'])
                    old = self.store.topic(topic_id)
                    topic = Topic(topic_id, html.unescape(row['title']), username, f'{BASE}/t/{topic_id}',
                                  row.get('created_at') or '', category_id=row.get('category_id'))
                    if old and old.author.casefold() == key:
                        topic.filenames, topic.tags = old.filenames, old.tags
                        topic.updated_at, topic.views, topic.posts_count = old.updated_at, old.views, old.posts_count
                        topic.body, topic.source_ids = old.body, old.source_ids
                        topic.details_loaded = old.details_loaded and (not self.refresh or topic_id in self.loaded_topics)
                        topic.links, topic.links_loaded = old.links, old.links_loaded
                        topic.category_name = old.category_name
                        if topic.category_id is None:
                            topic.category_id = old.category_id
                        topic.resource_links, topic.resource_details_loaded = old.resource_links, old.resource_details_loaded
                    self.store.upsert(topic)
                offset += len(rows)
                self.store.author_progress(username, offset)
                self.emit('log', f'{username}：已索引 {offset} 条；继续读取下一页。')
        except AuthorActivityUnavailable as exc:
            self.emit('log', str(exc))
            return self._author_web(username, offset)
        except Exception as exc:
            self.store.author_progress(username, offset, error=str(exc))
            raise

    def _author_web(self, username, offset=0):
        """Use normal first-post search; never label a search-limited index complete."""
        from .web_reader import WebReadError
        self.emit('log', f'作者 {username} 的活动列表不可用，改从网页搜索其首帖；其他文件不会被该列表的 404 卡住。')
        seen, count = set(), 0
        reason = '网页检索补充索引；活动列表尚未完成，下次分析继续检查'
        try:
            if not callable(getattr(self.client, 'read_web', None)):
                raise WebReadError('当前连接没有网页读取能力')
            query = f'@{username} in:first order:latest'
            for page in range(1, 51):
                self.client.check()
                data = self.client.read_web('/search.json', {'q': query, 'page': page})
                rows = data['topics']
                fresh = [row for row in rows if row['id'] not in seen]
                more = data.get('grouped_search_result', {}).get('more_full_page_results', False)
                if not fresh and more:
                    raise WebReadError('作者网页分页未产生新主题')
                for row in fresh:
                    seen.add(row['id'])
                    # Search result author is the matched post's author. Require
                    # both the first-post filter and first-post link, never a reply.
                    if row.get('username', '').casefold() != username.casefold() or row.get('post_number') != 1:
                        continue
                    old = self.store.topic(row['id'])
                    if old and old.details_loaded:
                        continue
                    topic = (replace(old, title=row['title']) if old and old.author.casefold() == username.casefold()
                             else Topic(row['id'], row['title'], username, f'{BASE}/t/{row["id"]}'))
                    self.store.upsert(topic)
                    count += 1
                self.store.author_progress(username, offset, error=reason)
                self.emit('log', f'{username}：网页已补充 {count} 条主题，读取第 {page} 批。')
                if not more:
                    break
        except Cancelled:
            self.store.author_progress(username, offset, error=reason)
            raise
        except Exception as exc:
            reason = f'作者网页暂不可用（{type(exc).__name__}）；已保留索引，继续其他文件，下次分析重试'
        self.store.author_progress(username, offset, error=reason)
        self.visited.add(username.casefold())
        self.emit('log', f'{username}：{reason}。')
        return username
