"""Actual browser and completion pipeline, using an isolated loopback forum."""
import json
import threading
from http.server import BaseHTTPRequestHandler
from .local_http_fixture import local_server
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch

from .completion import CompletionOptions, complete_resources
from .forum import BrowserClient
from .store import Store


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        route = urlparse(self.path); self.server.calls.append(route.path)
        status, kind = 200, 'text/html'
        if route.path == '/categories':
            body = '<div class="category-list"><a href="/c/scripts/paid-scripts/12"><span class="category-name">Paid Scripts</span></a></div>'
        elif route.path == '/u/Fixture.json':
            kind, body = 'application/json', '{"user":{"username":"Fixture"}}'
        elif route.path == '/user_actions.json':
            offset = int(parse_qs(route.query).get('offset',['0'])[0])
            rows = [] if offset else [dict(action_type=4, username='Fixture', topic_id=i, post_id=i, title=title)
                for i,title in [(101,'Aurora Harbor Midnight'),(102,'Aurora Harbor Morning')]]
            kind, body = 'application/json', json.dumps({'user_actions':rows})
        elif route.path == '/t/101.json':
            kind, body = 'application/json', json.dumps(dict(id=101,title='Aurora Harbor Midnight',category_id=12,created_at='2023-12-31T18:00:00Z',
                post_stream=dict(posts=[dict(post_number=1,username='Fixture',cooked='<p>Free video</p><p><a href="https://patreon.com/fixture/posts/101">Paid script</a>'
                    '<a href="/uploads/preview.png">script video</a>'
                    '<a href="https://discuss.eroscripts.com/">script video</a></p>')])) )
        elif route.path == '/t/102.json':
            status, body = 504, 'Gateway timeout'
        elif route.path == '/t/102/1':
            body = '''<div id="topic-title"><h1>Aurora Harbor Morning</h1><span class="badge-category__name">Free Scripts</span><span class="discourse-tag">video-paid</span></div>
                <article id="post_1"><div class="names"><div class="username"><a data-user-card="Fixture">Fixture</a></div></div>
                <div class="cooked"><p>Video is paid, script is free.</p><p><a href="https://mega.nz/folder/Video#FullKey">video</a></p>
                <p><a href="/uploads/short-url/file.funscript">Aurora Harbor Morning.funscript</a></p>
                <blockquote>Free video <a href="https://gofile.io/d/Quoted">video</a></blockquote></div></article>'''
        else:
            body = '<!doctype html><title>Local resource completion check</title>'
        data = body.encode('utf-8')
        self.send_response(status); self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)


def run_completion_check(folder):
    root = Path(folder)/'completion-check'; root.mkdir(parents=True, exist_ok=True)
    media = root/'media'; media.mkdir()
    (media/'Aurora Harbor Midnight.mp4').write_bytes(b'synthetic only')
    nested = media/'nested'; nested.mkdir()
    (nested/'Aurora Harbor Morning.funscript').write_text('{"actions":[]}')
    (nested/'Aurora Harbor Morning.pitch.funscript').write_text('{"actions":[]}')
    server = local_server(Handler); server.calls=[]
    threading.Thread(target=server.serve_forever,daemon=True).start()
    store = Store(root/'catalog.db')
    events=[]
    try:
        with patch('organizer.forum.BASE', f'http://127.0.0.1:{server.server_port}'):
            result = complete_resources(CompletionOptions(media,'Fixture',root/'out'),store,
                lambda: BrowserClient(root,threading.Event(),lambda *e:events.append(e),headful=False,
                    browser_channel='chromium',interval=0,search_interval=0,per_minute=0,cooldown_seconds=0), root)
        assert result['status']=='补齐报告完成',result
        assert len(result['rows'])==2 and result['total']==2,result
        assert '/t/102/1' in server.calls,server.calls
        row={r['missing']:r for r in result['rows']}
        assert row['script']['candidates'][0]['payment']['script']=='付费',row
        assert row['video']['candidates'][0]['payment']['video']=='付费',row
        topic=store.topic(102)
        assert topic.category_name=='Free Scripts' and topic.tags==['video-paid'],topic
        assert any(r['url'].startswith('http://127.0.0.1:') and r['url'].endswith('.funscript') for r in topic.resource_links)
        resources=row['video']['candidates'][0]['resources']
        assert [r['url'] for r in resources]==['https://mega.nz/folder/Video#FullKey'],resources
        report=Path(result['report_folder'])
        assert (report/'资源补齐报告.html').is_file()
        assert (report/'按域名链接/mega.nz.txt').read_text().strip().endswith('#FullKey')
        from .downloads.model import parse_items
        downloadable=(report/'下载用链接/mega.nz.txt').read_text(encoding='utf-8')
        assert downloadable.startswith('# 资源补齐')
        assert [item.url for item in parse_items(downloadable)]==['https://mega.nz/folder/Video#FullKey']
        assert not (report/'手动获取链接/patreon.com.txt').exists()
        assert not (report/'按域名链接/patreon.com.txt').exists()
        assert row['script']['candidates'][0]['resources']==[]
        assert len(list(media.rglob('*.*')))==3
        from .resource_organize import Source, OrganizeOptions, prepare, execute
        before=len(server.calls)
        with patch('organizer.forum.BASE', f'http://127.0.0.1:{server.server_port}'):
            organized=prepare(OrganizeOptions([Source(str(media))],'Fixture',root/'organized',refresh=True,workers=1),store,
                lambda: BrowserClient(root,threading.Event(),lambda *e:events.append(e),headful=False,
                    browser_channel='chromium',interval=0,search_interval=0,per_minute=0,cooldown_seconds=0),root)
        assert '/t/102/1' in server.calls[before:],server.calls[before:]
        selected=[r for r in organized['rows'] if r['selected']]
        assert len(selected)==1 and selected[0]['month']=='2024/01',organized
        assert any('发布日期' in r['status'] for r in organized['rows'])
        copied=execute(organized,'copy')
        assert (root/'organized/Fixture/2024/01/Aurora Harbor Midnight.mp4').is_file()
        assert len(list(media.rglob('*.*')))==3
        assert any(r['status']=='复制完成' for r in copied['rows'])
        return dict(real_browser=True,recursive_multi_axis_inventory=True,shared_collection=True,
            api_and_rendered_fallback=True,category_and_tags=True,relative_script_attachment=True,
            payment_by_resource=True,domain_exports_preserve_keys=True,media_untouched=True,
            images_navigation_and_support_filtered=True,paid_evidence_kept_without_purchase_urls=True,
            comprehensive_live_collection_and_dated_copy=True)
    finally:
        store.close(); server.shutdown(); server.server_close()
