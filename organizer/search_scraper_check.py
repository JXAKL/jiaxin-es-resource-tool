"""Exercise real Tk controls and a real browser against a loopback-only forum."""
import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from .local_http_fixture import local_server
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch
from .search_scraper import ScrapeOptions, scrape, inspect_topic
from .forum import BrowserClient, topic_from_json
from .store import Store


def fixture(identity):
    return dict(id=identity, title=f'(Kaotaro12) Fixture {identity}', category_id=7,
                tags=['mmd'], created_at='2026-10-18T00:00:00Z', post_stream=dict(posts=[dict(
                    post_number=1, username='PosterA' if identity != 3 else 'PosterB',
                    cooked='<p><a href="https://gofile.io/d/Fixture">video and script</a>'
                           '<a href="https://patreon.com/Fixture">Support</a><a href="https://example.org/p.jpg">Preview</a></p>')]))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def row(self, identity):
        row=fixture(identity)
        if self.server.filter_case:
            row['tags']=['mmd','vr'] if identity!=4 else ['mmd']
            if identity in (2,3):row['category_id']=8
            if identity==3:
                row['tags'].append('blocked')
                row['post_stream']['posts'][0]['cooked']='<p><a href="https://gofile.io/d/Excluded">private excluded data</a></p>'
        return row
    def do_GET(self):
        route=urlparse(self.path); self.server.calls.append(route.path)
        status, kind, body=200,'text/html','<title>Loopback fixture</title>'
        if route.path=='/categories':
            body='<a href="/c/free-scripts/7"><span class="category-name">Free Scripts</span></a><a href="/c/paid-scripts/8"><span class="category-name">Paid Scripts</span></a>'
        elif route.path=='/search.json':
            if self.server.fallback:
                status,body=504,'Unavailable'
            else:
                page=int(parse_qs(route.query)['page'][0]);ids=[1,2] if page==1 else [2,3]
                if self.server.filter_case and page!=1:ids.append(4)
                kind='application/json';body=json.dumps(dict(topics=[dict(id=i,title=fixture(i)['title']) for i in ids],
                    grouped_search_result=dict(more_full_page_results=page==1)))
        elif route.path=='/search':
            query=html.escape(parse_qs(route.query)['q'][0],quote=True)
            body=f'<div class="search-container"><input class="search-query" value="{query}"><div class="search-results"><div class="fps-result"><a class="search-link" href="/t/1">(Kaotaro12) Fixture 1</a></div></div><div class="search-footer">More results</div><div style="height:3000px"></div></div>'
            extra=''.join(f'<div class="fps-result"><a class="search-link" href="/t/{i}">(Kaotaro12) Fixture {i}</a></div>' for i in ([2,3,4] if self.server.filter_case else [2]))
            body+='<script>window.addEventListener("scroll",()=>{document.querySelector(".search-results").innerHTML+='+json.dumps(extra)+';document.querySelector(".search-footer").textContent="No more results";},{once:true});</script>'
        elif route.path.startswith('/t/') and route.path.endswith('.json'):
            if self.server.fallback:status,body=504,'Unavailable'
            else:
                kind='application/json';body=json.dumps(self.row(int(route.path.split('/')[2].split('.')[0])))
        elif route.path.startswith('/t/'):
            row=self.row(int(route.path.split('/')[2]));post=row['post_stream']['posts'][0]
            category='Paid Scripts' if row['category_id']==8 else 'Free Scripts'
            tags=''.join(f'<span class="discourse-tag">{tag}</span>' for tag in row['tags'])
            body=f'<div id="topic-title"><h1>{row["title"]}</h1><span class="badge-category__name">{category}</span>{tags}</div><article id="post_1"><div class="names"><div class="username"><a data-user-card="{post["username"]}">author</a></div></div><div class="cooked">{post["cooked"]}</div></article>'
        data=body.encode('utf-8');self.send_response(status);self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)


def run_browser_check(folder):
    root=Path(folder)/'search-browser-check';root.mkdir(parents=True,exist_ok=True)
    server=local_server(Handler);server.calls=[];server.fallback=False
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        for fallback,filter_case in ((False,False),(True,False),(False,True),(True,True)):
            server.fallback=fallback
            server.filter_case=filter_case
            state_dir=root/f'{fallback}-{filter_case}';store=Store(state_dir/'catalog.db')
            try:
                with patch('organizer.forum.BASE',f'http://127.0.0.1:{server.server_port}'):
                    stop=threading.Event()
                    deadline=threading.Timer(90,stop.set);deadline.start()
                    with BrowserClient(state_dir,stop,lambda *_:None,headful=False,browser_channel='chromium',
                                       interval=0,search_interval=0,per_minute=0,cooldown_seconds=0) as client:
                        options=ScrapeOptions(state_dir/'out','mmd,vr' if filter_case else 'mmd','Kaotaro12',
                                              exclude_tags='blocked,other' if filter_case else '',include_paid=filter_case)
                        result=scrape(options,store,client,state_dir)
                        if filter_case:
                            assert set(result['records'])=={'1','2'},result
                            options.include_paid=False
                            free=scrape(options,store,client,state_dir)
                            assert set(free['records'])=={'1'},free
                assert result['status']=='搜索完成',result
                assert len(result['records'])==(2 if fallback or filter_case else 3),result
                assert all(r['author']!='Kaotaro12' for r in result['records'].values())
                assert all(r['links']['resources']==['https://gofile.io/d/Fixture'] for r in result['records'].values())
            finally:
                deadline.cancel();store.close()
        assert '/search' in server.calls and '/t/1/1' in server.calls
        return dict(real_browser=True,api_pages=True,rendered_search_and_topics=True,
                    resume_search_after_topic_navigation=True,tag_creator_free_checks=True,resource_filter=True,
                    multiple_tags_all_required=True,exclude_after_fetch=True,paid_toggle=True)
    finally:server.shutdown();server.server_close()


def exercise_ui(shell,root,folder):
    assert shell.show('search_scraper');page=shell.search_scraper
    root.geometry('1180x820');root.update()
    page.tag.set('mmd,vr');page.creator.set('Kaotaro12');page.exclude_tags.set('blocked,other');page.paid_switch.toggle();page.save()
    page.exclude_tags.set('');page.include_paid.set(False)
    page.creator.set('');page._load();assert page.creator.get()=='Kaotaro12'
    assert page.tag.get()=='mmd,vr' and page.exclude_tags.get()=='blocked,other' and page.include_paid.get()
    rows={}
    for i in (1,2,3):
        data=fixture(i)
        if i==2:data['category_id']=8
        row,_=inspect_topic(topic_from_json(data),'mmd','Kaotaro12',{'7':'Free Scripts','8':'Paid Scripts'},include_paid=True)
        rows[str(i)]=row
    state=dict(records=rows,discovered=4,status='测试',excluded={},failed={})
    page.publish(state);root.update()
    assert page.tree.set('2','payment')=='付费'
    assert page.ranking.item('0','values')==('PosterA','2')
    page.ranking.selection_set('0');page.select_author();root.update()
    assert set(page.tree.get_children())=={'1','2'}
    page.clear_filter();root.update();assert len(page.tree.get_children())==3
    page.tree.selection_set('1');page.select();assert 'ES 作者：PosterA' in page.detail.get()
    assert '视频作者：Kaotaro12' in page.detail.get()
    page.busy=True;page.controls(True)
    assert page.exclude_entry.cget('state')=='disabled' and page.paid_switch.cget('state')=='disabled'
    assert not shell.show('collector') and shell.current=='search_scraper'
    assert shell.nav['search_scraper'].cget('fg_color')==__import__('organizer.workspace',fromlist=['ACCENT']).ACCENT
    page.stop_button.invoke();assert page.cancel.is_set()
    page.busy=False;page.controls(False)
    assert len(page.records)==3
    for _ in range(3):
        assert shell.show('collector');assert shell.show('search_scraper');root.update()
    controls=list(shell.nav.values())+[shell.back,page.start_button,page.stop_button,page.ranking,page.tree,page.exclude_entry,page.paid_switch]
    labels=[getattr(child, '_text', '') for child in shell.back.master.winfo_children()]
    assert labels.count('搜索刮削')==1, 'Duplicate navigation entry'
    for control in controls:
        assert control.winfo_ismapped(), str(control) + ' ' + str(getattr(control, '_text', 'table'))
        assert control.winfo_rootx()+control.winfo_width()<=root.winfo_rootx()+root.winfo_width()
        assert control.winfo_rooty()+control.winfo_height()<=root.winfo_rooty()+root.winfo_height()
    assert page.tree.winfo_height() >= 140, 'Post list must show usable rows, not only its heading'
    page.tree.selection_set('1')
    with patch.object(shell,'queue_resources',return_value=1) as queued:
        page.queue_downloads();assert len(queued.call_args.args[0])==1
    captured=[]
    with patch('organizer.search_scraper_ui.BrowserClient'),patch('organizer.search_scraper_ui.scrape',side_effect=lambda options,*args:captured.append(options)):
        page.start_button.invoke()
        deadline=time.monotonic()+5
        while page.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
        assert not page.busy and len(captured)==1
        assert captured[0].include_paid and captured[0].exclude_tags=='blocked,other' and captured[0].tag=='mmd,vr'
    return dict(ranking=True,author_filter=True,persistence=True,minimum_window=True,busy_guard=True,download_queue=True,
                multi_tag_exclusion_and_paid_controls=True,worker_receives_settings=True)
