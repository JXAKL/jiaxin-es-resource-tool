"""Local-only thumbnail download and real embedded animation playback checks."""
import io
import json
import threading
import time
import subprocess
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from .local_http_fixture import local_server
from urllib.parse import urlparse
from unittest.mock import patch
from PIL import Image
from .forum import BrowserClient
from .store import Store
from .search_scraper import ScrapeOptions,scrape


def sample_images(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    first=Image.new('RGB',(96,64),'red');second=Image.new('RGB',(96,64),'blue')
    first.save(folder/'still.png')
    for name,fmt in [('motion.gif','GIF'),('motion.webp','WEBP'),('motion.png','PNG')]:
        first.save(folder/name,format=fmt,save_all=True,append_images=[second],duration=240,loop=0)
    from .video_fixture import make_video
    from .local_tools.surfaces import player_executable
    make_video(folder/'source.avi',seconds=1)
    subprocess.run([str(player_executable()),'--no-config','--no-audio','--ovc=libx264','--of=mp4',
                    '--o='+str(folder/'clip.mp4'),str(folder/'source.avi')],check=True,timeout=15,
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    return folder


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        path=urlparse(self.path).path;self.server.calls.append(path)
        status,kind,body=200,'text/html',b'<title>Local thumbnail fixture</title>'
        if path=='/categories':body=b'<a href="/c/free-scripts/7"><span class="category-name">Free Scripts</span></a>'
        elif path=='/search.json':
            kind='application/json';body=json.dumps(dict(topics=[dict(id=i,title=f'(Creator) Work {i}') for i in (1,2,3)],grouped_search_result={})).encode()
        elif path.startswith('/t/'):
            identity=int(path.split('/')[-1].split('.')[0])
            cooked=('<div class="ad-banner"><img src="/advert.png"></div><a href="https://patreon.com/Fixture"><img src="/support.png"></a><img src="/motion.gif"><img src="/still.png"><img src="/still.png#copy"><video src="/clip.mp4"></video>' if identity==1 else '<img src="/invalid.png"><img src="/still.png">' if identity==2 else '<img src="/excluded.png">')
            kind='application/json';body=json.dumps(dict(id=identity,title=f'(Creator) Work {identity}',category_id=7,tags=['mmd']+(['excluded'] if identity==3 else []),post_stream=dict(posts=[dict(post_number=1,username='Poster',cooked=cooked)]))).encode()
        elif path in ('/motion.gif','/still.png','/clip.mp4'):
            if 'session=fixture' not in self.headers.get('Cookie',''):status,body=403,b'No session'
            else:kind='image/gif' if path.endswith('.gif') else 'image/png';body=(self.server.media/path[1:]).read_bytes()
        elif path=='/invalid.png':body=b'<html>Not an image</html>'
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
        if path=='/':self.send_header('Set-Cookie','session=fixture; Path=/; HttpOnly')
        self.end_headers();self.wfile.write(body)


def run_browser_check(folder):
    root=Path(folder)/'thumbnail-browser';root.mkdir(parents=True,exist_ok=True)
    server=local_server(Handler);server.calls=[];server.media=sample_images(root/'media')
    threading.Thread(target=server.serve_forever,daemon=True).start();store=Store(root/'catalog.db')
    stop=threading.Event();timer=threading.Timer(70,stop.set);timer.start()
    try:
        with patch('organizer.forum.BASE',f'http://127.0.0.1:{server.server_port}'):
            with BrowserClient(root,stop,lambda *_:None,headful=False,browser_channel='chromium',interval=0,search_interval=0,per_minute=0,cooldown_seconds=0) as client:
                options=ScrapeOptions(root/'out','mmd',exclude_tags='excluded',thumbnail_dir=root/'cache')
                result=scrape(options,store,client,root)
                assert not (root/'cache').exists() and '/still.png' not in server.calls
                options.thumbnails=True;result=scrape(options,store,client,root)
                assert len(result['records'])==2 and '/excluded.png' not in server.calls
                assert len(result['records']['1']['preview']['files'])==3,result
                assert result['records']['2']['preview']['errors'] and len(result['records']['2']['preview']['files'])==1
                assert '/advert.png' not in server.calls and '/support.png' not in server.calls
                assert server.calls.count('/still.png')==1,'Identical preview downloaded again for another post'
                count=server.calls.count('/motion.gif');scrape(options,store,client,root)
                assert server.calls.count('/motion.gif')==count,'Valid media must be reused'
                assert not list((root/'cache').glob('*.part'))
        return dict(real_browser_session_cookies=True,switch_off_no_media_requests=True,
                    exclude_before_download=True,static_and_animated_download=True,cache_reuse=True,invalid_media_keeps_post=True,
                    advertisements_not_requested=True,cross_post_single_download=True)
    finally:timer.cancel();store.close();server.shutdown();server.server_close()


def exercise_ui(shell,root,folder):
    assert shell.show('search_scraper');page=shell.search_scraper
    cache=sample_images(Path(folder)/'thumbnail-ui')
    page.thumbnail_dir.set(str(cache));page.thumbnails.set(True);page.preview_controls();page.save()
    page.thumbnails.set(False);page.thumbnail_dir.set('');page._load()
    assert page.thumbnails.get() and page.thumbnail_dir.get()==str(cache)
    page.preview_controls();root.geometry('1180x820');root.update()
    rows={str(i):dict(id=i,title=f'Preview {i}',author='PreviewAuthor',date='',url=f'https://example.invalid/{i}',
         video_creators=['Creator'],payment=dict(script='免费',post='未知',video='未知',evidence=[]),resources=[],links={'resources':[]},
         preview=dict(root=str(cache),files=[dict(file=name,kind='image')],errors=[]))
         for i,name in enumerate(('still.png','motion.gif','motion.webp','motion.png','clip.mp4'),1)}
    state=dict(job='abcdef1234567890',records=rows,excluded={},failed={},removed=[],discovered=5,status='预览测试',
               tag='mmd',creator='',report_folder=str(Path(folder)/'preview-report'))
    page.current_output=state['report_folder'];page.publish(state);root.update()
    from types import SimpleNamespace
    def wait(predicate,seconds=8):
        deadline=time.monotonic()+seconds
        while not predicate() and time.monotonic()<deadline:root.update();time.sleep(.01)
        assert predicate(),(page.preview.title.get(),page.preview.identity,page.preview.ready,page.preview.position,page.preview.video_params)
    for key in rows:
        page.tree.see(key);root.update()
        box=page.tree.bbox(key)
        page.hover(SimpleNamespace(y=box[1]+box[3]//2))
        wait(lambda:page.preview.identity and str(page.preview.identity[0])==key and page.preview.video_params)
        if key=='5':wait(lambda:page.preview.position>.05)
        elif key!='1':
            first_color=root.tk.call(str(page.preview.photo),'get',1,1)
            wait(lambda:page.preview.frame_count>=2 and root.tk.call(str(page.preview.photo),'get',1,1)!=first_color)
    process=page.preview.player.process
    assert page.preview.panel.winfo_ismapped() and page.tree.winfo_width()>150
    assert page.preview.panel.winfo_rootx()+page.preview.panel.winfo_width()<=root.winfo_rootx()+root.winfo_width()
    page.tree.selection_set('2');page.delete_button.invoke();wait(lambda:not page.busy)
    assert '2' not in page.records and page.ranking.item('0','values')[1]=='4'
    saved=json.loads((Path(state['report_folder'])/'搜索刮削报告.json').read_text(encoding='utf-8'))
    assert '2' not in saved['records'] and '2' in saved['removed']
    assert (cache/'motion.gif').is_file(),'Removing a result must not delete shared cache files'
    assert page.preview.frames is None and page.preview.player is None
    wait(lambda:process.poll() is not None)
    page.select();assert shell.show('collector');wait(lambda:page.preview.player is None)
    assert shell.show('search_scraper');root.update()
    from .preview_blacklist import KNOWN_LEGACY_URL_HASHES
    blocked_name='99-'+next(iter(KNOWN_LEGACY_URL_HASHES))+'.png'
    (cache/blocked_name).write_bytes((cache/'still.png').read_bytes())
    page.preview.show(dict(id=99,preview=dict(root=str(cache),files=[dict(file=blocked_name)])))
    assert not page.preview.files and '已过滤广告' in page.preview.title.get()
    assert (cache/blocked_name).is_file(), 'Filtering must not delete old cache files'
    return dict(hover_static=True,animated_gif_webp_apng=True,embedded_mp4=True,cache_setting_persists=True,
                delete_updates_report_and_counts=True,player_cleanup=True,minimum_window=True,legacy_ad_hidden=True)
