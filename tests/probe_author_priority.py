"""Real bundled Chromium / local HTTP integration; no external forum access."""
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.engine import analyse
from organizer.forum import BrowserClient
from organizer.matching import MatchingSession
from organizer.model import FileGroup, Topic
from organizer.store import Store


def title(n):
    return f'Aurora Harbor Episode {n}'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        route = urlparse(self.path).path
        self.server.routes.append(route)
        if route.startswith('/t/'):
            n = int(route.rsplit('/', 1)[1].split('.')[0])
            data = {'id': n, 'title': title(n), 'post_stream': {'posts': [
                {'post_number': 1, 'username': 'Alpha', 'cooked': f'<p>{title(n)}</p>'}]}}
        elif route == '/search.json':
            data = {'topics': []}
        else:
            data = None
        body = json.dumps(data).encode() if data is not None else b'<!doctype html><title>Local priority test</title>'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json' if data is not None else 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.routes = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as folder, patch('organizer.forum.BASE', f'http://127.0.0.1:{server.server_port}'):
            state = Path(folder)
            store = Store(state / 'catalog.db')
            matcher = MatchingSession(workers=2)
            try:
                for n in range(1, 61):
                    store.upsert(Topic(n, title(n), 'Alpha', ''))
                store.author_progress('Alpha', 60, complete=True)
                groups = [FileGroup('unknown', 'Uncatalogued Crimson Landscape')]
                groups += [FileGroup(str(n), title(n)) for n in range(1, 61)]
                with BrowserClient(state, threading.Event(), lambda *_: None, headful=False,
                                   browser_channel='chromium', interval=0, search_interval=0, per_minute=0) as client:
                    started = time.monotonic()
                    results = analyse(groups, store, client, matcher=matcher)
                    elapsed = time.monotonic() - started
                posts = [route for route in server.routes if route.startswith('/t/')]
                assert posts == ['/t/1.json', '/t/2.json', '/t/3.json'], posts
                assert server.routes.index('/t/3.json') < server.routes.index('/search.json')
                assert all(m.selected and m.author == 'Alpha' for m in results[1:])
                assert all(not g.search_queries for g in groups[1:])
                inferred = sum(m.selected and any(c.author_bonus for c in m.candidates) for m in results)
                assert inferred == 57
                print('LOCAL_AUTHOR_PRIORITY_OK ' + json.dumps({
                    'indexed_works': 60, 'post_requests': len(posts), 'weighted_without_post_reads': inferred,
                    'known_works_search_requests': 0, 'known_works_before_unknown': True,
                    'local_elapsed_seconds': round(elapsed, 3), 'independent_support': matcher.author_support}))
            finally:
                matcher.close()
                store.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
