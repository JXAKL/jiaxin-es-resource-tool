import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer.engine import analyse
from organizer.forum import Forum, ForumError, author_from_input, topic_from_json
from organizer.model import FileGroup, LocalFile, Topic, TopicIndex, match_group, scan, similarity, title_core
from organizer.moves import digest, execute, safe_author, undo
from organizer.store import Store


def topic(identity=1, title='Moonlight Dance 01', author='Alpha'):
    return Topic(identity, title, author, f'https://discuss.eroscripts.com/t/{identity}', body=title, details_loaded=True)


class MatchingTests(unittest.TestCase):
    def test_filename_vs_title_dots_not_extensions(self):
        self.assertEqual(title_core('Episode 1.5 - 1080p'), 'episode 1 5')
        self.assertGreater(similarity('[FREE] Moonlight Dance 01', 'Moonlight_Dance_01.1080p')[0], .97)

    def test_number_conflicts(self):
        score, _, conflict = similarity('Moonlight Dance 01', 'Moonlight Dance 02')
        self.assertTrue(conflict)
        self.assertLessEqual(score, .45)

    def test_same_title_two_authors(self):
        result = match_group(FileGroup('x', 'Moonlight Dance 01'), [topic(), topic(2, author='Beta')])
        self.assertEqual(result.status, '多作者待核对')
        self.assertFalse(result.selected)

    def test_reply_username_does_not_become_author(self):
        result = topic_from_json({'id': 1, 'title': 'Moonlight Dance', 'post_stream': {'posts': [
            {'post_number': 2, 'username': 'ReplyUser'}, {'post_number': 1, 'username': 'Creator',
             'cooked': '<a href="/uploads/unique.funscript">Moonlight Dance.funscript</a>'}]}})
        self.assertEqual(result.author, 'Creator')
        self.assertIn('Moonlight Dance.funscript', result.filenames)

    def test_missing_first_post_is_error(self):
        with self.assertRaises(ForumError):
            topic_from_json({'id': 1, 'title': 'Title', 'post_stream': {'posts': [{'post_number': 2, 'username': 'Reply'}]}})

    def test_short_or_numeric_name_is_manual(self):
        for name in ('Abc', '123456'):
            self.assertFalse(match_group(FileGroup(name, name), [topic(title=name)]).selected)

    def test_metadata_reference_can_resolve_renamed_file(self):
        result = match_group(FileGroup('x', 'renamed', topic_ids=[1]), [topic()])
        self.assertEqual(result.author, 'Alpha')
        self.assertTrue(result.selected)

    def test_scan_pairs_axis_scripts_and_excludes_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'Moonlight Dance.mp4').write_bytes(b'video')
            (root / 'Moonlight Dance.roll.funscript').write_text('{}')
            (root / 'out').mkdir()
            (root / 'out' / 'Already Sorted.mp4').write_bytes(b'x')
            groups = scan([str(root), str(root)], str(root / 'out'))
            self.assertEqual(len(groups), 1)
            self.assertEqual(len(groups[0].files), 2)

    def test_author_folder_and_input(self):
        self.assertEqual(author_from_input('https://discuss.eroscripts.com/u/Alpha/activity/portfolio'), 'Alpha')
        self.assertNotIn('/', safe_author('../../Alpha'))
        self.assertNotEqual(safe_author('CON'), 'CON')
        with self.assertRaises(ValueError):
            author_from_input('https://example.com/u/Alpha')

    def test_index_keeps_all_exact_author_competitors(self):
        topics = [topic(n, author=f'Author{n}') for n in range(1, 150)]
        result = match_group(FileGroup('x', 'Moonlight Dance 01'), TopicIndex(topics))
        self.assertEqual(result.status, '多作者待核对')

    def test_generic_attachment_names_are_not_auto_selected(self):
        self.assertFalse(match_group(FileGroup('x', 'default'), [topic(title='default')]).selected)


class FakeClient:
    def __init__(self, fail_page=False):
        self.offsets = []
        self.fail_page = fail_page

    def check(self):
        pass

    def get(self, endpoint, params=None):
        if endpoint.startswith('/t/'):
            return {'id': 1, 'title': 'Moonlight Dance 01', 'post_stream': {'posts': [
                {'post_number': 1, 'username': 'Alpha', 'cooked': '<p>Moonlight Dance 01</p>'}]}}
        if endpoint.startswith('/u/'):
            return {'user': {'username': 'Alpha'}}
        offset = params['offset']
        self.offsets.append(offset)
        if offset == 0:
            return {'user_actions': [{'action_type': 4, 'post_number': 1, 'username': 'Alpha',
                                     'topic_id': 1, 'title': 'Moonlight Dance 01'}]}
        if self.fail_page:
            raise ForumError('Temporary failure')
        return {'user_actions': []}


class CrawlTests(unittest.TestCase):
    def test_import_legacy_and_exported_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / 'cache.db')
            try:
                path = root / 'legacy.json'
                path.write_text(json.dumps([{'title': 'Moonlight Dance - Scripts / Free Scripts',
                                            'url': 'https://discuss.eroscripts.com/t/moonlight-dance'}]), encoding='utf-8')
                self.assertEqual(store.import_file(path, 'Alpha'), 1)
                self.assertEqual(store.topics()[0].title, 'Moonlight Dance')
                store.export(root / 'out')
                self.assertEqual(store.import_file(root / 'out' / '作者帖子索引.json'), 1)
                self.assertEqual(len(store.topics()), 1)
            finally:
                store.close()

    def test_bad_import_does_not_partially_change_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / 'cache.db')
            try:
                path = root / 'bad.json'
                path.write_text(json.dumps([{'title': 'Valid', 'url': 'https://discuss.eroscripts.com/t/123'}, {'title': 'Invalid'}]), encoding='utf-8')
                with self.assertRaises(ValueError):
                    store.import_file(path, 'Alpha')
                self.assertEqual(store.topics(), [])
            finally:
                store.close()

    def test_pagination_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'cache.db')
            try:
                first = FakeClient(fail_page=True)
                with self.assertRaises(ForumError):
                    Forum(first, store).author('Alpha')
                self.assertEqual(store.author('Alpha')['offset'], 1)
                self.assertFalse(store.author('Alpha')['complete'])
                second = FakeClient()
                Forum(second, store).author('Alpha')
                self.assertEqual(second.offsets, [1])
                self.assertTrue(store.author('Alpha')['complete'])
            finally:
                store.close()

    def test_seed_author_resolves_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'cache.db')
            try:
                result = analyse([FileGroup('x', 'Moonlight Dance 01')], store, FakeClient(), seeds=['Alpha'])
                self.assertTrue(result[0].selected)
            finally:
                store.close()

    def test_bad_search_response_not_saved_as_no_match(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'cache.db')
            try:
                client = FakeClient()
                client.get = lambda *args: {'unexpected': []}
                with self.assertRaises(ForumError):
                    Forum(client, store).search('Moonlight Dance')
                self.assertIsNone(store.search('2:Moonlight Dance in:title'))
            finally:
                store.close()


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.src = self.root / 'input'
        self.src.mkdir()
        for name in ('Moonlight Dance.mp4', 'Moonlight Dance.funscript'):
            (self.src / name).write_bytes(b'contents of ' + name.encode())
        groups = scan([str(self.src)], str(self.root / 'output'))
        self.matches = [match_group(g, [topic(title='Moonlight Dance')]) for g in groups]

    def tearDown(self):
        self.temp.cleanup()

    def run_move(self):
        return execute(self.matches, self.root / 'output', self.root / 'journals')

    def test_move_and_undo_preserves_bytes(self):
        before = {p.name: digest(p) for p in self.src.iterdir()}
        result = self.run_move()
        self.assertEqual(result['moved'], 2)
        restored = undo(Path(result['journal']))
        self.assertEqual(restored['restored'], 2)
        self.assertEqual(before, {p.name: digest(p) for p in self.src.iterdir()})

    def test_collision_keeps_entire_pair(self):
        dest = self.root / 'output' / 'Alpha'
        dest.mkdir(parents=True)
        (dest / 'Moonlight Dance.mp4').write_bytes(b'existing')
        result = self.run_move()
        self.assertEqual(result['moved'], 0)
        self.assertEqual(len(list(self.src.iterdir())), 2)
        self.assertEqual((dest / 'Moonlight Dance.mp4').read_bytes(), b'existing')

    def test_changed_source_is_skipped(self):
        (self.src / 'Moonlight Dance.mp4').write_bytes(b'new content with changed length')
        self.assertEqual(self.run_move()['moved'], 0)

    def test_undo_does_not_overwrite_or_restore_modified_files(self):
        result = self.run_move()
        (self.src / 'Moonlight Dance.mp4').write_bytes(b'new source')
        (self.root / 'output' / 'Alpha' / 'Moonlight Dance.funscript').write_bytes(b'changed')
        restored = undo(Path(result['journal']))
        self.assertEqual(restored['restored'], 0)
        self.assertEqual(len(restored['errors']), 2)

    def test_crash_before_done_record_is_recoverable(self):
        result = self.run_move()
        journal = Path(result['journal'])
        lines = [line for line in journal.read_text(encoding='utf-8').splitlines() if json.loads(line)['event'] != 'done']
        journal.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        self.assertEqual(undo(journal)['restored'], 2)

    def test_cross_volume_path_uses_verified_copy(self):
        from organizer import moves
        original = moves.publish
        def simulated(source, target):
            if source.parent == self.src:
                import errno
                raise OSError(errno.EXDEV, 'cross-device')
            return original(source, target)
        with patch.object(moves, 'publish', side_effect=simulated):
            result = self.run_move()
        self.assertEqual(result['moved'], 2)
        self.assertEqual(undo(Path(result['journal']))['restored'], 2)


if __name__ == '__main__':
    unittest.main()
