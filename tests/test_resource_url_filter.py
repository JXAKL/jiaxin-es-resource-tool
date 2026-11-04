import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from organizer.resource_info import resource_links, filter_resource_rows, payment_info
from organizer.resource_urls import resource_url, address_kind, text_urls
from organizer.completion import export_report as export_completion, build_rows
from organizer.completion_export import export_download_text
from organizer.resource_organize_report import export_report as export_organize
from organizer.collector import CollectionOptions, collect
from organizer.store import Store
from tests import test_completion as completion
from tests import test_resource_organize as organize


class ResourceURLTests(unittest.TestCase):
    def test_context_and_misleading_labels_cannot_promote_junk(self):
        junk=['https://cdn.example.com/a.JPG?download=1','https://i.imgur.com/image',
              'https://discuss.eroscripts.com/','https://discuss.eroscripts.com/#funscript',
              'https://discuss.eroscripts.com/t/anything.funscript','https://discuss.eroscripts.com/u/Author',
              'https://patreon.com/author/posts','https://creator.fanbox.cc/posts/123',
              'https://ko-fi.com/creator','https://x.com/user/status/123',
              'https://www.iwara.tv/profile/123','https://hanime1.me/','https://hanime1.me/watch',
              'https://gofile.io/','https://drive.google.com/drive/my-drive','https://mega.nz/login',
              'https://pixeldrain.com/user','https://1fichier.com/?login=1',
              'https://unknown.example.com/posts/123','https://mega.nz.evil.example/folder/id',
              'https://unknown.example.com/redirect?url=https://files.example.com/a.mp4']
        body='<p>Video and script download '+''.join(f'<a href="{u}">Video.funscript</a>' for u in junk)+'</p>'
        self.assertEqual(resource_links(completion.post(body=body)),[])

    def test_real_resources_survive_preview_images_signatures_and_quote_links(self):
        body='''<p>Free video and script
          <a href="/uploads/short-url/abc.funscript">Main.funscript</a>
          <a href="//mega.nz/folder/ABC#Key/file/Child">folder</a>
          <a href="https://cdn.example.com/thumb.webp"><img src="https://cdn.example.com/thumb.webp"></a>
          <a href="https://gofile.io/d/Folder"><img src="https://cdn.example.com/preview.jpg"></a>
          <a href="https://patreon.com/creator">Support my scripts</a></p>
          <blockquote><a href="https://gofile.io/d/Quote">script</a></blockquote>
          <img src="https://files.example.com/NotAnchor.mp4">
          <p><a href="https://cdn.example.com/movie.mp4?secure=Ab%2BC%3D&amp;expires=123#part">Video</a></p>'''
        links=resource_links(completion.post(body=body))
        self.assertEqual(len(links),4)
        urls={r['url'] for r in links}
        self.assertIn('https://mega.nz/folder/ABC#Key/file/Child',urls)
        self.assertIn('https://cdn.example.com/movie.mp4?secure=Ab%2BC%3D&expires=123#part',urls)
        self.assertTrue(all(not any(bad in u for bad in ('patreon','thumb','Quote','NotAnchor')) for u in urls))

    def test_missing_scheme_relative_uploads_and_old_wrong_es_prefix(self):
        cases={'gofile.io/d/ABC':'https://gofile.io/d/ABC',
               '//mega.nz/file/ABC#Key':'https://mega.nz/file/ABC#Key',
               '/uploads/name.funscript':'https://discuss.eroscripts.com/uploads/name.funscript',
               'uploads/name.funscript':'https://discuss.eroscripts.com/uploads/name.funscript',
               'https://discuss.eroscripts.com/mega.nz/folder/ABC#Key':'https://mega.nz/folder/ABC#Key',
               'https://files.example.com/a(1).mp4?key=(Keep)':'https://files.example.com/a(1).mp4?key=(Keep)'}
        for raw, expected in cases.items():self.assertEqual(resource_url(raw),expected)
        self.assertEqual(resource_url('uploads/a.funscript','https://discuss.eroscripts.com/t/123'),
                         'https://discuss.eroscripts.com/uploads/a.funscript')
        for raw in ('#link','/u/User','javascript:alert(1)','mailto:a@x.com','http//mega.nz/file/Ab',
                    'https://bad host/a.mp4','https://user:secret@files.example/a.mp4','https://x:invalid/a.mp4'):
            self.assertIsNone(resource_url(raw),raw)
        found=list(text_urls('Use gofile.io/d/ABC or //pixeldrain.com/l/DEF, mail hello@example.com.'))
        self.assertEqual(found,['https://gofile.io/d/ABC','https://pixeldrain.com/l/DEF'])

    def test_known_storage_and_video_object_routes_not_homepages(self):
        valid=['https://disk.yandex.com/d/Public','https://disk.yandex.com/i/Public',
               'https://drive.google.com/open?id=Object','https://drive.google.com/drive/u/0/folders/Object',
               'https://www.mediafire.com/file/Id/file.zip/file','https://1fichier.com/?abcd1234',
               'https://www.dropbox.com/scl/fi/Id/file.zip?rlkey=Key&dl=1',
               'https://1drv.ms/v/s!ABC','https://workupload.com/file/Public',
               'https://www.youtube.com/watch?v=ID','https://www.iwara.tv/video/ID',
               'https://hanime1.me/download?v=123','https://rule34video.com/video/123/name',
               'https://www.pornhub.com/view_video.php?viewkey=ID','https://spankbang.com/id/video/name',
               'https://www.eporner.com/video-123/name/','https://pmvhaven.com/video/Id',
               'https://faptap.net/v/ID','https://mmdfans.net/mmd/ID/name',
               'https://hmvmania.com/video/name/','https://hentaibar.com/videos/123/name/',
               'https://www.sexlikereal.com/scenes/name','https://www.redgifs.com/watch/ID',
               'https://files.catbox.moe/a.mp4','https://cdn.example.com/all.7z']
        for url in valid:self.assertIsNotNone(address_kind(url),url)
        for url in ('https://faptap.net/u/creator','https://pmvhaven.com/profile/Creator',
                    'https://www.sexlikereal.com/tags/Tag','https://www.eporner.com/','https://youtu.be/'):
            self.assertIsNone(address_kind(url),url)

    def test_extensionless_es_attachment_requires_filename_and_image_is_rejected(self):
        rows=[dict(url='/uploads/short-url/Object',label='Main.funscript'),
              dict(url='https://pixeldrain.com/u/Image',label='preview.png'),
              dict(url='/uploads/short-url/Image.jpg',label='Main.funscript'),
              dict(url='https://unknown.example.com/item',label='Main.funscript')]
        result=filter_resource_rows(rows)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['kind'],'script')

    def test_payment_stays_without_exporting_subscription_entry(self):
        topic=completion.post(body='<p>Paid script <a href="https://patreon.com/posts/123">Buy script</a></p>')
        self.assertEqual(payment_info(topic)['script'],'付费')
        self.assertEqual(resource_links(topic),[])

    def test_same_cloud_used_for_both_sides_keeps_both_meanings(self):
        topic=completion.post(body='<p>Video: <a href="https://gofile.io/d/Both">Video</a></p>'
                              '<p>Script: <a href="https://gofile.io/d/Both">Script</a></p>')
        self.assertEqual(resource_links(topic)[0]['kind'],'shared')

    def test_plain_body_cache_recovery_and_malformed_rows(self):
        topic=completion.post(body='')
        topic.body='Video: iwara.tv/video/ABC and https://files.example.com/a.mp4.'
        topic.resource_links=[dict(url='http://[bad',label=None),dict(url=14),dict(url='#top')]
        self.assertEqual(len(resource_links(topic)),2)

    def test_signed_download_attachment_query_not_image_or_arbitrary_label(self):
        for name,kind in [('file.funscript','script'),('file.mp4','video'),('file.zip','storage'),('file.png',None)]:
            self.assertEqual(address_kind('https://cdn.example.com/download?filename='+name),kind)


class ResourceExportFilterTests(unittest.TestCase):
    setUp=organize.ResourceOrganizeTests.setUp
    file=organize.ResourceOrganizeTests.file
    state=organize.ResourceOrganizeTests.state

    def junk(self):
        return [dict(kind='script',url='https://patreon.com/creator',scope='post'),
                dict(kind='video',url='https://images.example.com/pic.png',scope='post'),
                dict(kind='script',url='https://discuss.eroscripts.com/#reply',scope='post')]

    def test_bare_and_relative_old_links_export_as_absolute_resources(self):
        rows=build_rows([completion.group()],[completion.post()],85)
        rows[0]['candidates']=[dict(url='https://discuss.eroscripts.com/t/123',confirmed=True,
            payment=dict(tag='未知'),resources=[dict(kind='script',url='gofile.io/d/ABC'),
                dict(kind='script',url='uploads/a.funscript')])]
        state=dict(author='Alpha',status='done',rows=rows)
        result=export_download_text(state,self.root/'out')
        self.assertEqual((result['download'],result['manual']),(1,1))
        values=[]
        for name in result['files']:
            values += [s for s in (self.root/'out'/name).read_text(encoding='utf-8').splitlines() if s and not s.startswith('#')]
        self.assertEqual(set(values),{'https://gofile.io/d/ABC','https://discuss.eroscripts.com/uploads/a.funscript'})

    def test_completion_old_report_is_cleaned_in_every_format_and_old_txt_retired(self):
        topic=completion.post(body='<p><a href="https://gofile.io/d/File">script</a></p>')
        rows=build_rows([completion.group()],[topic],85)
        rows[0]['candidates'][0]['resources']+=self.junk()
        state=dict(author='Alpha',source='fixture',total=1,status='done',rows=rows,failed={})
        original=copy.deepcopy(state)
        folder=self.root/'report';folder.mkdir()
        for relative in ('按域名链接/patreon.com.txt','手动获取链接/patreon.com.txt'):
            p=folder/relative;p.parent.mkdir(exist_ok=True);p.write_text('old junk')
        (folder/'资源补齐报告.json').write_text(json.dumps(dict(domain_files=['patreon.com.txt'])))
        (folder/'下载链接导出清单.json').write_text(json.dumps(dict(files=['手动获取链接/patreon.com.txt'])))
        note=folder/'按域名链接/用户笔记.txt';note.write_text('keep')
        cleaned=export_completion(state,folder)
        self.assertEqual(state,original)
        self.assertEqual(len(cleaned['rows'][0]['candidates'][0]['resources']),1)
        self.assertFalse((folder/'按域名链接/patreon.com.txt').exists())
        self.assertFalse((folder/'手动获取链接/patreon.com.txt').exists())
        self.assertEqual(note.read_text(),'keep')
        for ext in ('txt','html','json','csv'):
            text=(folder/('资源补齐报告.'+ext)).read_text(encoding='utf-8')
            self.assertNotIn('patreon.com',text);self.assertNotIn('pic.png',text)

    def test_organize_saved_report_and_txt_are_clean_without_changing_selection(self):
        self.file('Aurora Harbor Midnight.mp4')
        state=self.state();candidate=state['rows'][0]['candidates'][0]
        candidate['resources']+=self.junk()
        original=copy.deepcopy(state)
        folder=Path(state['report_folder']);folder.mkdir()
        old=folder/'手动获取链接/patreon.com.txt';old.parent.mkdir();old.write_text('old')
        (folder/'链接清单.json').write_text(json.dumps(['手动获取链接/patreon.com.txt']))
        cleaned=export_organize(state)
        self.assertEqual(state,original);self.assertEqual(cleaned['rows'][0]['selected'],state['rows'][0]['selected'])
        self.assertFalse(old.exists())
        for ext in ('txt','html','json','csv'):
            text=(folder/('资源综合整理报告.'+ext)).read_text(encoding='utf-8')
            self.assertNotIn('patreon.com',text);self.assertNotIn('pic.png',text)

    def test_collection_subreport_and_cache_resume_do_not_reintroduce_junk(self):
        topic=completion.post(body='<p>Video script <a href="https://patreon.com/creator">Script</a>'
            '<a href="/uploads/a.png">Video</a><a href="gofile.io/d/File">script</a>'
            '<a href="/uploads/a.funscript">a.funscript</a></p>')
        store=Store(self.root/'store.db');self.addCleanup(store.close)
        store.upsert(topic);store.author_progress('Alpha',1,True)
        options=CollectionOptions('Alpha',self.root/'collect',types=['gofile'],custom='https?://',resource_details=True)
        client=Mock()
        for _ in range(2):
            with patch('organizer.collector.Forum.author',return_value='Alpha'),patch('organizer.collector.Forum.topic',return_value=topic):
                result=collect(options,store,client,self.root/'state')
            self.assertEqual(result['unique_links'],2)
            content=(options.output/'Alpha_Links_Unique.txt').read_text(encoding='utf-8')
            self.assertIn('https://gofile.io/d/File',content)
            self.assertIn('https://discuss.eroscripts.com/uploads/a.funscript',content)
            self.assertNotIn('patreon',content);self.assertNotIn('a.png',content)


if __name__=='__main__':unittest.main()
