import json
import tempfile
import unittest
from pathlib import Path

from organizer.completion_export import export_download_text
from organizer.downloads.model import parse_items


def candidate(confirmed=True, resources=None):
    return dict(confirmed=confirmed, url='https://discuss.eroscripts.com/t/1', payment=dict(tag='付费'),
                resources=resources or [dict(kind='video',url='https://www.iwara.tv/video/Example/title')])


def state():
    return dict(author='Alpha',status='补齐报告完成',rows=[dict(title='Aurora Harbor',missing='video',missing_label='缺视频',
        candidates=[candidate(),candidate(resources=[dict(kind='video',url='https://mega.nz/folder/Test#FullKey'),
            dict(kind='purchase',url='https://patreon.com/creator'),dict(kind='unknown',url='https://drive.google.com/file/d/Test')]),
            candidate(False,[dict(kind='video',url='https://gofile.io/d/Review')])]),
        dict(title='Dawn Harbor',missing='script',missing_label='缺脚本',candidates=[candidate(resources=[
            dict(kind='script',url='https://discuss.eroscripts.com/uploads/Example.funscript'),
            dict(kind='script',url='https://gofile.io/d/Script')])])])


class CompletionExportTests(unittest.TestCase):
    def test_domain_headers_import_keys_manual_and_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);data=state()
            data['rows'][0]['candidates'].append(candidate())
            result=export_download_text(data,folder)
            self.assertEqual((result['download'],result['manual']),(3,2))
            items=[]
            for path in (folder/'下载用链接').glob('*.txt'):
                text=path.read_text(encoding='utf-8')
                self.assertTrue(text.startswith('# 资源补齐'))
                items.extend(parse_items(text))
            self.assertEqual(len(items),3)
            self.assertIn('https://mega.nz/folder/Test#FullKey',[x.url for x in items])
            self.assertNotIn('https://gofile.io/d/Review',[x.url for x in items])
            self.assertFalse((folder/'手动获取链接/patreon.com.txt').is_file())
            self.assertTrue((folder/'手动获取链接/discuss.eroscripts.com.txt').is_file())

    def test_filters_and_reexport_retire_only_manifest_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);data=state();export_download_text(data,folder)
            userfile=folder/'下载用链接/用户笔记.txt';userfile.write_text('keep')
            data['download_export']=dict(scope='script')
            result=export_download_text(data,folder)
            self.assertEqual(result['download'],1)
            self.assertFalse((folder/'下载用链接/iwara.tv.txt').exists())
            self.assertTrue(userfile.is_file())
            self.assertEqual([x.url for x in parse_items((folder/'下载用链接/gofile.io.txt').read_text(encoding='utf-8'))],['https://gofile.io/d/Script'])
            data['download_export']=dict(enabled=False)
            result=export_download_text(data,folder)
            self.assertEqual(result['files'],[])
            self.assertFalse((folder/'下载用链接/gofile.io.txt').exists())
            self.assertTrue(userfile.is_file())

    def test_review_opt_in_error_exclusion_and_comment_url_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=state();data['download_export']=dict(include_review=True,scope='video')
            data['rows'][0]['candidates'].append(dict(candidate(),read_error='403',resources=[dict(kind='video',url='https://gofile.io/d/Failed')]))
            export_download_text(data,tmp)
            text=(Path(tmp)/'下载用链接/gofile.io.txt').read_text(encoding='utf-8')
            self.assertIn('待核对',text)
            self.assertEqual([x.url for x in parse_items(text)],['https://gofile.io/d/Review'])
            text='\ufeff # 来源：https://gofile.io/d/NotAResource\n# https://iwara.tv/video/Comment\n'+text
            self.assertEqual([x.url for x in parse_items(text)],['https://gofile.io/d/Review'])

    def test_multiline_titles_cannot_inject_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=state();data['author']='Alpha\nhttps://gofile.io/d/Injected'
            data['rows'][0]['title']='Title\nhttps://gofile.io/d/Injected'
            export_download_text(data,tmp)
            parsed=[item.url for f in (Path(tmp)/'下载用链接').glob('*.txt') for item in parse_items(f.read_text(encoding='utf-8'))]
            self.assertNotIn('https://gofile.io/d/Injected',parsed)


if __name__=='__main__':unittest.main()
