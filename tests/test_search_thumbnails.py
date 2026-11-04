import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from organizer.search_thumbnails import extract_previews, cache_path
from organizer.search_scraper import ScrapeOptions, scrape, remove_posts, export_report
from tests.test_search_scraper import Client, topic, page
from organizer.store import Store
from organizer.forum import Cancelled


class PreviewTests(unittest.TestCase):
    def test_only_embedded_media_not_resources_or_avatars(self):
        rows=extract_previews('''<img src="/uploads/a.png"><a href="/uploads/anim.gif"><img src="/uploads/still.jpg"></a>
            <video poster="/uploads/poster.png"><source src="/uploads/clip.webm"></video>
            <a href="https://example.org/full.mp4">video download</a><img class="emoji" src="/uploads/emoji.png">
            <img width="16" src="/uploads/icon.png"><blockquote><img src="/quoted.png"></blockquote>
            <img data-src="//cdn.example.org/b.webp"><img src="file:///C:/secret.png">''','https://discuss.eroscripts.com/t/1')
        urls=[r['url'] for r in rows]
        self.assertIn('https://discuss.eroscripts.com/uploads/anim.gif',urls)
        self.assertIn('https://discuss.eroscripts.com/uploads/clip.webm',urls)
        self.assertEqual(rows[0]['kind'],'animation')
        self.assertFalse(any(x in ' '.join(urls) for x in ('full.mp4','emoji','icon','quoted','secret')))
        self.assertEqual(len(rows),6)

    def test_cache_paths_are_confined(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'x.png').write_bytes(b'x')
            self.assertEqual(cache_path(root,'x.png'),(root/'x.png').resolve())
            self.assertIsNone(cache_path(root,'../x.png'))
            self.assertIsNone(cache_path(root,'https://example.org/x.png'))

    def test_switch_off_never_constructs_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'catalog.db')
            try:
                with patch('organizer.search_scraper.category_map',return_value={}),patch('organizer.search_scraper.Forum.topic',return_value=topic()),patch('organizer.search_thumbnails.PreviewCache') as cache:
                    result=scrape(ScrapeOptions(root/'out','mmd'),store,Client([page([1])],{}),root)
                    self.assertEqual(len(result['records']),1);cache.assert_not_called()
            finally:store.close()

    def test_stop_during_preview_keeps_confirmed_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'catalog.db')
            try:
                with patch('organizer.search_scraper.category_map',return_value={}),patch('organizer.search_thumbnails.PreviewCache') as cache:
                    cache.return_value.topic.return_value=(topic(),{'sources':[]})
                    cache.return_value.fetch.side_effect=Cancelled()
                    with self.assertRaises(Cancelled):
                        scrape(ScrapeOptions(root/'out','mmd',thumbnails=True,thumbnail_dir=root/'cache'),store,Client([page([1])],{}),root)
                    saved=json.loads(next((root/'search-scrapes').glob('*.json')).read_text(encoding='utf-8'))
                    self.assertEqual(set(saved['records']),{'1'})
                    self.assertEqual(saved['pending'][0]['id'],1)
            finally:store.close()

    def test_delete_persists_counts_export_and_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=Store(root/'catalog.db')
            try:
                with patch('organizer.search_scraper.category_map',return_value={}),patch('organizer.search_scraper.Forum.topic',side_effect=lambda i,**kw:topic(i)) as reader:
                    options=ScrapeOptions(root/'out','mmd');client=Client([page([1,2])],{})
                    result=scrape(options,store,client,root)
                    result=remove_posts(result,['1'],root)
                    report=export_report(result,result['report_folder'])
                    self.assertEqual(report['posts'],1)
                    data=json.loads((Path(result['report_folder'])/'搜索刮削报告.json').read_text(encoding='utf-8'))
                    self.assertEqual(data['ranking'][0]['count'],1);self.assertNotIn('1',data['records'])
                    for refresh in (False,True):
                        options.refresh=refresh;reader.reset_mock()
                        resumed=scrape(options,store,client,root)
                        self.assertEqual(set(resumed['records']),{'2'})
                        self.assertNotIn(1,[c.args[0] for c in reader.call_args_list])
            finally:store.close()


if __name__=='__main__':unittest.main()
