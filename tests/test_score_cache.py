import tempfile
import sqlite3
from contextlib import closing
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from organizer.matching import MatchingSession
from organizer.model import FileGroup, Topic


class PersistentScoreTests(unittest.TestCase):
    def test_previous_evidence_version_is_recomputed_without_deleting_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scores.sqlite3'
            group = FileGroup('g', 'Aurora Harbor Performance')
            topic = Topic(1, group.title, 'Author', '', body='Download below', details_loaded=True)
            first = MatchingSession(workers=1, cache_path=path)
            try:
                first.update([topic]); first.refresh([group])
            finally:
                first.dispose()
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("UPDATE scores SET version='evidence-2.2.1-v1'")
            second = MatchingSession(workers=1, cache_path=path)
            try:
                second.update([topic])
                self.assertTrue(second.refresh([group])[0].selected)
                self.assertEqual(second.scored_pairs, 1)
                self.assertEqual(second.reused_pairs, 0)
                self.assertEqual(len(second.index.topics), 1)
            finally:
                second.dispose()

    def test_reopen_reuses_scores_and_rechecks_changed_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scores.sqlite3'
            group = FileGroup('g', 'Aurora Harbor Performance')
            topic = Topic(1, group.title, 'Author', '', body=group.title, details_loaded=True)
            first = MatchingSession(workers=1, cache_path=path)
            try:
                first.update([topic])
                expected = first.refresh([group])[0]
                self.assertEqual(first.scored_pairs, 1)
            finally:
                first.dispose()
            second = MatchingSession(workers=1, cache_path=path)
            try:
                second.update([replace(topic, views=123)])
                self.assertEqual(asdict(second.refresh([group])[0]), asdict(expected))
                self.assertEqual(second.scored_pairs, 0)
                self.assertEqual(second.reused_pairs, 1)
                second.update([replace(topic, body='Different passage')])
                second.refresh([group])
                self.assertEqual(second.scored_pairs, 1)
                second.refresh([group], threshold=.99)
                self.assertEqual(second.scored_pairs, 1)
            finally:
                second.dispose()

    def test_different_direct_topic_reference_does_not_reuse_old_decision(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scores.sqlite3'
            topic = Topic(1, 'Aurora Harbor Performance', 'Author', '')
            first = MatchingSession(workers=1, cache_path=path)
            try:
                first.update([topic])
                first.refresh([FileGroup('g', topic.title, topic_ids=[1])])
            finally:
                first.dispose()
            second = MatchingSession(workers=1, cache_path=path)
            try:
                second.update([topic])
                self.assertFalse(second.refresh([FileGroup('g', topic.title)])[0].selected)
                self.assertEqual(second.scored_pairs, 1)
            finally:
                second.dispose()
