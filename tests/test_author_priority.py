import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from organizer.engine import analyse, cached_evidence_candidates
from organizer.forum import Cancelled
from organizer.matching import MatchingSession
from organizer.model import FileGroup, Topic, match_group
from organizer.store import Store


def title(n):
    return f'Aurora Harbor Episode {n}'


def topic(n, verified=False, author='Alpha'):
    return Topic(n, title(n), author, f'https://discuss.eroscripts.com/t/{n}',
                 body=title(n) if verified else '', details_loaded=verified)


class AuthorWeightTests(unittest.TestCase):
    def make_batch(self, verified=3, total=10):
        matcher = MatchingSession()
        matcher.update([topic(n, n <= verified) for n in range(1, total + 1)])
        groups = [FileGroup(str(n), title(n)) for n in range(1, total + 1)]
        return matcher, groups

    def test_three_independent_works_enable_bounded_weight_without_fake_verification(self):
        matcher, groups = self.make_batch()
        results = matcher.refresh(groups)
        self.assertTrue(all(m.selected for m in results))
        candidate = results[-1].candidates[0]
        self.assertEqual(candidate.author_support, 3)
        self.assertFalse(candidate.verified)
        self.assertAlmostEqual(candidate.author_bonus, .14)
        self.assertIn('此帖未单独读取', candidate.reason)
        self.assertEqual(cached_evidence_candidates(results[-1]), [])
        # Repeated refresh / later match must agree without turning inferred works into support.
        self.assertEqual(matcher.refresh(groups), results)
        self.assertEqual(matcher.author_support, {'alpha': 3})
        self.assertEqual(matcher.match(groups[-1]), results[-1])

    def test_weight_grows_with_independent_works_and_is_capped(self):
        scores = []
        for count in (2, 3, 5, 7, 20):
            matcher, groups = self.make_batch(count, count + 1)
            result = matcher.refresh(groups)[-1]
            scores.append(result.candidates[0].score)
            self.assertEqual(result.selected, count >= 3)
        self.assertLess(scores[0], scores[1])
        self.assertLess(scores[1], scores[2])
        self.assertLess(scores[2], scores[3])
        self.assertEqual(scores[3], scores[4])

    def test_stop_after_unrelated_update_preserves_author_weight(self):
        matcher, groups = self.make_batch()
        self.assertTrue(matcher.refresh(groups)[-1].selected)
        matcher.update([*matcher.index.topics.values(), Topic(999, 'Zebra Quantum Workshop', 'Beta', '')])
        events = []
        def stop():
            raise Cancelled('stop')
        with self.assertRaises(Cancelled):
            matcher.refresh(groups, stop=stop, emit=lambda k, v: events.append((k, v)))
        result = [v for k, v in events if k == 'matches'][-1]
        self.assertTrue(all(m.selected for m in result))
        self.assertEqual(result[-1].candidates[0].author_support, 3)

    def test_duplicates_axes_and_separated_video_do_not_inflate_support(self):
        matcher, groups = self.make_batch(2, 3)
        groups = [groups[0], *[replace(groups[0], key=f'copy-{n}') for n in range(20)], *groups[1:]]
        result = matcher.refresh(groups)
        self.assertEqual(matcher.author_support, {'alpha': 2})
        self.assertFalse(result[-1].selected)

    def test_same_original_name_in_multiple_topics_counts_once(self):
        matcher = MatchingSession()
        matcher.update([replace(topic(n, True), title=title(1), body=title(1)) for n in range(1, 4)])
        matcher.refresh([FileGroup(str(n), title(1)) for n in range(4)])
        self.assertEqual(matcher.author_support, {'alpha': 1})

    def test_stricter_threshold_still_applies(self):
        matcher, groups = self.make_batch()
        self.assertFalse(matcher.refresh(groups, threshold=.95)[-1].selected)
        matcher, groups = self.make_batch(7)
        self.assertTrue(matcher.refresh(groups, threshold=.95)[-1].selected)
        self.assertFalse(matcher.refresh(groups, threshold=.99)[-1].selected)

    def test_competing_author_revokes_inference(self):
        matcher, groups = self.make_batch()
        self.assertTrue(matcher.refresh(groups)[-1].selected)
        rival = replace(topic(100), title=title(10), author='Beta')
        matcher.update([*matcher.index.topics.values(), rival])
        result = matcher.refresh(groups)[-1]
        self.assertFalse(result.selected)
        self.assertTrue(all(c.author_bonus == 0 for c in result.candidates))

    def test_lost_support_revokes_inference(self):
        matcher, groups = self.make_batch()
        self.assertTrue(matcher.refresh(groups)[-1].selected)
        matcher.update([topic(n, n <= 2) for n in range(1, 11)])
        self.assertFalse(matcher.refresh(groups)[-1].selected)

    def test_stop_during_revision_clears_stale_weighted_selections(self):
        matcher, groups = self.make_batch()
        self.assertTrue(matcher.refresh(groups)[-1].selected)
        matcher.update([topic(n, n <= 2) for n in range(1, 11)])
        events = []
        def stop():
            raise Cancelled('fixture')
        with self.assertRaises(Cancelled):
            matcher.refresh(groups, emit=lambda *args: events.append(args), stop=stop)
        preview = next(value for kind, value in events if kind == 'matches')
        self.assertFalse(preview[-1].selected)
        self.assertEqual(preview[-1].status, '等待匹配')

    def test_conflicts_generic_titles_and_verified_weak_evidence_are_not_boosted(self):
        matcher, groups = self.make_batch()
        extra = [replace(topic(101), title='Dance'), replace(topic(102), title='Unrelated Plain Song',
                  details_loaded=True, body='Nothing relevant')]
        matcher.update([*matcher.index.topics.values(), *extra])
        groups += [FileGroup('conflict', title(999)), FileGroup('generic', 'Dance'),
                   FileGroup('weak', 'Unrelated Plain Song Missing Edition')]
        for result in matcher.refresh(groups)[-3:]:
            self.assertFalse(result.selected)
            self.assertFalse(any(c.author_bonus for c in result.candidates))


class PriorityPipelineTests(unittest.TestCase):
    def test_discovering_author_reorders_late_works_ahead_of_unrelated_files(self):
        class Client:
            def __init__(self):
                self.calls = []
            def check(self):
                pass
            def get(self, endpoint, params=None):
                self.calls.append((endpoint, params))
                if endpoint == '/search.json':
                    return {'topics': [{'id': 1, 'title': title(1)}] if 'aurora' in params['q'] else []}
                if endpoint.startswith('/t/'):
                    n = int(endpoint.rsplit('/', 1)[1].split('.')[0])
                    return {'id': n, 'title': title(n), 'post_stream': {'posts': [
                        {'post_number': 1, 'username': 'Alpha', 'cooked': f'<p>{title(n)}</p>'}]}}
                if endpoint == '/u/Alpha.json':
                    return {'user': {'username': 'Alpha'}}
                if endpoint == '/user_actions.json':
                    return {'user_actions': [{'action_type': 4, 'post_number': 1, 'topic_id': n,
                        'username': 'Alpha', 'title': title(n)} for n in range(1, 11)] if params['offset'] == 0 else []}
                raise AssertionError(endpoint)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'catalog.db')
            try:
                groups = [FileGroup('1', title(1)), FileGroup('unknown', 'Uncatalogued Crimson Landscape')]
                groups += [FileGroup(str(n), title(n)) for n in range(2, 11)]
                client = Client()
                results = analyse(groups, store, client)
                last_post = max(i for i, (path, _) in enumerate(client.calls) if path.startswith('/t/'))
                unrelated_search = next(i for i, (path, params) in enumerate(client.calls)
                                        if path == '/search.json' and 'uncatalogued' in params['q'])
                self.assertLess(last_post, unrelated_search)
                self.assertEqual([p for p, _ in client.calls if p.startswith('/t/')],
                                 ['/t/1.json', '/t/2.json', '/t/3.json'])
                self.assertTrue(all(m.selected for m in results if m.group.key != 'unknown'))
                self.assertTrue(all(not g.search_queries for g in groups[2:]))
            finally:
                store.close()

    def test_late_indexed_batch_needs_only_three_posts_and_goes_before_unknown_search(self):
        class Client:
            def __init__(self):
                self.calls = []
            def get(self, endpoint, params=None):
                self.calls.append(endpoint)
                if endpoint == '/search.json':
                    return {'topics': []}
                if endpoint.startswith('/t/'):
                    n = int(endpoint.split('/')[-1].split('.')[0])
                    return {'id': n, 'title': title(n), 'post_stream': {'posts': [
                        {'post_number': 1, 'username': 'Alpha', 'cooked': '<p>' + title(n) + '</p>'}]}}
                raise AssertionError('Unexpected request: ' + endpoint)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'catalog.db')
            try:
                for n in range(1, 31):
                    store.upsert(topic(n))
                store.author_progress('Alpha', 30, complete=True)
                groups = [FileGroup('unknown', 'Completely Unknown Skyline')]
                groups += [FileGroup(str(n), title(n)) for n in range(1, 31)]
                client = Client()
                results = analyse(groups, store, client)
                self.assertEqual(client.calls[:3], ['/t/1.json', '/t/2.json', '/t/3.json'])
                self.assertEqual(sum(p.startswith('/t/') for p in client.calls), 3)
                self.assertTrue(all(m.selected for m in results[1:]))
                self.assertTrue(all(not g.search_queries for g in groups[1:]))
                self.assertEqual(results[0].group.key, 'unknown')
                self.assertFalse(results[0].selected)
            finally:
                store.close()

    def test_confirmed_work_skips_weak_candidates_but_preserves_strong_rival_check(self):
        group = FileGroup('x', 'Aurora Harbor Midnight')
        verified = Topic(1, group.title, 'Alpha', '', body=group.title, details_loaded=True)
        weak = Topic(2, 'Aurora Harbor Midnight Extended', 'Beta', '')
        result = match_group(group, [verified, weak])
        self.assertTrue(result.selected)
        self.assertEqual(cached_evidence_candidates(result), [])
        strong = replace(weak, title=group.title)
        result = match_group(group, [verified, strong])
        self.assertEqual([c.topic_id for c in cached_evidence_candidates(result)], [2])


if __name__ == '__main__':
    unittest.main()
