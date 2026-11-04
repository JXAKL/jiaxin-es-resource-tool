"""Real browser integration against a loopback fixture, never the external forum."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.engine import analyse, export_matches
from organizer.forum import BrowserClient
from organizer.model import FileGroup
from organizer.names import group_fragments
from organizer.store import Store


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        request = urlparse(self.path)
        params = parse_qs(request.query)
        self.server.requests.append((request.path, params))
        titles = {101: 'Moonlight Dance 01', 102: 'Moonlight Collection', 103: 'Harbor Lights'}
        if request.path == '/':
            self.reply(b'<!doctype html><title>Local fragment search test</title>', 'text/html')
            return
        if request.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        if request.path == '/search.json':
            query = params['q'][0]
            hits = [101, 102] if query == 'moonlight' else []
            data = {'posts': [], 'grouped_search_result': {'term': query, 'error': None}}
            if hits:
                data['topics'] = [{'id': identity, 'title': titles[identity]} for identity in hits]
        elif request.path.startswith('/t/'):
            identity = int(request.path.split('/')[-1].split('.')[0])
            body = 'Unrelated discussion and a keyword.' if identity == 102 else f'Original title: {titles[identity]}'
            data = {'id': identity, 'title': titles[identity], 'post_stream': {'posts': [
                {'post_number': 1, 'username': 'Beta' if identity == 102 else 'Alpha', 'cooked': f'<p>{body}</p>'},
                {'post_number': 2, 'username': 'ReplyUser', 'cooked': '<p>Moonlight Dance 01</p>'}]}}
        elif request.path == '/u/Alpha.json':
            data = {'user': {'username': 'Alpha', 'name': 'Local fixture author'}}
        elif request.path == '/user_actions.json' and params.get('username') == ['Alpha']:
            data = {'user_actions': [{'action_type': 4, 'post_number': 1, 'topic_id': identity,
                                     'post_id': identity * 10, 'username': 'Alpha', 'title': titles[identity]}
                                    for identity in (101, 103)] if params['offset'] == ['0'] else []}
        else:
            self.server.unexpected.append(self.path)
            self.send_error(404)
            return
        self.reply(json.dumps(data).encode(), 'application/json')

    def reply(self, body, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.requests, server.unexpected = [], []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    groups = [FileGroup('typo', 'Moonlight Dnace 01'), FileGroup('next', 'Harbor Lights')]
    def emit(kind, value):
        if kind == 'log':
            print(value, flush=True)
    try:
        with tempfile.TemporaryDirectory() as temp, patch('organizer.forum.BASE', base):
            store = Store(Path(temp) / 'catalog.sqlite3')
            try:
                with BrowserClient(Path(temp), threading.Event(), emit, headful=False, interval=1.5) as client:
                    assert client.page.url.startswith(base)
                    results = analyse(groups, store, client, emit, threshold=.85)
                assert all(m.author == 'Alpha' and m.selected for m in results)
                assert all(m.candidates[0].verified and m.candidates[0].evidence for m in results)
                assert .85 <= results[0].candidates[0].score < .95
                assert store.author('Alpha')['complete']
                assert store.author('Beta') is None
                assert store.topic(103).details_loaded, 'The next group must hydrate the author-list summary'
                queries = [p['q'][0] for route, p in server.requests if route == '/search.json']
                assert queries == group_fragments(groups[0]), queries
                assert any('dnace' not in q for q in queries)
                assert groups[0].search_hits['101'] == ['moonlight']
                assert not groups[1].search_queries, 'The remaining file should reuse the newly discovered author index'
                profiles = [route for route, _ in server.requests if route.startswith('/u/')]
                assert profiles == ['/u/Alpha.json'], profiles
                assert not server.unexpected, server.unexpected
                export_matches(results, Path(temp) / 'report')
                report = json.loads((Path(temp) / 'report' / '归类预览.json').read_text(encoding='utf-8'))
                assert report[0]['candidates'][0]['body_score'] > .8
                print('LOCAL_FRAGMENT_PIPELINE_OK', json.dumps({
                    'queries': queries, 'confirmed_author': 'Alpha',
                    'scores': [round(m.candidates[0].score * 100, 1) for m in results],
                    'rejected_author_not_crawled': True, 'remaining_file_reuses_author_index': True,
                    'request_count': len(server.requests)}), flush=True)
            finally:
                store.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
