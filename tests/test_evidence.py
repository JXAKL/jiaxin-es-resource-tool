import json
import tempfile
import unittest
from pathlib import Path

from organizer.engine import analyse, search_ids
from organizer.forum import Forum, topic_from_json
from organizer.model import FileGroup, Topic, TopicIndex, match_group
from organizer.names import fragments, group_fragments, search_plan
from organizer.store import Store


def post(identity=1, title='Moonlight Dance 01', author='Alpha', body=None):
    return topic_from_json({'id': identity, 'title': title, 'post_stream': {'posts': [
        {'post_number': 1, 'username': author, 'cooked': body if body is not None else f'<p>Original title: {title}</p>'}]}})


class EvidenceTests(unittest.TestCase):
    def test_mixed_language_name_is_split_into_independent_queries(self):
        name = 'Iwara - [Blue Archive] NO.4 Kisaki キサキ Time after school 放課後のタイム [mraT9qCLMmnDyn] [Source]'
        pieces = fragments(name)
        for expected in ('blue archive', 'kisaki', 'キサキ', 'time after school', '放課後のタイム'):
            self.assertIn(expected, pieces)
        self.assertLessEqual(len(pieces), 12)
        self.assertTrue(all(len(piece.split()) <= 3 for piece in pieces))
        self.assertNotIn('source', pieces)

    def test_long_plain_title_has_short_phrases_and_typo_independent_queries(self):
        pieces = fragments('Midnight Skyline Moonlight Dnace Final Edition')
        self.assertTrue(all(len(piece.split()) <= 3 for piece in pieces))
        self.assertIn('moonlight', pieces)
        self.assertTrue(any('dnace' not in piece for piece in pieces))

    def test_typo_can_pass_with_title_and_post_content_evidence(self):
        result = match_group(FileGroup('x', 'Moonlight Dnace 01'), [post()])
        self.assertTrue(result.selected)
        self.assertGreaterEqual(result.candidates[0].score, .85)
        self.assertGreater(result.candidates[0].body_score, .8)
        self.assertTrue(result.candidates[0].evidence)

    def test_threshold_changes_confirmation(self):
        group = FileGroup('x', 'Moonlight Dnace 01')
        self.assertTrue(match_group(group, [post()], .85).selected)
        self.assertFalse(match_group(group, [post()], .95).selected)

    def test_title_only_cached_record_requires_first_post(self):
        summary = Topic(1, 'Moonlight Dance 01', 'Alpha', 'https://discuss.eroscripts.com/t/1')
        result = match_group(FileGroup('x', 'Moonlight Dance 01'), [summary])
        self.assertFalse(result.selected)
        self.assertFalse(result.candidates[0].verified)

    def test_full_original_name_in_body_can_resolve_a_different_topic_title(self):
        title = 'Harbor Lights Midnight Edition'
        result = match_group(FileGroup('x', title), [post(title='New release', body=f'<p>Original video: {title}</p>')])
        self.assertTrue(result.selected)
        self.assertGreaterEqual(result.candidates[0].score, .97)

    def test_attachment_can_resolve_differently_titled_post(self):
        result = match_group(FileGroup('x', 'Harbor Lights Midnight'), [post(title='A new release',
            body='<a href="/uploads/Harbor%20Lights%20Midnight.funscript">Download script</a>')])
        self.assertTrue(result.selected)
        self.assertGreaterEqual(result.candidates[0].attachment_score, .97)

    def test_one_common_fragment_does_not_confirm_author(self):
        group = FileGroup('x', '[Blue Archive] Kisaki Time after school')
        candidate = post(title='Blue Archive', body='<p>General discussion about Blue Archive characters.</p>')
        self.assertFalse(match_group(group, [candidate]).selected)

    def test_number_conflict_blocks_even_strong_body_match(self):
        candidate = post(title='Moonlight Dance 02', body='<p>Moonlight Dance 01</p>')
        result = match_group(FileGroup('x', 'Moonlight Dance 01'), [candidate], .70)
        self.assertFalse(result.selected)
        self.assertTrue(result.candidates[0].conflict)

    def test_quotes_and_replies_cannot_supply_evidence(self):
        data = {'id': 1, 'title': 'Unrelated release', 'post_stream': {'posts': [
            {'post_number': 1, 'username': 'Alpha', 'cooked': '<aside class="quote"><blockquote>'
             '<a href="/uploads/Moonlight%20Dance%2001.funscript">Moonlight Dance 01.funscript</a>'
             '</blockquote></aside><p>My own unrelated work.</p>'},
            {'post_number': 2, 'username': 'Beta', 'cooked': '<p>Moonlight Dance 01</p>'}]}}
        topic = topic_from_json(data)
        self.assertEqual(topic.author, 'Alpha')
        self.assertNotIn('Moonlight', topic.body)
        self.assertFalse(topic.filenames)
        self.assertFalse(match_group(FileGroup('x', 'Moonlight Dance 01'), [topic]).selected)

    def test_equal_scores_from_two_authors_remain_ambiguous(self):
        result = match_group(FileGroup('x', 'Moonlight Dnace 01'), [post(), post(2, author='Beta')])
        self.assertEqual(result.status, '多作者待核对')

    def test_media_identifier_matches_case_sensitively(self):
        group = FileGroup('x', 'Renamed [mraT9qCLMmnDyn]')
        candidate = post(title='New release', body='<a href="https://example.test/video/mraT9qCLMmnDyn">Video</a>')
        self.assertTrue(match_group(group, TopicIndex([candidate])).selected)
        candidate.source_ids = ['mrat9qclmmndyn']
        self.assertFalse(match_group(group, [candidate]).selected)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'cache.db')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_every_fragment_is_queried_and_hits_are_merged_without_title_cutoff(self):
        class Search:
            calls = []
            def search(self, query, **kwargs):
                self.calls.append(query)
                return [{'id': 1, 'title': 'New release'}]
        search = Search()
        group = FileGroup('x', '[Harbor Lights] Midnight Edition')
        ids = search_ids(search, group, lambda *_: None)
        self.assertEqual(search.calls, search_plan(group))
        self.assertEqual(ids, [1])
        self.assertEqual(set(group.search_hits['1']), set(search.calls))

    def test_old_summary_cache_is_hydrated(self):
        self.store.upsert(Topic(1, 'Moonlight Dance 01', 'Alpha', 'https://discuss.eroscripts.com/t/1'))
        class Client:
            calls = []
            def get(self, endpoint, params=None):
                self.calls.append(endpoint)
                return {'id': 1, 'title': 'Moonlight Dance 01', 'post_stream': {'posts': [
                    {'post_number': 1, 'username': 'Alpha', 'cooked': '<p>Moonlight Dance 01</p>'}]}}
        client = Client()
        loaded = Forum(client, self.store).topic(1)
        self.assertTrue(loaded.details_loaded)
        self.assertEqual(client.calls, ['/t/1.json'])
        self.assertTrue(self.store.topic(1).body)

    def test_below_threshold_does_not_crawl_author(self):
        class Client:
            def get(self, endpoint, params=None):
                if endpoint == '/search.json':
                    return {'topics': [{'id': 1, 'title': 'Moonlight Dance 01'}]}
                if endpoint == '/t/1.json':
                    return {'id': 1, 'title': 'Moonlight Dance 01', 'post_stream': {'posts': [
                        {'post_number': 1, 'username': 'Alpha', 'cooked': '<p>Unrelated text.</p>'}]}}
                raise AssertionError('Below-threshold candidate must not trigger author discovery: ' + endpoint)
        # A verified exact title is now independent evidence. Use a genuinely
        # incomplete local name to retain this pipeline threshold safeguard.
        result = analyse([FileGroup('x', 'Moonlight Dance 01 Missing Edition')], self.store, Client())
        self.assertFalse(result[0].selected)
        self.assertIsNone(self.store.author('Alpha'))

    def test_scoring_details_survive_export_import(self):
        self.store.upsert(post())
        folder = Path(self.temp.name) / 'export'
        self.store.export(folder)
        other = Store(Path(self.temp.name) / 'second.db')
        try:
            other.import_file(folder / '作者帖子索引.json')
            restored = other.topic(1)
            self.assertTrue(restored.details_loaded)
            self.assertEqual(restored.body, self.store.topic(1).body)
            self.assertTrue(match_group(FileGroup('x', 'Moonlight Dance 01'), [restored]).selected)
        finally:
            other.close()
