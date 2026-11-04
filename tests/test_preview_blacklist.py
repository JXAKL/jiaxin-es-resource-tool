import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image

from organizer.preview_blacklist import KNOWN_UPLOADS
from organizer.preview_policy import blocked_reason, visible_files, cache_key
from organizer.search_thumbnails import extract_previews, PreviewCache


class PreviewBlacklistTests(unittest.TestCase):
    def test_confirmed_upload_blocks_original_and_resized_variants(self):
        for key in KNOWN_UPLOADS:
            for prefix,tail in [('original',''),('optimized','_2_690x100')]:
                self.assertTrue(blocked_reason(f'https://eroscripts-discourse.eroscripts.com/{prefix}/4X/a/b/c/{key}{tail}.png'))
        self.assertFalse(blocked_reason('https://example.org/original/4X/a/b/c/'+next(iter(KNOWN_UPLOADS))+'.png'))

    def test_structural_ads_and_support_not_legitimate_covers(self):
        html='''<div class="discourse-adplugin"><p><img src="/x.png"></p></div>
          <a href="https://www.patreon.com/creator"><img src="/join.png"></a>
          <img src="/promo.png" alt="15% discount promo code">
          <img src="/work.gif" alt="Adventure work"><img src="/work.gif#duplicate">
          <a href="https://patreon.com/posts/work-123"><img src="/real-cover.png"></a>
          <img src="/heatmap.png"><img src="/preview.png">'''
        rows=extract_previews(html,'https://discuss.eroscripts.com/t/1')
        self.assertEqual([Path(r['url']).name for r in rows],['work.gif','real-cover.png','heatmap.png','preview.png'])

    def test_ads_do_not_consume_the_eight_preview_slots(self):
        ad=next(iter(KNOWN_UPLOADS))
        html=''.join(f'<img src="https://eroscripts-discourse.eroscripts.com/original/4X/{ad}.png?x={i}">' for i in range(12))
        rows=extract_previews(html+'<img src="/work.png">','https://discuss.eroscripts.com/t/1')
        self.assertEqual(len(rows),1);self.assertTrue(rows[0]['url'].endswith('/work.png'))

    def test_legacy_preview_hidden_without_deleting_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);key=next(iter(KNOWN_UPLOADS))
            url=f'https://eroscripts-discourse.eroscripts.com/optimized/4X/{key}_2_420x90.png'
            file='99-'+hashlib.sha256(url.encode()).hexdigest()[:20]+'.png'
            (root/file).write_bytes(b'preserve')
            (root/'99.json').write_text(json.dumps(dict(sources=[dict(url=url)])))
            preview=dict(root=str(root),files=[dict(file=file),dict(file='real.gif')])
            self.assertEqual(visible_files(preview,99),[dict(file='real.gif')])
            self.assertEqual((root/file).read_bytes(),b'preserve')

    def test_shared_cache_across_posts_and_restarts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);url='https://example.org/work.png';client=Mock()
            def download(client,url,referer,target):Image.new('RGB',(80,60),'red').save(target)
            with patch('organizer.search_thumbnails.download_preview',side_effect=download) as request:
                cache=PreviewCache(root,client,Mock(),lambda *_:None)
                a=cache.fetch(SimpleNamespace(id=1,url='https://example.org/t/1'),dict(sources=[dict(url=url,kind='image')]))
                b=cache.fetch(SimpleNamespace(id=2,url='https://example.org/t/2'),dict(sources=[dict(url=url,kind='image')]))
                self.assertEqual(request.call_count,1);self.assertEqual(a['files'],b['files'])
                cache=PreviewCache(root,client,Mock(),lambda *_:None)
                cache.fetch(SimpleNamespace(id=3,url='https://example.org/t/3'),dict(sources=[dict(url=url,kind='image')]))
                self.assertEqual(request.call_count,1)

    def test_existing_per_post_file_reused_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);url='https://example.org/work.png'
            name='11-'+hashlib.sha256(url.encode()).hexdigest()[:20]+'.png'
            Image.new('RGB',(80,60),'blue').save(root/name)
            (root/'11.json').write_text(json.dumps(dict(sources=[dict(url=url,kind='image')],files=[dict(file=name)])))
            with patch('organizer.search_thumbnails.download_preview') as request:
                cache=PreviewCache(root,Mock(),Mock(),lambda *_:None)
                result=cache.fetch(SimpleNamespace(id=12,url='https://example.org/t/12'),dict(sources=[dict(url=url,kind='image')]))
                request.assert_not_called();self.assertEqual(result['files'][0]['file'],name)

    def test_original_animation_not_replaced_with_optimized_still(self):
        key='0'*40
        self.assertNotEqual(cache_key(f'https://eroscripts-discourse.eroscripts.com/original/4X/{key}.gif'),
                            cache_key(f'https://eroscripts-discourse.eroscripts.com/optimized/4X/{key}_2_300x200.png'))


if __name__=='__main__':unittest.main()
