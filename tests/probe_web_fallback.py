"""Real browser checks of HTML fallback against loopback only."""
import html
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.forum import BrowserClient, Forum
from organizer.store import Store


def result(identity, username='Alpha'):
    return f'''<div class="fps-result"><div class="author"><a data-user-card="{username}"></a></div>
        <a class="search-link" href="/t/{identity}/1"><span class="topic-title">Harbor Lights {identity}</span></a></div>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        route = urlparse(self.path)
        params = parse_qs(route.query)
        self.server.calls.append((route.path, params))
        status, content = 200, 'text/html'
        if route.path == '/search.json':
            status, body = 504, 'Gateway timeout'
        elif route.path == '/t/11.json':
            status, body = 503, 'Unavailable'
        elif route.path == '/user_actions.json':
            status, content, body = 404, 'application/json', '{}'
        elif route.path == '/u/Alpha.json':
            content, body = 'application/json', '{"user":{"username":"Alpha"}}'
        elif route.path == '/t/11/1':
            body = '''<div id="topic-title"><h1>Harbor Lights 11</h1></div>
            <article id="post_1"><div class="topic-meta-data"><div class="names"><div class="username">
            <a data-user-card="Alpha">Alpha</a></div></div></div><div class="cooked"><p>Harbor Lights 11</p>
            <a href="/uploads/Harbor%20Lights%2011.funscript">Download</a>
            <blockquote>Wrong quoted attachment</blockquote></div></article>
            <article id="post_2"><a data-user-card="WrongReply">WrongReply</a><div class="cooked">Reply</div></article>'''
        elif route.path == '/search':
            query = params.get('q', [''])[0]
            author = query.startswith('@Alpha ')
            items = result(11)
            if query == 'missing':
                items = '<div class="no-results-container">No results</div>'
            if author:
                items += result(99, 'WrongAuthor')
            body = f'''<div class="search-container"><input class="search-query" value="{html.escape(query, quote=True)}">
                <div class="search-results">{items}</div>
                <h3 class="search-footer">{'More results' if author else 'No more results'}</h3></div>'''
            if author:
                body += '''<div style="height:3000px"></div><script>
                let loaded = false; window.addEventListener('scroll', () => {
                    if (!loaded && window.scrollY > 100) {
                        loaded = true;
                        setTimeout(() => {
                            document.querySelector('.search-results').insertAdjacentHTML('beforeend', ''' + json.dumps(result(12)) + ''');
                            document.querySelector('.search-footer').textContent = 'No more results';
                        }, 80);
                    }
                });</script>'''
        else:
            body = '<!doctype html><title>Local web fallback test</title>'
        encoded = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content + '; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.calls = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as folder, patch('organizer.forum.BASE', f'http://127.0.0.1:{server.server_port}'):
            root = Path(folder)
            store = Store(root / 'catalog.db')
            try:
                events = []
                with BrowserClient(root, threading.Event(), lambda *args: events.append(args), headful=False,
                                   browser_channel='chromium', interval=0, search_interval=0, per_minute=0,
                                   cooldown_seconds=0) as client:
                    forum = Forum(client, store)
                    assert forum.search('Harbor Lights') == [{'id': 11, 'title': 'Harbor Lights 11'}]
                    assert forum.search('missing') == []
                    topic = forum.topic(11)
                    assert topic.author == 'Alpha' and topic.details_loaded
                    assert 'Harbor Lights 11.funscript' in topic.filenames
                    assert 'Wrong quoted' not in topic.body and 'Reply' not in topic.body
                    forum.author('Alpha')
                    forum.author('Alpha')
                    assert store.topic(12).author == 'Alpha' and not store.topic(12).details_loaded
                    assert store.topic(99) is None
                    assert not store.author('Alpha')['complete']
                    assert not any(kind == 'login' for kind, _ in events)
                    assert sum(route == '/user_actions.json' for route, _ in server.calls) == 3
                    assert sum(route == '/search.json' for route, _ in server.calls) == 1
                print('LOCAL_WEB_FALLBACK_OK ' + json.dumps({
                    'search_504_to_rendered_page': True, 'topic_503_to_first_post_dom': True,
                    'activity_404_not_retried_forever': True, 'author_search_scroll': True,
                    'wrong_author_and_reply_rejected': True, 'partial_index_not_marked_complete': True,
                    'cached_page_mode_skips_broken_api': True, 'no_manual_confirmation': True}))
            finally:
                store.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
