import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer.downloads.model import Item, load_queue, normalize_url, parse_items, save_queue, collection_items
from organizer.downloads.service import DownloadService


class DownloadQueueTests(unittest.TestCase):
    def test_mixed_links_deduplicate_and_preserve_mega_key(self):
        rows = parse_items('https://www.iwara.tv/video/fixture\nhttps://iwara.tv/video/fixture\n'
                           'https://mega.nz/file/fixture#secret\nhttps://gofile.io/d/Fixture')
        self.assertEqual([r.platform for r in rows], ['Iwara', 'MEGA', 'GoFile'])
        self.assertTrue(rows[1].url.endswith('#secret'))
        self.assertIsNone(normalize_url('https://gofile.io.evil.invalid/d/Fixture'))
        self.assertIsNone(normalize_url('https://user:password@gofile.io/d/Fixture'))
        self.assertEqual(normalize_url('https://mega.io/folder/Test#key/file/child'),'https://mega.nz/folder/Test#key/file/child')
        self.assertEqual(normalize_url('https://mega.co.nz/#F!Test!key'),'https://mega.nz/#F!Test!key')

    def test_queue_restores_interrupted_without_repeating_uncertain_mega(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'queue.json'
            save_queue(target, [Item('https://gofile.io/d/Test', status='下载中'),
                                Item('https://mega.nz/file/Test#key', status='需核对')], {'interval':2})
            rows, options = load_queue(target)
            self.assertEqual([r.status for r in rows], ['待继续', '需核对'])
            self.assertEqual(options['interval'], 2)

    def test_website_wait_restores_as_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'queue.json'
            save_queue(target, [Item('https://pixeldrain.com/u/Test', status='等待官网下载'),
                                Item('https://iwara.tv/video/Test', status='等待网站验证')], {})
            rows, _ = load_queue(target)
            self.assertEqual([r.status for r in rows], ['待继续', '待继续'])

    def test_stop_before_worker_start_never_launches_browser(self):
        service = DownloadService(lambda event: None)
        service.stop()
        with patch('organizer.downloads.service.runtime_paths') as paths:
            service.run([], {})
            paths.assert_not_called()

    def test_collected_resources_merge_sources_and_preserve_title_keys(self):
        rows = [dict(id=1, author='Alpha', title='First', url='https://discuss.eroscripts.com/t/1', date='',
                     links={'iwara':['https://www.iwara.tv/video/Test'],
                            'mega_file':['https://mega.nz/file/Test#FullKey'],
                            'custom':['https://iwara.tv/video/Test', 'https://mediafire.com/file/Test']}),
                dict(id=2, author='Beta', title='Second', url='https://discuss.eroscripts.com/t/2', date='',
                     links={'iwara':['https://iwara.tv/video/Test']})]
        items, ignored = collection_items(rows)
        self.assertEqual(len(items), 2)
        self.assertEqual(ignored, 1)
        self.assertEqual(items[0].title, 'First')
        self.assertEqual([x['author'] for x in items[0].sources], ['Alpha', 'Beta'])
        self.assertEqual(items[1].url, 'https://mega.nz/file/Test#FullKey')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'queue.json'
            items[0].status, items[0].detail = '完成', 'Verified'
            save_queue(target, items, {})
            restored, _ = load_queue(target)
            self.assertEqual(restored, items)

    def test_damaged_collection_json_is_reported_without_guessing_links(self):
        for records in ([{}], [dict(links={'iwara':'https://iwara.tv/video/Test'})],
                        [dict(links={'iwara':[42]})]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                collection_items(records)
