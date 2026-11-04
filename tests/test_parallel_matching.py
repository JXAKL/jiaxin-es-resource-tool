import tempfile
import time
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from organizer.evidence import body_similarity, body_windows, coverage
from organizer.forum import Cancelled
from organizer.matching import MatchingSession
from organizer.model import FileGroup, TopicIndex, similarity, title_core
from organizer.parallel_check import parallel_fixture, run_parallel_check


class ParallelMatchingTests(unittest.TestCase):
    def test_single_group_really_scores_in_multiple_processes(self):
        report = run_parallel_check()
        self.assertTrue(report['identical_results'])
        self.assertEqual(len(report['2']['processes']), 2)

    def test_incremental_small_group_batch_and_cancel_recovery(self):
        groups, topics = parallel_fixture()
        groups += [replace(groups[0], key='second', title='Aurora Harbor Performance Alternate')]
        matcher = MatchingSession(workers=2)
        try:
            matcher.update(topics)
            first = matcher.refresh(groups)
            self.assertEqual(len(matcher.last_work['processes']), 2)
            before = matcher.scored_pairs
            changed = [replace(t, body=t.body + '\nA new passage') for t in topics[:8]] + topics[8:]
            matcher.update(changed)
            matcher.refresh(groups)
            self.assertEqual(matcher.scored_pairs - before, 16)
            self.assertEqual(len(matcher.last_work['processes']), 2)
            matcher.update(topics)
            calls = 0
            def stop():
                nonlocal calls
                calls += 1
                if calls > 15:
                    raise Cancelled('fixture stop')
            with self.assertRaises(Cancelled):
                matcher.refresh(groups, stop=stop)
            self.assertIsNone(matcher._pool)
            resumed = matcher.refresh(groups)
            self.assertEqual([asdict(m) for m in resumed], [asdict(m) for m in first])
        finally:
            matcher.close()

    def test_incremental_postings_equal_full_rebuild(self):
        groups, topics = parallel_fixture()
        matcher = MatchingSession(workers=1)
        matcher.update(topics)
        updated = [replace(topics[0], title='Different unique title', body='Renamed material')] + topics[2:]
        matcher.update(updated)
        rebuilt = TopicIndex(updated)
        self.assertEqual(matcher.index.exact, rebuilt.exact)
        self.assertEqual(matcher.index.grams, rebuilt.grams)
        self.assertEqual(matcher.index.identifiers, rebuilt.identifiers)
        self.assertEqual(matcher.index.shortlist(groups[0]), rebuilt.shortlist(groups[0]))

    def test_body_shortcuts_preserve_scores_and_first_tied_excerpt(self):
        for name, body in [('abc', 'abc\nabc other'), ('12345', '12345\n12345 other'),
                           ('Moonlit Harbor', 'Moonlit Harbor extra\nMoonlit Harbor'),
                           ('Aurora Harbor 01', 'Aurora Harbor 02\nAurora Harbor 01 notes\nAurora Harbor 01 more')]:
            expected, excerpt = 0., ''
            for window, passage in body_windows(body, max(4, len(title_core(name).split()) + 3)):
                score = max(similarity(name, window)[0], coverage(name, window) * .96)
                if score > expected:
                    expected, excerpt = score, passage
            self.assertEqual(body_similarity(name, body), (expected, excerpt))


if __name__ == '__main__':
    unittest.main()
