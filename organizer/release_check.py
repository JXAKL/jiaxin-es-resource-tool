"""Packaged application self-check: synthetic local files and loopback HTTP only."""
import ctypes
import json
import sys
import tempfile
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler
from .local_http_fixture import local_server
from pathlib import Path
from unittest.mock import patch

from .forum import BrowserClient, Cancelled
from .matching import MatchingSession
from .model import FileGroup, Topic, match_group, scan
from .moves import execute, undo
from .runtime import BUNDLE, FROZEN, APP_NAME
from .store import Store


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path in ('/search.json?q=web-fixture', '/t/987.json'):
            self.send_response(504)
            self.end_headers()
            return
        if self.path == '/unstable.json' and self.server.failures > 0:
            self.server.failures -= 1
            self.send_response(504)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'gateway timeout')
            return
        if self.path == '/always.json':
            self.send_response(503)
            self.end_headers()
            return
        body = b'{"fixture": true,"topics": []}' if self.path.endswith('.json') else b'<!doctype html><title>Packaged local test</title><p>Local fixture</p>'
        if self.path == '/search?q=web-fixture':
            body = b'''<div class="search-container"><input class="search-query" value="web-fixture">
                <div class="search-results"><div class="fps-result"><div class="author"><a data-user-card="Fixture"></a></div>
                <a class="search-link" href="/t/987/1"><span class="topic-title">Web Fixture Work</span></a></div></div>
                <h3 class="search-footer">No more results</h3></div>'''
        if self.path == '/t/987/1':
            body = b'''<div id="topic-title"><h1>Web Fixture Work</h1></div><article id="post_1">
                <div class="names"><div class="username"><a data-user-card="Fixture">Fixture</a></div></div>
                <div class="cooked"><p>Web Fixture Work</p></div></article>'''
        self.send_response(200)
        self.send_header('Content-Type', 'application/json' if self.path.endswith('.json') else 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(report_path):
    import app
    report = {'frozen': FROZEN, 'python': sys.version.split()[0], 'executable': sys.executable}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        assert FROZEN, 'This check must run from the packaged executable'
        with tempfile.TemporaryDirectory(prefix='es-packaged-test-') as temp:
            folder = Path(temp)
            app.STATE, app.REPORTS = folder / 'state', folder / 'reports'
            app.STATE.mkdir()
            root = tk.Tk()
            try:
                ui = app.App(root)
                root.update()
                report['gui'] = bool(root.winfo_ismapped())
                report['console_attached'] = bool(ctypes.windll.kernel32.GetConsoleWindow())
                report['default_browser'] = ui.browser_choice.get()
                report['app_name'] = root.title()
                report['dark_theme'] = root.cget('bg') == '#101113'
                report['custom_icon'] = (BUNDLE / 'assets' / 'jiaxin.ico').is_file()
                assert report['gui'] and not report['console_attached']
                assert report['default_browser'] == 'Chromium'
                assert report['app_name'] == APP_NAME
                assert report['dark_theme'] and report['custom_icon']
                assert ui.sync_search.get() is True
                ui.sync_search.set(False)
                ui._save()
                ui.sync_search.set(True)
                ui._load()
                assert ui.sync_search.get() is False
                report['sync_switch_persistence'] = True
            finally:
                root.destroy()
            from .integration_check import run_local_check
            report['local_tools'] = run_local_check(folder)
            from .collection_check import run_collection_check
            report['collection'] = run_collection_check(folder)
            from .completion_check import run_completion_check
            report['resource_completion'] = run_completion_check(folder)
            from .search_scraper_check import run_browser_check
            report['search_scraper'] = run_browser_check(folder)
            from .search_thumbnail_check import run_browser_check as run_thumbnail_check
            report['search_thumbnails'] = run_thumbnail_check(folder)
            from .parallel_check import run_parallel_check
            report['parallel_scoring'] = run_parallel_check()
            from .downloads.check import run_download_check
            report['resource_downloads'] = run_download_check(folder)
            topic = Topic(1, 'Moonlight Dance', 'FixtureAuthor', 'https://example.invalid/1',
                          body='Original title: Moonlight Dance', details_loaded=True)
            store = Store(folder / 'test.sqlite3')
            try:
                store.upsert(topic)
                assert store.topic(1) == topic
                store.export(folder / 'export')
                report['sqlite_and_export'] = True
            finally:
                store.close()
            score_path = folder / 'scores.sqlite3'
            matcher = MatchingSession(workers=2, cache_path=score_path)
            try:
                matcher.update([topic])
                results = matcher.refresh([FileGroup(str(n), 'Moonlight Dance') for n in range(24)])
                assert len(results) == 24 and all(m.selected for m in results)
                report['cached_batch_scoring'] = True
                weighted_topics = [Topic(n, f'Aurora Harbor Episode {n}', 'Alpha', '',
                                         body=f'Aurora Harbor Episode {n}' if n <= 3 else '',
                                         details_loaded=n <= 3) for n in range(1, 25)]
                matcher.update(weighted_topics)
                weighted_groups = [FileGroup(f'weight-{t.id}', t.title) for t in weighted_topics]
                results = matcher.refresh(weighted_groups)
                assert all(m.selected for m in results)
                assert matcher.author_support == {'alpha': 3}
                assert results[-1].candidates[0].author_support == 3
                assert not results[-1].candidates[0].verified
                report['author_weight_scoring'] = True
            finally:
                matcher.dispose()
            restored = MatchingSession(workers=2, cache_path=score_path)
            try:
                restored.update(weighted_topics)
                results = restored.refresh(weighted_groups)
                assert all(m.selected for m in results)
                assert restored.scored_pairs == 0 and restored.reused_pairs > 0
                report['persistent_score_cache_after_reopen'] = {'reused_pairs': restored.reused_pairs, 'new_scores': 0}
            finally:
                restored.dispose()
            source, target = folder / 'input', folder / 'output'
            source.mkdir()
            (source / 'Moonlight Dance.mp4').write_bytes(b'synthetic test data')
            (source / 'Moonlight Dance.funscript').write_text('{}', encoding='utf-8')
            (source / 'Moonlight Dance.pitch.funscript').write_text('{}', encoding='utf-8')
            matches = [match_group(g, [topic]) for g in scan([str(source)], str(target))]
            assert len(matches) == 1 and matches[0].group.display_name == 'Moonlight Dance.funscript'
            moved = execute(matches, target, folder / 'journals')
            assert moved['moved'] == 3
            assert undo(Path(moved['journal']))['restored'] == 3
            report['multi_axis_group'] = True
            separate = scan([str(source)], str(target), sync_search=False)
            assert len(separate) == 2
            assert {g.kind_label for g in separate} == {'funscript', '视频'}
            report['sync_search_both_modes'] = True
            report['move_and_undo'] = True
            server = local_server(Handler)
            server.failures = 2
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                with patch('organizer.forum.BASE', f'http://127.0.0.1:{server.server_port}'):
                    events = []
                    with BrowserClient(folder / 'browser-state', threading.Event(), lambda *args: events.append(args),
                                       headful=False, browser_channel='chromium', interval=0,
                                       search_interval=0, per_minute=0, cooldown_seconds=0) as browser:
                        assert browser.page.title() == 'Packaged local test'
                        executable = Path(browser.playwright.chromium.executable_path).resolve()
                        assert executable.is_relative_to(BUNDLE.resolve())
                        report['bundled_browser'] = str(executable.relative_to(BUNDLE))
                        report['browser_loopback'] = True
                        started = time.monotonic()
                        for _ in range(3):
                            assert browser.get('/search.json')['fixture'] is True
                        elapsed = time.monotonic() - started
                        assert elapsed < 5, 'A fixed request interval is still in effect'
                        report['custom_pacing_three_local_requests_seconds'] = round(elapsed, 3)
                        assert browser.get('/unstable.json')['fixture'] is True
                        assert not any(kind == 'login' for kind, _ in events)
                        assert browser.diagnostics.stats['outcomes']['504'] == 2
                        assert browser.diagnostics.stats['recoveries'] == 1
                        report['automatic_504_recovery_without_confirmation'] = True
                        data = browser.get('/search.json', {'q': 'web-fixture'})
                        assert data['topics'][0]['id'] == 987
                        data = browser.get('/t/987.json')
                        assert data['post_stream']['posts'][0]['username'] == 'Fixture'
                        report['rendered_page_search_and_topic_fallback'] = True
                        timer = threading.Timer(.2, browser.stop.set)
                        timer.start()
                        try:
                            browser.get('/always.json')
                            raise AssertionError('Expected Stop to interrupt automatic recovery')
                        except Cancelled:
                            report['stop_interrupts_automatic_retry'] = True
                        finally:
                            timer.cancel()
            finally:
                server.shutdown()
                server.server_close()
        report['passed'] = True
    except BaseException as exc:
        report['passed'], report['error'] = False, repr(exc)
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
