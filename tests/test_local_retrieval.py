import unittest
from dataclasses import replace

from organizer.model import FileGroup, Topic, TopicIndex


class LocalRetrievalTests(unittest.TestCase):
    def test_large_unrelated_bodies_do_not_crowd_out_named_work(self):
        alphabet = ' '.join(chr(a)+chr(b) for a in range(97,123) for b in range(97,123))
        topics = [Topic(n, 'Unrelated collection', 'Other', '', body=alphabet) for n in range(1,151)]
        correct = Topic(999, 'Shared uploads', 'Author', '', body='Original title: Aurora Harbor Performance')
        index = TopicIndex(topics + [correct])
        found = index.shortlist(FileGroup('g', 'Aurora Harbor Performance'))
        self.assertIn(correct, found)
        self.assertLess(len(found), 100)

    def test_typo_title_and_late_body_evidence_remain_retrievable(self):
        typo = Topic(1, 'Aurora Harbour Performance', 'A', '')
        body = Topic(2, 'Shared material', 'B', '', body='Unrelated text.\n'*600+'Aurora Harbor Performance')
        index = TopicIndex([typo, body])
        self.assertEqual({t.id for t in index.shortlist(FileGroup('g', 'Aurora Harbor Performance'))}, {1,2})

    def test_cached_retrieval_tracks_updates_and_explicit_refs(self):
        first = Topic(1, 'Aurora Harbor Performance', 'A', '')
        second = Topic(2, 'Different material', 'B', '')
        index = TopicIndex([first, second])
        group = FileGroup('g', 'Aurora Harbor Performance', topic_ids=[2])
        self.assertIn(second, index.shortlist(group))
        renamed = replace(first, title='Other content', body='')
        index.update({1:renamed, 2:second}, {1})
        rebuilt = TopicIndex([renamed, second])
        self.assertEqual(index.shortlist(group), rebuilt.shortlist(group))

