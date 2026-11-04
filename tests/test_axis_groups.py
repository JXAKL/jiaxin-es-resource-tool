import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer.engine import analyse
from organizer.model import scan, Topic, match_group, file_title, script_hint
from organizer.script_axes import AXIS_NAMES
from organizer.moves import execute, undo
from organizer.names import search_plan
from organizer.store import Store


class AxisGroupTests(unittest.TestCase):
    def test_all_named_axes_channels_and_delimiters_share_the_base(self):
        axes = sorted(AXIS_NAMES) + [f'{channel}{n}' for channel in 'LRVA' for n in range(10)] + ['vib2']
        for axis in axes:
            for suffix in ('.'+axis, '_'+axis, ' - '+axis, '['+axis+']', ' ('+axis+')', '【'+axis+'】'):
                with self.subTest(suffix=suffix):
                    self.assertEqual(file_title(Path('Aurora Harbor Midnight'+suffix+'.funscript')), 'Aurora Harbor Midnight')
                    self.assertEqual(script_hint('Aurora Harbor Midnight'+suffix), 'Aurora Harbor Midnight')
        self.assertEqual(file_title(Path('Aurora Harbor Midnight．ＶＡＬＶＥ.funscript')), 'Aurora Harbor Midnight')
        self.assertEqual(file_title(Path('Aurora Harbor Midnight.pitch.R0.funscript')), 'Aurora Harbor Midnight')
        for name in ('Aurora Last Twist', 'Aurora.roll.custom', 'Aurora Episode 2', 'Aurora.valve2', 'Aurora.R10'):
            self.assertEqual(file_title(Path(name+'.funscript')), name)
        self.assertEqual(file_title(Path('Aurora.pitch.mp4')), 'Aurora.pitch')

    def test_quality_only_names_keep_axes_together_without_cross_folder_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for sub in ('one','two'):
                (root/sub).mkdir()
                for suffix in ('.mp4','.funscript','.pitch.funscript','.valve.funscript','.A1.funscript'):
                    (root/sub/('1080p 60fps'+suffix)).write_text('{}')
            groups=scan([str(root)],'')
            self.assertEqual(len(groups),2)
            self.assertTrue(all(len(group.files)==5 and group.kind_label=='funscript / 视频' for group in groups))
            self.assertEqual(len(scan([str(root)],'',sync_search=False)),4)

    def test_sync_toggle_combines_video_with_single_or_multi_axis_scripts(self):
        for axes in (('',), ('', '.pitch', '.roll'), ('.pitch', '.roll')):
            with self.subTest(axes=axes), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                scripts = root / 'scripts'
                scripts.mkdir()
                title = 'Bouquetman - Lancer Artoria Riding'
                (root / (title + '.mp4')).write_bytes(b'fixture')
                for axis in axes:
                    (scripts / (title + axis + '.funscript')).write_text('{}')
                together = scan([str(root), str(scripts)], '', sync_search=True)
                separate = scan([str(root), str(scripts)], '', sync_search=False)
                self.assertEqual(len(together), 1)
                self.assertEqual(len(together[0].files), len(axes) + 1)
                self.assertEqual(together[0].kind_label, 'funscript / 视频')
                self.assertEqual(len(separate), 2)
                self.assertEqual({g.kind_label for g in separate}, {'funscript', '视频'})
                self.assertEqual(len(next(g for g in separate if g.kind_label == 'funscript').files), len(axes))
                self.assertEqual({f.path for g in together for f in g.files}, {f.path for g in separate for f in g.files})
                self.assertEqual(len({g.key for g in together + separate}), 3)
                store = Store(root / 'index.sqlite3')
                try:
                    for groups, count in ((together, 1), (separate, 2)):
                        with patch('organizer.engine.search_ids', return_value=[]) as search:
                            analyse(groups, store, client=object())
                        self.assertEqual(search.call_count, count)
                finally:
                    store.close()

    def test_sync_does_not_drop_unpaired_files_or_cross_episode_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('Moonlight 01.mp4', 'Moonlight 02.pitch.funscript',
                         'Harbor Lights.mp4', 'Harbor Lights.funscript'):
                (root / name).write_text('{}')
            for enabled, count in ((True, 3), (False, 4)):
                groups = scan([str(root)], '', sync_search=enabled)
                self.assertEqual(len(groups), count)
                self.assertEqual(sum(len(g.files) for g in groups), 4)

    def test_base_and_axes_preview_and_move_as_one_group(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, target = root / 'input', root / 'output'
            source.mkdir()
            title = 'Bouquetman - Lancer Artoria Riding'
            names = [title + suffix + '.funscript' for suffix in ('', '.pitch', '.roll', '.twist', '.R0', '．yaw', '.valve', '.V0', '.A1', '_lube', ' [pump]')]
            for name in names:
                (source / name).write_text(json.dumps({'metadata': {'title': name}}), encoding='utf-8')
            groups = scan([str(source), str(source)], str(target))
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].display_name, title + '.funscript')
            self.assertEqual(groups[0].kind_label, 'funscript')
            self.assertEqual(groups[0].hints, [])
            self.assertEqual(len(groups[0].files), len(names))
            self.assertFalse(any('pitch' in query or 'roll' in query or 'funscript' in query for query in search_plan(groups[0])))
            store = Store(root / 'index.sqlite3')
            try:
                with patch('organizer.engine.search_ids', return_value=[]) as search:
                    analyse(groups, store, client=object())
                self.assertEqual(search.call_count, 1)
            finally:
                store.close()
            topic = Topic(1, title, 'FixtureAuthor', 'https://example.invalid/', body=title, details_loaded=True)
            match = match_group(groups[0], [topic])
            result = execute([match], target, root / 'journal')
            self.assertEqual(result['moved'], len(names))
            self.assertEqual(sorted(p.name for p in (target / 'FixtureAuthor').iterdir()), sorted(names))
            self.assertEqual(undo(Path(result['journal']))['restored'], len(names))

    def test_axis_only_group_and_distinct_parts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('Moonlight 01.PITCH.funscript', 'Moonlight 01.roll.funscript',
                         'Moonlight 02.pitch.funscript', 'Moonlight 01.alternate.funscript'):
                (root / name).write_text('{}')
            groups = scan([str(root)], '')
            self.assertEqual(len(groups), 2)
            axis = next(g for g in groups if len(g.files) == 3)
            self.assertEqual(axis.display_name, 'Moonlight 01.funscript')

    def test_video_script_and_mixed_type_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('Mixed.mp4', 'Mixed.funscript', 'Only Video.mp4', 'Only Script.funscript'):
                (root / name).write_text('{}')
            groups = {g.title: g for g in scan([str(root)], '')}
            self.assertEqual(groups['Mixed'].kind_label, 'funscript / 视频')
            self.assertEqual(groups['Only Video'].kind_label, '视频')
            self.assertEqual(groups['Only Script'].kind_label, 'funscript')
