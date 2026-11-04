import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from organizer.collector import CollectionOptions, collect, sorted_records, display_date, export_collection, title_filter_keyword, title_matches
from organizer.forum import topic_from_json, TopicUnavailable, Cancelled
from organizer.links import filter_links, urls, compile_custom, DOWNLOAD_TYPES
from organizer.downloads.model import collection_items
from organizer.model import Topic
from organizer.store import Store


def data(identity, author='Alpha'):
    return dict(id=identity, title=f'Topic {identity}', created_at='2026-01-01T00:00:00Z', post_stream={'posts': [
        dict(post_number=1, username=author, cooked='<p>https://gofile.io/d/AbC</p><a href="https://mega.nz/folder/Id#SecretKey">folder</a>'
             '<blockquote><a href="https://gofile.io/d/Quoted">quoted</a></blockquote>'),
        dict(post_number=2, username='Reply', cooked='https://gofile.io/d/Reply')]})


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.store = Store(self.path / 'db.sqlite')
        self.addCleanup(self.store.close)
        self.client = Mock()
        self.client.check = Mock()
        def get(endpoint, params=None):
            if endpoint.startswith('/u/'):
                return {'user': {'username': endpoint.split('/')[2].split('.')[0]}}
            if endpoint == '/user_actions.json':
                return {'user_actions': [] if params['offset'] else [dict(action_type=4, username='Alpha', topic_id=n, post_id=n, title=f'Topic {n}', created_at='2026-01-01T00:00:00Z') for n in (1, 2)]}
            return data(int(endpoint.split('/')[2].split('.')[0]))
        self.client.get.side_effect = get

    def options(self, **kw):
        return CollectionOptions('Alpha', self.path / 'out', **kw)

    def test_first_post_only_preserves_mega_keys(self):
        topic = topic_from_json(data(1))
        links = filter_links(topic.links, ['gofile', 'mega_folder'])
        self.assertEqual(links['gofile'], ['https://gofile.io/d/AbC'])
        self.assertEqual(links['mega_folder'], ['https://mega.nz/folder/Id#SecretKey'])
        self.assertTrue(topic.links_loaded)

    def test_success_and_refilter_reuses_index_and_post_cache(self):
        result = collect(self.options(), self.store, self.client, self.path)
        self.assertEqual((result['posts'], result['unique_links'], result['status']), (2, 1, '采集完成'))
        self.client.get.reset_mock()
        result = collect(self.options(types=['mega_folder']), self.store, self.client, self.path)
        self.client.get.assert_not_called()
        self.assertEqual(result['unique_links'], 1)
        self.assertIn('#SecretKey', (self.path/'out/Alpha_Links_Unique.txt').read_text())

    def test_missing_post_is_failed_not_zero_link_success(self):
        original = self.client.get.side_effect
        self.client.get.side_effect = lambda endpoint, params=None: (_ for _ in ()).throw(TopicUnavailable('404')) if endpoint == '/t/2.json' else original(endpoint, params)
        result = collect(self.options(), self.store, self.client, self.path)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['posts'], 1)
        self.assertIn('部分', result['status'])
        self.client.get.side_effect = original
        result = collect(self.options(), self.store, self.client, self.path)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['posts'], 2)

    def test_limit_not_mislabeled_complete(self):
        result = collect(self.options(limit=1), self.store, self.client, self.path)
        self.assertEqual(result['posts'], 1)
        self.assertIn('部分', result['status'])

    def test_title_filter_runs_before_limit_and_skips_unmatched_requests(self):
        result = collect(self.options(title_filter_enabled=True, title_keyword='topic 1', limit=1), self.store, self.client, self.path)
        calls = [call.args[0] for call in self.client.get.call_args_list]
        self.assertIn('/t/1.json', calls); self.assertNotIn('/t/2.json', calls)
        self.assertEqual(result['posts'],1); self.assertNotIn('部分', result['status'])
        exported = json.loads((self.path/'out/Alpha_Links_Data.json').read_text(encoding='utf-8'))
        self.assertEqual([row['id'] for row in exported['records']],[1])
        self.assertTrue(exported['title_filter_enabled'])

    def test_change_filter_and_disable_reuses_cache_without_leaking_old_records(self):
        collect(self.options(), self.store, self.client, self.path)
        self.client.get.reset_mock()
        for keyword,expected in [('Topic 1',[1]),('Topic 2',[2]),('absent',[])]:
            events=[]
            result=collect(self.options(title_filter_enabled=True,title_keyword=keyword),self.store,self.client,self.path,
                           lambda kind,value:events.append((kind,value)))
            for kind,value in events:
                if kind=='collection': self.assertTrue(all(r['id'] in expected for r in value['records']))
            self.assertEqual(result['posts'],len(expected)); self.assertNotIn('部分',result['status'])
        result=collect(self.options(title_filter_enabled=False,title_keyword='absent'),self.store,self.client,self.path)
        self.assertEqual(result['posts'],2); self.client.get.assert_not_called()

    def test_filter_literal_unicode_and_empty_validation(self):
        keyword=title_filter_keyword(True,'  [ＰＩＴＣＨ] 中文  ')
        self.assertTrue(title_matches('Work [pitch] 中文 extra',keyword))
        self.assertFalse(title_matches('pitch 中文',keyword))
        with self.assertRaises(ValueError):
            collect(self.options(title_filter_enabled=True,title_keyword='  '),self.store,self.client,self.path)
        self.client.get.assert_not_called()

    def test_filter_cancellation_does_not_export_other_cached_titles(self):
        collect(self.options(), self.store, self.client, self.path)
        with patch('organizer.collector.Forum.author',side_effect=Cancelled('stop')):
            with self.assertRaises(Cancelled):
                collect(self.options(title_filter_enabled=True,title_keyword='Topic 1'),self.store,self.client,self.path)
        saved=json.loads((self.path/'out/Alpha_Links_Data.json').read_text(encoding='utf-8'))
        self.assertEqual([r['id'] for r in saved['records']],[1])

    def test_fresh_title_is_checked_again(self):
        original=self.client.get.side_effect
        def get(endpoint, params=None):
            value=original(endpoint,params)
            if endpoint=='/t/1.json': value['title']='Renamed unrelated work'
            return value
        self.client.get.side_effect=get
        result=collect(self.options(title_filter_enabled=True,title_keyword='Topic 1'),self.store,self.client,self.path)
        self.assertEqual(result['posts'],0); self.assertEqual(result['failed'],0)

    def test_previous_failure_outside_filter_does_not_mark_filtered_run_incomplete(self):
        original=self.client.get.side_effect
        self.client.get.side_effect=lambda endpoint,params=None: (_ for _ in ()).throw(TopicUnavailable('404')) if endpoint=='/t/2.json' else original(endpoint,params)
        collect(self.options(),self.store,self.client,self.path)
        self.client.get.reset_mock()
        result=collect(self.options(title_filter_enabled=True,title_keyword='Topic 1'),self.store,self.client,self.path)
        self.assertEqual(result['failed'],0); self.assertNotIn('部分',result['status'])
        self.client.get.assert_not_called()

    def test_stop_before_index_does_not_erase_previous_results(self):
        collect(self.options(), self.store, self.client, self.path)
        with patch('organizer.collector.Forum.author', side_effect=Cancelled('stop')):
            with self.assertRaises(Cancelled):
                collect(self.options(), self.store, self.client, self.path)
        saved = json.loads((self.path/'out/Alpha_Links_Data.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['records']), 2)
        self.assertIn('停止', saved['status'])

    def test_different_author_does_not_reuse_foreign_records(self):
        collect(self.options(), self.store, self.client, self.path)
        original = self.client.get.side_effect
        def get(endpoint, params=None):
            if endpoint == '/user_actions.json':
                return {'user_actions': []}
            return original(endpoint, params)
        self.client.get.side_effect = get
        result = collect(CollectionOptions('Beta', self.path/'out'), self.store, self.client, self.path)
        self.assertEqual(result['posts'], 0)
        self.assertTrue((self.path/'out/Alpha_Links_Data.json').exists())

    def test_legacy_topic_is_read_once_to_populate_links(self):
        self.store.upsert(Topic(1, 'Old', 'Alpha', '', details_loaded=True))
        self.store.author_progress('Alpha', 1, complete=True)
        result = collect(self.options(), self.store, self.client, self.path)
        self.assertEqual(result['unique_links'], 1)
        self.client.get.assert_called_once_with('/t/1.json')

    def test_web_partial_index_remains_partial(self):
        self.store.upsert(topic_from_json(data(1)))
        self.store.author_progress('Alpha', 0, complete=False)
        with patch('organizer.collector.Forum.author', return_value='Alpha'):
            result = collect(self.options(), self.store, self.client, self.path)
        self.assertIn('部分', result['status'])

    def test_sort_unknown_dates_last_and_no_timestamp_guess(self):
        rows = [dict(id=1, date=''), dict(id=2, date='2025-01-01T00:00:00Z'), dict(id=3, date='2026-01-01T00:00:00+08:00')]
        self.assertEqual([r['id'] for r in sorted_records(rows, 'desc')], [3, 2, 1])
        self.assertEqual([r['id'] for r in sorted_records(rows, 'asc')], [2, 3, 1])

    def test_link_types_custom_and_query_are_lossless(self):
        values = urls('https://drive.google.com/file/d/Id/view?usp=share&amp;k=A https://mega.nz/#!Abc!Key https://1fichier.com/?CdE')
        result = filter_links(values, ['google_drive', 'mega_file', '1fichier'], compile_custom(r'mega\.nz'))
        self.assertEqual(result['google_drive'], ['https://drive.google.com/file/d/Id/view?usp=share&k=A'])
        self.assertEqual(result['mega_file'], ['https://mega.nz/#!Abc!Key'])
        self.assertEqual(result['custom'], result['mega_file'])

    def test_invalid_custom_pattern_reports_setting_error(self):
        with self.assertRaises(ValueError):
            compile_custom('[')

    def test_all_download_sources_extracted_from_first_post_and_cached_refilter(self):
        samples = ['https://hanime1.me/watch?v=123&quality=high',
                   'https://www.iwara.tv/video/Work/title?lang=ja',
                   'https://rule34video.com/video/123/example/',
                   'https://pixeldrain.com/u/Resource',
                   'https://mega.nz/file/File#FullSecret']
        topic_data = data(1)
        topic_data['post_stream']['posts'][0]['cooked'] += ''.join(f'<a href="{url}">Resource</a>' for url in samples)
        topic_data['post_stream']['posts'][1]['cooked'] += '<a href="https://iwara.tv/video/Reply">Reply</a>'
        self.store.upsert(topic_from_json(topic_data))
        self.store.author_progress('Alpha', 1, complete=True)
        result = collect(self.options(types=list(DOWNLOAD_TYPES)), self.store, self.client, self.path)
        self.client.get.assert_not_called()
        self.assertEqual(result['unique_links'], 7)
        exported = json.loads((self.path/'out/Alpha_Links_Data.json').read_text(encoding='utf-8'))
        items, ignored = collection_items(exported['records'])
        self.assertEqual(ignored, 0)
        self.assertEqual({x.platform for x in items}, {'Hanime1', 'Iwara', 'Rule34Video', 'GoFile', 'Pixeldrain', 'MEGA'})
        self.assertEqual(len(items), 7)
        self.assertTrue(all(x.sources[0]['author'] == 'Alpha' for x in items))
        self.assertFalse(any('Reply' in x.url or 'Quoted' in x.url for x in items))

    def test_video_link_classification_rejects_other_pages_and_fake_hosts(self):
        result = filter_links(['https://iwara.tv/users/Example', 'https://iwara.tv.evil.invalid/video/Bad',
                               'https://hanime1.me/', 'https://rule34video.com/members/123/'], list(DOWNLOAD_TYPES))
        self.assertFalse(any(result.values()))

    def test_damaged_checkpoint_recovers_from_catalog(self):
        collect(self.options(), self.store, self.client, self.path)
        next((self.path/'collections').glob('*.json')).write_text('{broken', encoding='utf-8')
        self.client.get.reset_mock()
        result = collect(self.options(), self.store, self.client, self.path)
        self.assertEqual(result['posts'], 2)
        self.client.get.assert_not_called()

    def test_cancel_refresh_preserves_previously_exported_posts(self):
        collect(self.options(), self.store, self.client, self.path)
        with patch('organizer.collector.Forum.topic', side_effect=Cancelled('stop')):
            with self.assertRaises(Cancelled):
                collect(self.options(refresh=True), self.store, self.client, self.path)
        saved = json.loads((self.path/'out/Alpha_Links_Data.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['records']), 2)
        self.assertIn('停止', saved['status'])

    def test_display_date_has_explicit_timezone_and_unknown(self):
        self.assertEqual(display_date('2026-01-01T00:00:00Z'), '2026-01-01 08:00:00 +08:00')
        self.assertEqual(display_date(''), '未知')

    def test_domain_exports_group_hosts_preserve_urls_and_remove_only_own_stale_files(self):
        state = dict(author='Alpha', status='采集完成', failed={}, records={'1':dict(
            id=1, title='Work', date='', url='https://discuss.eroscripts.com/t/1', links={
                'iwara':['https://www.iwara.tv/video/A?lang=ja', 'https://iwara.tv/video/B'],
                'mega_file':['https://mega.nz/file/A#FullKey'],
                'custom':['https://www.iwara.tv/video/A?lang=ja', 'https://cdn.example.com/file?k=A']})})
        result = export_collection(state, self.path/'out', 'desc')
        folder = Path(result['domain_folder'])
        self.assertEqual(result['domains'], 3)
        self.assertEqual({p.name for p in folder.iterdir()}, {'iwara.tv.txt', 'mega.nz.txt', 'cdn.example.com.txt'})
        self.assertEqual((folder/'iwara.tv.txt').read_text().splitlines(),
                         ['https://www.iwara.tv/video/A?lang=ja', 'https://iwara.tv/video/B'])
        self.assertEqual((folder/'mega.nz.txt').read_text(), 'https://mega.nz/file/A#FullKey\n')
        (folder/'notes.txt').write_text('User notes', encoding='utf-8')
        other = export_collection(dict(state, author='Beta'), self.path/'out', 'desc')
        state['records']['1']['links'] = {'mega_file':['https://mega.nz/file/B#OtherKey']}
        export_collection(state, self.path/'out', 'desc')
        self.assertFalse((folder/'iwara.tv.txt').exists())
        self.assertFalse((folder/'cdn.example.com.txt').exists())
        self.assertEqual((folder/'notes.txt').read_text(), 'User notes')
        self.assertTrue((Path(other['domain_folder'])/'iwara.tv.txt').exists())
        state['records'] = {}
        export_collection(state, self.path/'out', 'desc')
        self.assertFalse((folder/'mega.nz.txt').exists())
        self.assertTrue((folder/'notes.txt').exists())

    def test_stale_export_metadata_cannot_remove_files_outside_domain_folder(self):
        output = self.path/'out'
        output.mkdir()
        (output/'keep.txt').write_text('Keep', encoding='utf-8')
        (output/'Alpha_Links_Data.json').write_text(json.dumps(dict(
            domain_folder='Alpha_Links_按域名', domain_files=['../keep.txt', '..\\keep.txt', 'bad.json'])), encoding='utf-8')
        export_collection(dict(author='Alpha', status='采集完成', failed={}, records={}), output, 'desc')
        self.assertEqual((output/'keep.txt').read_text(), 'Keep')


if __name__ == '__main__':
    unittest.main()
