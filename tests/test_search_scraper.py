import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer.search_scraper import ScrapeOptions, video_creators, inspect_topic, scrape, author_counts, parse_tags
from organizer.forum import topic_from_json, Cancelled, SearchResponseError, TopicUnavailable
from organizer.store import Store


def topic(identity=1, title='(Kaotaro12) Milked by Mutsuki (Requested, Soft & Hard Scripts)', author='ESPoster',
          category='Free Scripts', tags=None, body=''):
    return topic_from_json(dict(id=identity, title=title, category_name=category, tags=tags or ['mmd'],
        post_stream=dict(posts=[dict(post_number=1, username=author, cooked=body or
             '<p><a href="https://gofile.io/d/Test">video and script</a><a href="https://patreon.com/Test">Support</a><img src="https://example.org/p.png"></p>')])) )


class Client:
    def __init__(self, pages, topics):
        self.pages, self.topics, self.calls, self.stop = pages, topics, [], False
    def check(self):
        if self.stop: raise Cancelled()
    def get(self, endpoint, params=None):
        self.check(); self.calls.append((endpoint, params))
        if endpoint == '/search.json':
            value = self.pages[params['page']-1]
            if isinstance(value, Exception): raise value
            return value
        raise AssertionError(endpoint)


def page(ids, more=False):
    return dict(topics=[dict(id=i, title=topic(i).title) for i in ids], grouped_search_result=dict(more_full_page_results=more))


class SearchScraperTests(unittest.TestCase):
    def test_multiple_required_tags_are_all_not_any(self):
        self.assertEqual(parse_tags('MMD, #vr，mmd, ,https://discuss.eroscripts.com/tag/VR'), ['mmd', 'vr'])
        for tags in (['mmd'], ['vr'], ['mmd-vr'], []):
            self.assertIsNone(inspect_topic(topic(tags=tags), 'mmd,vr', '', {})[0], tags)
        self.assertIsNotNone(inspect_topic(topic(tags=['MMD','VR','extra']), 'mmd,vr', '', {})[0])
        self.assertIsNone(inspect_topic(topic(tags=['mmd','vr']), 'mmd,vr', 'Other', {})[0])

    def test_exclusions_override_all_tags_and_paid_switch(self):
        for paid in (False, True):
            row, why=inspect_topic(topic(tags=['mmd','vr','blocked']), 'mmd,vr', 'Kaotaro12', {}, 'other,blocked', paid)
            self.assertIsNone(row);self.assertIn('blocked',why)
        self.assertIsNotNone(inspect_topic(topic(tags=['mmd','vr','blocked-extra']), 'mmd,vr', '', {}, 'blocked')[0])
        self.assertIsNone(inspect_topic(topic(tags=['mmd']), 'mmd', '', {}, 'mmd', True)[0])

    def test_paid_switch_and_unknown_labels(self):
        for category in ('Paid Scripts', 'General', 'Free Scripts'):
            row,_=inspect_topic(topic(category=category), 'mmd', '', {}, include_paid=True)
            self.assertIsNotNone(row)
        self.assertEqual(inspect_topic(topic(category='Paid Scripts'), '', '', {}, include_paid=True)[0]['payment']['script'], '付费')
        self.assertIsNone(inspect_topic(topic(category='Paid Scripts'), '', '', {})[0])

    def test_complete_pipeline_filters_before_count_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            topics={1:topic(1,tags=['mmd','vr']),2:topic(2,tags=['mmd','vr'],category='Paid Scripts'),
                    3:topic(3,tags=['mmd','vr','blocked'],author='ExcludedAuthor',body='<p>EXCLUDED_CONTENT <a href="https://gofile.io/d/EXCLUDED_LINK">script</a></p>'),
                    4:topic(4,tags=['mmd'],author='OnlyOneTag')}
            client=Client([page([1,2,3,4])],topics)
            with patch('organizer.search_scraper.category_map',return_value={}),patch('organizer.search_scraper.Forum.topic',side_effect=lambda i,**kw: topics[i]) as reader:
                store=Store(root/'catalog.db')
                try:
                    options=ScrapeOptions(root/'out','vr,mmd',exclude_tags='blocked,other',include_paid=True)
                    state=scrape(options,store,client,root)
                    self.assertEqual(set(state['records']),{'1','2'})
                    self.assertEqual(author_counts(state['records']),[dict(author='ESPoster',count=2)])
                    self.assertIn(3,[call.args[0] for call in reader.call_args_list])
                    report=next((root/'out').glob('*/搜索刮削报告.json'))
                    exported=report.read_text(encoding='utf-8')
                    for forbidden in ('EXCLUDED_CONTENT','EXCLUDED_LINK','ExcludedAuthor'):
                        self.assertNotIn(forbidden,exported)
                    self.assertNotIn('title',state['excluded']['3'])
                    options.include_paid=False
                    free=scrape(options,store,client,root)
                    self.assertEqual(set(free['records']),{'1'})
                    options.include_paid=True;options.exclude_tags=''
                    no_exclusion=scrape(options,store,client,root)
                    self.assertEqual(set(no_exclusion['records']),{'1','2','3'})
                    self.assertEqual(len(list((root/'out').glob('*/搜索刮削报告.json'))),3)
                finally:store.close()

    def test_creator_is_leading_video_credit_not_es_author(self):
        row, why = inspect_topic(topic(), '', 'kaotaro12', {})
        self.assertEqual(row['author'], 'ESPoster'); self.assertEqual(row['video_creators'], ['Kaotaro12'])
        self.assertIsNone(inspect_topic(topic(author='Kaotaro12', title='Other work (Kaotaro12)'), '', 'Kaotaro12', {})[0])
        for title in ['[Kaotaro12] Work', '（Kaotaro12） Work', '【Kaotaro12】 Work', '[Free][by Kaotaro12] Work', '(Other & Kaotaro12) Work']:
            self.assertIsNotNone(inspect_topic(topic(title=title), '', 'Kaotaro12', {})[0], title)
        self.assertIsNone(inspect_topic(topic(title='(Kaotaro123) Work'), '', 'Kaotaro12', {})[0])

    def test_tag_and_creator_intersection(self):
        self.assertIsNotNone(inspect_topic(topic(), 'mmd', '', {})[0])
        self.assertIsNotNone(inspect_topic(topic(), 'MMD', 'Kaotaro12', {})[0])
        self.assertIsNone(inspect_topic(topic(), 'vr', 'Kaotaro12', {})[0])
        self.assertIsNone(inspect_topic(topic(), 'mmd', 'Other', {})[0])

    def test_payment_and_filtered_links(self):
        for category in ['Paid Scripts', '', 'General']:
            self.assertIsNone(inspect_topic(topic(category=category), '', '', {})[0])
        self.assertIsNone(inspect_topic(topic(body='<p>Paid script.</p>'), '', '', {})[0])
        row, _ = inspect_topic(topic(body='<p>Video is paid, script is free.</p>'), '', '', {})
        self.assertEqual(row['payment']['video'], '付费')
        row, _ = inspect_topic(topic(), '', '', {})
        self.assertEqual(row['links']['resources'], ['https://gofile.io/d/Test'])
        self.assertIsNotNone(inspect_topic(topic(category='', body='<p>Free script.</p>'), '', '', {})[0])

    def test_input_validation(self):
        self.assertEqual(ScrapeOptions(Path('.'), 'https://discuss.eroscripts.com/tag/mmd', '(Kaotaro12)').filters(), ('mmd', 'Kaotaro12'))
        for tag, creator in [('', ''), ('mmd category:paid', ''), ('', '@ESAuthor'), ('', 'x" status:open'), ('https://example.com/tag/mmd', '')]:
            with self.assertRaises(ValueError): ScrapeOptions(Path('.'), tag, creator).filters()

    def run_job(self, root, client, **kwargs):
        with patch('organizer.search_scraper.category_map', return_value={}), \
             patch('organizer.search_scraper.Forum.topic', side_effect=lambda i, **kw: self.read_topic(client, i)):
            store = Store(root / 'catalog.db')
            try: return scrape(ScrapeOptions(root/'out', 'mmd', 'Kaotaro12', **kwargs), store, client, root)
            finally: store.close()

    def read_topic(self, client, identity):
        client.check()
        value = client.topics[identity]
        if callable(value): return value()
        if isinstance(value, Exception): raise value
        return value

    def test_pages_dedup_counts_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = Client([page([1,2], True), page([2,3,4])], {1:topic(1), 2:topic(2), 3:topic(3,author='Other'), 4:topic(4,category='Paid Scripts')})
            state = self.run_job(root, client)
            self.assertEqual(author_counts(state['records']), [dict(author='ESPoster',count=2),dict(author='Other',count=1)])
            self.assertTrue(state['list_complete']); self.assertEqual(state['discovered'], 4)
            report = next((root/'out').glob('*/搜索刮削报告.json'))
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(data['ranking'][0]['count'],2)
            txt = (report.parent/'按域名链接/gofile.io.txt').read_text(encoding='utf-8')
            self.assertTrue(txt.startswith('# 搜索刮削')); self.assertEqual(txt.count('https://gofile.io/d/Test'),1)
            self.assertFalse((report.parent/'按域名链接/patreon.com.txt').exists())

    def test_limit_resume_mid_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); client=Client([page([1,2],True),page([3])], {i:topic(i) for i in (1,2,3)})
            state = self.run_job(root, client, limit=1)
            self.assertEqual(list(state['records']), ['1']); self.assertFalse(state['list_complete'])
            state = self.run_job(root, client)
            self.assertEqual(len(state['records']),3)
            self.assertEqual([c[1]['page'] for c in client.calls], [1,2])

    def test_stop_preserves_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); client=Client([page([1,2])],{1:topic(1),2:Cancelled()})
            with self.assertRaises(Cancelled): self.run_job(root,client)
            saved=json.loads(next((root/'search-scrapes').glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(list(saved['records']),['1']); self.assertEqual(saved['pending'][0]['id'],2)
            client.topics[2]=topic(2)
            self.assertEqual(len(self.run_job(root,client)['records']),2)

    def test_page_error_and_repeated_page_not_complete(self):
        for second in [SearchResponseError('broken'), page([1],True)]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);client=Client([page([1],True),second],{1:topic(1)})
                with self.assertRaises(SearchResponseError): self.run_job(root,client)
                saved=json.loads(next((root/'search-scrapes').glob('*.json')).read_text(encoding='utf-8'))
                self.assertFalse(saved['list_complete']);self.assertEqual(len(saved['records']),1)

    def test_unavailable_retry_and_unknown_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=Client([page([1,2])],{1:TopicUnavailable('missing'),2:topic(2,category='')})
            state=self.run_job(root,client)
            self.assertEqual(len(state['failed']),1);self.assertEqual(len(state['excluded']),1);self.assertFalse(state['records'])
            client.topics[1]=topic(1)
            state=self.run_job(root,client)
            self.assertEqual(len(state['records']),1);self.assertFalse(state['failed'])


if __name__ == '__main__': unittest.main()
