"""Real Chromium collection exercise against a local test forum only."""
import json
import threading
from http.server import BaseHTTPRequestHandler
from .local_http_fixture import local_server
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch
from .collector import collect, CollectionOptions
from .forum import BrowserClient
from .store import Store
from .links import DOWNLOAD_TYPES
from .downloads.model import collection_items


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        route = urlparse(self.path)
        query = parse_qs(route.query)
        self.server.calls.append(route.path)
        status, kind = 200, 'text/html'
        if route.path == '/u/Fixture.json':
            kind, body = 'application/json', '{"user":{"username":"Fixture"}}'
        elif route.path == '/user_actions.json':
            offset = int(query.get('offset', ['0'])[0])
            rows = [dict(action_type=4, username='Fixture', topic_id=101 + offset, post_id=101 + offset,
                         title=f'Fixture {101 + offset}', created_at=f'2026-01-0{1 + offset}T00:00:00Z')] if offset < 2 else []
            kind, body = 'application/json', json.dumps({'user_actions': rows})
        elif route.path in ('/t/101.json', '/t/102.json'):
            status, body = 504, 'Gateway timeout'
        elif route.path in ('/t/101/1', '/t/102/1'):
            identity = int(route.path.split('/')[2])
            body = f'''<div id="topic-title"><h1>Fixture {identity}</h1></div>
                <article id="post_1"><div class="names"><div class="username"><a data-user-card="Fixture">Fixture</a></div></div>
                <time datetime="2026-01-0{identity-100}T00:00:00Z"></time><div class="cooked">
                <a href="https://gofile.io/d/Shared">Files</a>
                <a href="https://mega.nz/folder/AbC#FullSecretKey">Folder</a>
                <a href="https://hanime1.me/watch?v=101">Hanime1</a>
                <a href="https://www.iwara.tv/video/Fixture/title">Iwara</a>
                <a href="https://rule34video.com/video/101/fixture/">Rule34Video</a>
                <a href="https://pixeldrain.com/u/Fixture">Pixeldrain</a>
                <blockquote><a href="https://gofile.io/d/Quoted">Quoted</a></blockquote></div></article>
                <article id="post_2"><div class="cooked"><a href="https://gofile.io/d/Reply">Reply</a></div></article>'''
        else:
            body = '<!doctype html><title>Local collection fixture</title>'
        if self.server.author_failures.get(route.path, 0):
            self.server.author_failures[route.path] -= 1
            status, kind, body = 504, 'application/json', '{"errors":["temporary fixture outage"]}'
        encoded = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', kind + '; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def run_collection_check(folder):
    root = Path(folder) / 'collection-check'
    root.mkdir(parents=True, exist_ok=True)
    server = local_server(Handler)
    server.calls = []
    server.author_failures = {'/u/Fixture.json': 2, '/user_actions.json': 2}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    store = Store(root / 'catalog.db')
    events = []
    try:
        with patch('organizer.forum.BASE', f'http://127.0.0.1:{server.server_port}'):
            with BrowserClient(root, threading.Event(), lambda *args: events.append(args), headful=False,
                               browser_channel='chromium', interval=0, search_interval=0, per_minute=0,
                               cooldown_seconds=0) as client:
                store.author_progress('Fixture', 0, error='上轮网页补充，接口尚未完成')
                client._web_until['/u/Fixture.json'] = float('inf')
                options = CollectionOptions('Fixture', root/'out', ['gofile', 'mega_folder'])
                result = collect(options, store, client, root)
                assert result['posts'] == 2 and result['unique_links'] == 2 and result['status'] == '采集完成', result
                exported = json.loads((root/'out/Fixture_Links_Data.json').read_text(encoding='utf-8'))
                assert [r['id'] for r in exported['records']] == [102, 101]
                assert exported['records'][0]['date'] == '2026-01-02T00:00:00Z'
                assert server.calls.count('/u/Fixture.json') == 3
                assert server.calls.count('/user_actions.json') == 5
                assert '/t/101/1' in server.calls and '/t/102/1' in server.calls
                links = (root/'out/Fixture_Links_Unique.txt').read_text(encoding='utf-8').splitlines()
                assert links == ['https://gofile.io/d/Shared', 'https://mega.nz/folder/AbC#FullSecretKey'], links
                previous = list(server.calls)
                options.types = list(DOWNLOAD_TYPES)
                result = collect(options, store, client, root)
                assert result['unique_links'] == 6 and server.calls == previous
                expanded = json.loads((root/'out/Fixture_Links_Data.json').read_text(encoding='utf-8'))
                items, ignored = collection_items(expanded['records'])
                assert ignored == 0 and len(items) == 6
                assert {item.platform for item in items} == {'Hanime1', 'Iwara', 'Rule34Video', 'GoFile', 'Pixeldrain', 'MEGA'}
                assert all(len(item.sources) == 2 and item.title == 'Fixture 102' for item in items)
                domains = Path(result['domain_folder'])
                assert {p.name for p in domains.glob('*.txt')} == {
                    'hanime1.me.txt', 'iwara.tv.txt', 'rule34video.com.txt', 'gofile.io.txt', 'pixeldrain.com.txt', 'mega.nz.txt'}
                assert (domains/'mega.nz.txt').read_text(encoding='utf-8').splitlines() == ['https://mega.nz/folder/AbC#FullSecretKey']
                options.types = ['mega_folder']
                result = collect(options, store, client, root)
                assert result['unique_links'] == 1 and server.calls == previous
                assert [p.name for p in domains.glob('*.txt')] == ['mega.nz.txt']
                assert not any(kind == 'login' for kind, _ in events)
        return dict(real_browser=True, multipage_author=True, api_504_to_first_post_page=True,
                    author_api_retry_recovers_on_third_attempt=True, previous_profile_web_mode_reprobed=True,
                    quotes_and_replies_excluded=True, full_mega_key=True, first_post_date=True,
                    cached_refilter_without_requests=True, no_manual_confirmation=True,
                    all_six_download_sources_collected=True, resource_sources_deduplicated=True,
                    per_domain_text_exports_and_refilter_cleanup=True)
    finally:
        store.close()
        server.shutdown()
        server.server_close()
