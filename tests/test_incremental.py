import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from organizer.engine import analyse
from organizer.evidence import body_similarity, coverage, lexical_bound
from organizer.forum import Cancelled
from organizer.matching import MatchingSession, configured_workers
from organizer.model import FileGroup, Topic, TopicIndex, similarity, title_core
from organizer.store import Store


def topic(identity=1, author='Alpha', body='Moonlight Dance 01'):
    return Topic(identity, 'Moonlight Dance 01', author, f'https://discuss.eroscripts.com/t/{identity}',
                 body=body, details_loaded=True)


class IncrementalTests(unittest.TestCase):
    def test_worker_setting_auto_manual_and_bounds(self):
        with patch('organizer.matching.os.cpu_count', return_value=16):
            self.assertEqual(configured_workers('0'), 8)
            self.assertEqual(configured_workers('12'), 12)
            for invalid in ('17', '-1', 'nan'):
                with self.assertRaises(ValueError):
                    configured_workers(invalid)
        with patch('organizer.matching.os.cpu_count', return_value=1):
            self.assertEqual(configured_workers('0'), 1)

    def test_shortlist_cutoff_is_stable_for_equal_retrieval_scores(self):
        topics = [Topic(i, 'Shared phrase', str(i), '') for i in range(1, 151)]
        index = TopicIndex(topics)
        # Exact matches remain untruncated; this query exercises the ranked cutoff.
        group = FileGroup('cutoff', 'Shared phrase extra')
        self.assertEqual([t.id for t in index.shortlist(group)], list(range(1, 33)))
        reverse = TopicIndex(list(reversed(topics)))
        self.assertEqual(index.shortlist(group), reverse.shortlist(group))

    def setUp(self):
        self.groups = [FileGroup('a', 'Moonlight Dnace 01'), FileGroup('b', 'Harbor Lights Midnight')]
        self.matcher = MatchingSession()
        self.matcher.update([topic()])

    def test_unchanged_batch_and_threshold_do_not_repeat_pair_scoring(self):
        result = self.matcher.refresh(self.groups)
        scored = self.matcher.scored_pairs
        self.assertTrue(result[0].selected)
        self.matcher.refresh(self.groups)
        strict = self.matcher.refresh(self.groups, threshold=.95)
        self.assertEqual(self.matcher.scored_pairs, scored)
        self.assertFalse(strict[0].selected)

    def test_new_author_can_revoke_a_previous_match_without_rescoring_old_posts(self):
        self.matcher.refresh(self.groups)
        scored = self.matcher.scored_pairs
        self.matcher.update([topic(), topic(2, 'Beta')])
        result = self.matcher.refresh(self.groups)
        self.assertEqual(result[0].status, '多作者待核对')
        self.assertEqual(self.matcher.scored_pairs - scored, len(self.groups))
        self.matcher.update([topic()])
        scored = self.matcher.scored_pairs
        self.assertTrue(self.matcher.refresh(self.groups)[0].selected)
        self.assertEqual(self.matcher.scored_pairs, scored)

    def test_changed_body_invalidates_only_its_pairs(self):
        second = replace(topic(2), title='Harbor Lights Midnight', body='Harbor Lights Midnight')
        self.matcher.update([topic(), second])
        self.matcher.refresh(self.groups)
        scored = self.matcher.scored_pairs
        self.matcher.update([topic(body='Unrelated notes'), second])
        result = self.matcher.refresh(self.groups)
        self.assertFalse(result[0].selected)
        self.assertTrue(result[1].selected)
        self.assertEqual(self.matcher.scored_pairs - scored, len(self.groups))

    def test_rescan_uses_current_group_and_changed_metadata_gets_new_evidence(self):
        self.matcher.refresh(self.groups)
        new = replace(self.groups[0], hints=['Harbor Lights Midnight'])
        self.assertIs(self.matcher.match(new).group, new)
        self.assertIn(self.matcher.key(new), self.matcher.cache)

    def test_store_read_and_rebuild_only_after_real_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'cache.db')
            try:
                store.upsert(topic())
                with patch.object(store, 'topics', wraps=store.topics) as read:
                    self.matcher.sync(store)
                    self.matcher.sync(store)
                    store.upsert(topic())
                    self.matcher.sync(store)
                    self.assertEqual(read.call_count, 1)
                    store.upsert(topic(body='Changed content'))
                    self.assertTrue(self.matcher.sync(store))
                    self.assertEqual(read.call_count, 2)
            finally:
                store.close()

    def test_offline_preview_is_reused_by_online_stage(self):
        class Client:
            def check(self):
                pass
            def get(self, endpoint, params=None):
                if endpoint.startswith('/u/'):
                    return {'user': {'username': 'Alpha'}}
                if endpoint == '/user_actions.json':
                    return {'user_actions': []}
                raise AssertionError(endpoint)
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'cache.db')
            try:
                store.upsert(topic())
                analyse(self.groups[:1], store, matcher=self.matcher)
                scored = self.matcher.scored_pairs
                result = analyse(self.groups[:1], store, Client(), matcher=self.matcher)
                self.assertTrue(result[0].selected)
                self.assertEqual(self.matcher.scored_pairs, scored)
            finally:
                store.close()

    def test_stop_interrupts_body_comparison_and_keeps_all_scanned_groups(self):
        self.matcher.update([topic(body='\n'.join(f'Long local fixture phrase {n}' for n in range(500)))])
        calls, events = 0, []
        def stop():
            nonlocal calls
            calls += 1
            if calls == 5:
                raise Cancelled('stopped inside scoring')
        with self.assertRaises(Cancelled):
            self.matcher.refresh(self.groups, emit=lambda k, v: events.append((k, v)), stop=stop)
        previews = [value for kind, value in events if kind == 'matches']
        self.assertEqual(len(previews[-1]), len(self.groups))
        self.assertEqual(calls, 5)

    def test_stopping_rematch_cannot_leave_stale_author_selected(self):
        self.assertTrue(self.matcher.refresh(self.groups)[0].selected)
        self.matcher.update([topic(), topic(2, 'Beta')])
        events = []
        def stop():
            raise Cancelled('stopped before updated scores')
        with self.assertRaises(Cancelled):
            self.matcher.refresh(self.groups, emit=lambda k, v: events.append((k, v)), stop=stop)
        preview = next(value for kind, value in events if kind == 'matches')
        self.assertFalse(preview[0].selected)
        self.assertFalse(preview[0].author)
        self.assertEqual(preview[0].status, '等待匹配')

    def test_stop_after_unrelated_catalog_update_keeps_confirmed_result(self):
        self.assertTrue(self.matcher.refresh(self.groups)[0].selected)
        self.matcher.update([topic(), Topic(9, 'Zebra Quantum Workshop', 'Beta', '',
                                           body='Zebra Quantum Workshop', details_loaded=True)])
        events = []
        with self.assertRaises(Cancelled):
            self.matcher.refresh(self.groups, emit=lambda k, v: events.append((k, v)),
                                 stop=lambda: (_ for _ in ()).throw(Cancelled('stop')))
        preview = [v for k, v in events if k == 'matches'][-1]
        self.assertTrue(preview[0].selected)
        self.assertEqual(preview[0].author, 'Alpha')

    def test_stop_flushes_completed_group_before_throttled_preview(self):
        events = []
        real_finish = self.matcher._finish
        finished = False
        def finish(group, threshold):
            nonlocal finished
            value = real_finish(group, threshold)
            if group.key == self.groups[0].key:
                finished = True
            return value
        def stop():
            if finished:
                raise Cancelled('stop immediately after first group')
        second = replace(topic(2), title='Harbor Lights Midnight', body='Harbor Lights Midnight')
        self.matcher.update([topic(), second])
        with patch.object(self.matcher, '_finish', side_effect=finish), \
                patch('organizer.matching.time.monotonic', return_value=0):
            with self.assertRaises(Cancelled):
                self.matcher.refresh(self.groups, emit=lambda k, v: events.append((k, v)), stop=stop)
        preview = [v for k, v in events if k == 'matches'][-1]
        self.assertTrue(preview[0].selected)
        self.assertEqual(preview[0].author, 'Alpha')
        self.assertFalse(preview[1].selected)
        self.assertEqual(len(preview), 2)

    def test_external_catalog_change_invalidates_session(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cache.db'
            store, other = Store(path), Store(path)
            try:
                store.upsert(topic())
                self.matcher.sync(store)
                other.upsert(topic(2, 'Beta'))
                self.assertTrue(self.matcher.sync(store))
                self.assertEqual(self.matcher.match(self.groups[0]).status, '多作者待核对')
            finally:
                store.close()
                other.close()

    def test_progress_is_emitted_during_local_scoring(self):
        self.matcher.update([topic(body='\n'.join(f'Long local fixture phrase {n}' for n in range(500)))])
        events = []
        clock = iter(n * .3 for n in range(10000))
        with patch('organizer.matching.time.monotonic', side_effect=lambda: next(clock)):
            self.matcher.refresh(self.groups, emit=lambda k, v: events.append((k, v)))
        self.assertTrue(any(kind == 'progress' and '本地匹配' in value[2] for kind, value in events))


class BoundTests(unittest.TestCase):
    def test_lexical_bound_never_undercuts_actual_similarity(self):
        rng = random.Random(71)
        for _ in range(150):
            left = title_core(''.join(rng.choices('abcd ef012夜空', k=rng.randrange(5, 50))))
            right = title_core(''.join(rng.choices('abcd ef012夜空', k=rng.randrange(5, 50))))
            self.assertGreaterEqual(lexical_bound(left, right) + 1e-12, similarity(left, right)[0])

    def test_optimized_body_score_matches_exhaustive_windows(self):
        from organizer.evidence import body_windows
        fixtures = [('Moonlight Dnace 01', 'Moonlight Dance 01\nUnrelated release.\nOriginal: Moonlight Dance 01'),
                    ('青空放課後のタイム', '夜空の別作品\n青空放課後のタイム\nお知らせ'),
                    ('Harbor Lights Midnight', 'A long announcement ' * 100 + 'Harbor Lights Midnight')]
        for name, body in fixtures:
            scores = [max(similarity(name, window)[0], coverage(name, window) * .96)
                      for window, _ in body_windows(body, max(4, len(title_core(name).split()) + 3))]
            self.assertEqual(body_similarity(name, body)[0], max(scores))


if __name__ == '__main__':
    unittest.main()
