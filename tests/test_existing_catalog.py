"""Read-only regression against the user's previously exported metadata format."""
import tempfile
import unittest
from pathlib import Path

from organizer.model import FileGroup, TopicIndex, match_group
from organizer.store import Store

CATALOG = Path(__file__).resolve().parents[2] / 'eroscripts_gofile/output/Ox_Gofile_Data.json'


@unittest.skipUnless(CATALOG.exists(), '已有采集器导出不在当前电脑')
class ExistingCatalogTests(unittest.TestCase):
    def test_existing_metadata_import_and_sample_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'fixture.db')
            try:
                count = store.import_file(CATALOG, 'fixture_author')
                self.assertGreater(count, 0)
                topics = store.topics()
                index = TopicIndex(topics)
                # A fixture label is used; this test does not verify the real author.
                for topic in topics[::max(1, len(topics) // 20)]:
                    match = match_group(FileGroup(str(topic.id), topic.title), index)
                    self.assertTrue(match.candidates)
                    self.assertEqual(match.candidates[0].author, 'fixture_author')
                    self.assertGreaterEqual(match.candidates[0].title_score, .90)
                    self.assertFalse(match.selected, 'A legacy title-only export does not prove post content')
                print(f'Existing local metadata: {count} rows imported; sampled title matches passed.')
            finally:
                store.close()
