"""Read normal rendered pages using the existing browser session, without JSON APIs."""
import re
import time
from urllib.parse import quote, urlencode, urlparse


class WebReadError(Exception):
    pass


class WebAuthentication(WebReadError):
    pass


class WebRateLimited(WebReadError):
    def __init__(self, seconds=None):
        self.seconds = seconds
        super().__init__('网页要求限流等待')


SNAPSHOT = r'''kind => {
    const text = el => (el?.textContent || '').trim();
    const visible = el => el && el.getClientRects().length > 0;
    const any = selector => [...document.querySelectorAll(selector)].some(visible);
    const topicRef = url => {
        const parts = url.pathname.split('/').filter(Boolean);
        const index = /^\d+$/.test(parts[1] || '') ? 1 : 2;
        if (url.origin !== location.origin || parts[0] !== 't' || parts.length > index + 2 ||
            !/^\d+$/.test(parts[index] || '') || !/^\d+$/.test(parts[index + 1] || '1')) return null;
        return {id: Number(parts[index]), post: Number(parts[index + 1] || 1)};
    };
    const auth = /\/login\/?$/.test(location.pathname) ||
        /just a moment|checking your browser|验证.*浏览器/i.test(document.title) ||
        any('form input[type="password"], #challenge-running, #challenge-form, .login-required');
    if (auth) return {auth: true};
    if (kind === 'categories') {
        const items = Array.from(document.querySelectorAll('a[href*="/c/"]')).map(link => {
            const url = new URL(link.href, location.href);
            const id = url.pathname.split('/').filter(Boolean).at(-1);
            if (url.origin !== location.origin || !/^\d+$/.test(id || '')) return null;
            const name = text(link.querySelector('.badge-category__name, .category-name')) || text(link);
            return name ? {id: Number(id), name} : null;
        }).filter(Boolean);
        return {ready: items.length > 0, items};
    }
    if (kind === 'search') {
        const root = document.querySelector('.search-container, .full-page-search');
        const error = any('.search-container .warning, .search-container .alert-error, .search-results .error');
        const busy = any('.search-container .spinner, .search-results .spinner, .search-container [aria-busy="true"]');
        const items = [...document.querySelectorAll('.search-results .fps-result')].map(row => {
            const link = row.querySelector('a.search-link');
            if (!link) return null;
            const url = new URL(link.href, location.href);
            const match = topicRef(url);
            return {id: match?.id || 0,
                    post_number: match?.post || 0,
                    title: text(link.querySelector('.topic-title')) || text(link),
                    username: row.querySelector('.author [data-user-card]')?.getAttribute('data-user-card') || ''};
        });
        const footer = text(document.querySelector('.search-footer'));
        const end = /no more results|没有更多(?:的)?结果|无更多结果/i.test(footer);
        return {ready: !!root && !busy && (items.length > 0 || any('.no-results-container')),
                error, items, empty: any('.no-results-container'), more: !end && !any('.no-results-container'),
                query: document.querySelector('input.search-query, input.full-page-search, .search-bar input')?.value};
    }
    if (kind === 'topic') {
        const post = document.querySelector('#post_1, article[data-post-number="1"]');
        const cooked = post?.querySelector('.cooked');
        const author = post?.querySelector('.topic-meta-data .username [data-user-card], .names .username [data-user-card]');
        const title = text(document.querySelector('#topic-title h1, h1.fancy-title, .topic-title h1'));
        const match = topicRef(new URL(location.href));
        return {ready: !!(cooked && author && title), id: match?.id || 0, title,
                category_name: Array.from(document.querySelectorAll('#topic-title .badge-category__name, #topic-title .category-name, #topic-title .badge-category')).map(text).join(' / '),
                tags: Array.from(document.querySelectorAll('#topic-title .discourse-tag')).map(text),
                username: author?.getAttribute('data-user-card'), cooked: cooked?.innerHTML,
                created_at: post?.querySelector('time[datetime]')?.getAttribute('datetime') || '',
                timestamp: post?.querySelector('.relative-date[data-time]')?.getAttribute('data-time') || ''};
    }
    const username = text(document.querySelector('.user-profile-names .username, .user-main h1.username'));
    return {ready: !!username, username};
}'''


class WebReader:
    def __init__(self, client, base):
        self.client, self.base = client, base
        self.query = None
        self.page_number = 0
        self.ids = set()

    def _snapshot(self, kind):
        row = self.client.page.evaluate(SNAPSHOT, kind)
        if row.get('auth'):
            raise WebAuthentication('网页需要登录或验证')
        if row.get('error'):
            raise WebReadError('网页显示错误，未当作空结果')
        return row

    def _wait(self, kind, previous=None):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            self.client.check()
            row = self._snapshot(kind)
            ids = {item['id'] for item in row.get('items', []) if item}
            if row.get('ready') and (previous is None or ids - previous or not row.get('more')):
                return row
            self.client.wait(.2)
        raise WebReadError('网页内容未完整加载或结构无法识别，保留待重试')

    def _open(self, path, kind, endpoint):
        self.query = None
        self.client.check()
        self.client.pace(endpoint)
        started = time.monotonic()
        status, error = None, None
        try:
            response = self.client.page.goto(self.base + path, wait_until='domcontentloaded', timeout=20000)
            status = response.status if response else None
            if urlparse(self.client.page.url).netloc != urlparse(self.base).netloc:
                raise WebReadError('网页跳转离开论坛，未读取结果')
            if status in (401, 403):
                raise WebAuthentication('网页需要登录或验证')
            if status == 429:
                raise WebRateLimited(response.headers.get('retry-after'))
            if status != 200:
                raise WebReadError(f'网页返回 HTTP {status}')
            return self._wait(kind)
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            self.client.pacer.finished(time.time())
            self.client.diagnostics.event('request', endpoint, status=status, error=error,
                transport='web', elapsed_seconds=round(time.monotonic() - started, 3),
                requests_last_minute=self.client._request_recent)

    def _next_search(self):
        previous = set(self.ids)
        self.client.pace('/search.json')
        started = time.monotonic()
        error = None
        try:
            # Discourse's normal page loads more when the footer enters view.
            self.client.page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
            return self._wait('search', previous)
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            self.client.pacer.finished(time.time())
            self.client.diagnostics.event('request', '/search.json', status=200, error=error,
                transport='web-scroll', elapsed_seconds=round(time.monotonic() - started, 3),
                requests_last_minute=self.client._request_recent)

    def search(self, query, page=1):
        page = int(page)
        if not 1 <= page <= 50:
            raise WebReadError('网页检索达到分页范围，保留未完成状态')
        # Reading a topic between search pages navigates this shared tab away.
        # Reopen and replay search pages instead of scrolling a topic as results.
        on_search = urlparse(self.client.page.url).path.rstrip('/') == '/search'
        if self.query != query or page != self.page_number + 1 or not on_search:
            row = self._open('/search?' + urlencode({'q': query}), 'search', '/search.json')
            self.ids, self.page_number, self.query = set(), 0, query
        else:
            row = self._next_search()
        while self.page_number < page:
            if row.get('query') is None or ' '.join(row['query'].split()) != ' '.join(query.split()):
                raise WebReadError('网页搜索词不一致，未采用页面结果')
            if any(not item or not item.get('id') or not item.get('title') for item in row['items']):
                raise WebReadError('网页候选缺少有效标题或同站主题链接')
            fresh = [item for item in row['items'] if item['id'] not in self.ids]
            self.ids.update(item['id'] for item in fresh)
            self.page_number += 1
            if self.page_number == page:
                return {'topics': fresh, 'grouped_search_result': {'more_full_page_results': row['more']}}
            if not row['more']:
                raise WebReadError('请求页码超过网页已确认的末页')
            row = self._next_search()

    def read(self, endpoint, params=None):
        params = params or {}
        if endpoint == '/categories.json':
            row = self._open('/categories', 'categories', endpoint)
            return {'category_list': {'categories': row['items']}}
        if endpoint == '/search.json':
            return self.search(params.get('q', ''), params.get('page', 1))
        match = re.fullmatch(r'/t/(\d+)\.json', endpoint)
        if match:
            identity = int(match[1])
            row = self._open(f'/t/{identity}/1', 'topic', endpoint)
            if row['id'] != identity:
                raise WebReadError('页面主题编号不一致')
            created = row.get('created_at') or ''
            if not created and str(row.get('timestamp', '')).isdigit():
                from datetime import datetime, timezone
                try:
                    created = datetime.fromtimestamp(int(row['timestamp']) / 1000, timezone.utc).isoformat()
                except (ValueError, OverflowError, OSError):
                    pass
            return {'id': identity, 'title': row['title'], 'created_at': created,
                    'category_name': row.get('category_name', ''), 'tags': row.get('tags', []), 'post_stream': {'posts': [
                {'post_number': 1, 'username': row['username'], 'cooked': row['cooked']}]}}
        match = re.fullmatch(r'/u/([\w.%-]+)\.json', endpoint)
        if match:
            from urllib.parse import unquote
            username = unquote(match[1])
            row = self._open('/u/' + quote(username, safe=''), 'profile', endpoint)
            if row['username'].casefold() != username.casefold():
                raise WebReadError('页面用户名不一致')
            return {'user': {'username': row['username']}}
        raise WebReadError('此接口没有网页读取映射')
