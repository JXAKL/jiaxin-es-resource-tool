import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from organizer import folder_groups as fg


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'library'; self.root.mkdir()
        self.target = self.base/'small'

    def file(self, name, data=b'fixture'):
        path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return path

    def scan(self):
        return fg.scan_folders(self.root, self.target)

    def move(self, rows, **kw):
        for row in rows: row.selected = True
        return fg.execute(rows, self.root, self.target, 5, self.base/'journals', **kw)

    def test_multiaxis_nested_counts_and_strict_threshold(self):
        for name in ['A/Dance.mp4','A/scripts/Dance.funscript','A/scripts/Dance.pitch.funscript',
                     'A/scripts/Dance.roll.funscript','A/Second.mkv','A/Third.funscript','A/readme.txt']:
            self.file(name)
        row = self.scan()[0]
        self.assertEqual((row.groups,row.paired,row.video_only,row.script_only,row.files), (3,1,1,1,7))
        self.assertFalse(fg.eligible(row,3))
        self.assertTrue(fg.eligible(row,4))
        self.assertTrue(fg.eligible(row,2,paired_only=True))

    def test_zero_and_nonmedia_excluded_by_default(self):
        self.file('notes/readme.txt'); (self.root/'empty').mkdir()
        for row in self.scan():
            self.assertFalse(fg.eligible(row,5))
            self.assertTrue(fg.eligible(row,5,include_zero=True))
        for value in ('-1','1.5','abc',''):
            with self.assertRaises(ValueError): fg.threshold_value(value)

    def test_extended_axes_and_quality_only_names_count_as_paired_groups(self):
        for title in ('Aurora Harbor Midnight','1080p 60fps'):
            for suffix in ('.mp4','.funscript','.valve.funscript','.V0.funscript',' [A1].funscript'):
                self.file('A/'+title+suffix)
        row=self.scan()[0]
        self.assertEqual((row.groups,row.paired,row.video_only,row.script_only,row.files),(2,2,0,0,10))

    def test_move_and_undo_preserve_whole_folder(self):
        self.file('A/Dance.mp4'); self.file('A/note.txt', b'keep me')
        (self.root/'A'/'empty'/'nested').mkdir(parents=True)
        before = fg.inventory(self.root/'A')
        result = self.move(self.scan())
        self.assertEqual(result['moved'],1); self.assertFalse(result['errors'])
        self.assertFalse((self.root/'A').exists())
        self.assertEqual(fg.inventory(self.target/'A'), before)
        restored = fg.undo_folders(result['journal'])
        self.assertFalse(restored['errors']); self.assertEqual(restored['folders'],1)
        self.assertEqual(fg.inventory(self.root/'A'), before)
        self.assertEqual(fg.undo_folders(result['journal'])['folders'],0)

    def test_conflict_preview_and_race_never_overwrite(self):
        self.file('A/a.mp4'); rows = self.scan()
        (self.target/'A').mkdir(parents=True); (self.target/'A'/'precious.txt').write_text('keep')
        self.assertTrue(self.scan()[0].error)
        result = self.move(rows)
        self.assertEqual(result['moved'],0); self.assertTrue(result['errors'])
        self.assertEqual((self.target/'A'/'precious.txt').read_text(),'keep')

    def test_changed_source_refused(self):
        self.file('A/a.mp4'); rows = self.scan(); self.file('A/new.txt')
        result = self.move(rows)
        self.assertEqual(result['moved'],0); self.assertTrue(result['errors'])

    def test_destination_subtree_excluded_and_same_root_refused(self):
        self.file('A/a.mp4')
        rows = fg.scan_folders(self.root, self.root/'A'/'out')
        self.assertTrue(rows[0].error)
        with self.assertRaises(ValueError): fg.scan_folders(self.root,self.root)

    def test_unreadable_directory_is_error_not_zero(self):
        self.file('A/a.mp4')
        with patch.object(fg, 'inventory', side_effect=PermissionError('denied')):
            row = self.scan()[0]
        self.assertTrue(row.error); self.assertFalse(fg.eligible(row,100,include_zero=True))

    def test_modified_target_undo_refused(self):
        self.file('A/a.mp4'); result = self.move(self.scan())
        (self.target/'A'/'new.txt').write_text('keep')
        restored = fg.undo_folders(result['journal'])
        self.assertTrue(restored['errors']); self.assertTrue((self.target/'A'/'a.mp4').exists())

    def cross_rename(self, source, target):
        # Force the top-level directory path to use the real per-file fallback.
        if Path(source).is_dir() or Path(source).is_relative_to(self.root):
            raise OSError(errno.EXDEV, 'cross device')
        return self.real_rename(source,target)

    def test_cross_volume_preserves_files_empty_dirs_and_undo(self):
        self.file('A/a.mp4'); self.file('A/deep/note.txt')
        (self.root/'A'/'empty').mkdir()
        before = fg.inventory(self.root/'A'); rows = self.scan()
        self.real_rename = os.rename
        with patch.object(fg.os,'rename',side_effect=self.cross_rename): result = self.move(rows)
        self.assertEqual(result['moved'],1); self.assertFalse(result['errors'])
        self.assertEqual(fg.inventory(self.target/'A'),before)
        restored = fg.undo_folders(result['journal'])
        self.assertFalse(restored['errors']); self.assertEqual(fg.inventory(self.root/'A'),before)

    def test_partial_cross_volume_stop_and_undo(self):
        self.file('A/a.mp4'); self.file('A/z.txt')
        before = fg.inventory(self.root/'A'); rows = self.scan(); self.real_rename = os.rename
        def stop():
            if (self.target/'A'/'a.mp4').exists(): raise fg.Cancelled()
        with patch.object(fg.os,'rename',side_effect=self.cross_rename): result = self.move(rows,stop=stop)
        self.assertTrue(result['stopped']); self.assertTrue((self.root/'A'/'z.txt').exists())
        restored = fg.undo_folders(result['journal'])
        self.assertFalse(restored['errors']); self.assertEqual(fg.inventory(self.root/'A'),before)

    def test_stop_before_move_and_unselected_preserved(self):
        self.file('A/a.mp4'); rows = self.scan()
        result = fg.execute(rows,self.root,self.target,5,self.base/'journals')
        self.assertEqual(result['moved'],0); self.assertTrue((self.root/'A').exists())
        def stop(): raise fg.Cancelled()
        result = self.move(rows,stop=stop)
        self.assertTrue(result['stopped']); self.assertTrue((self.root/'A').exists())

    def test_journal_outside_paths_rejected(self):
        self.file('A/a.mp4'); result = self.move(self.scan())
        journal = Path(result['journal'])
        records = [json.loads(line) for line in journal.read_text().splitlines()]
        plan = next(r for r in records if r['event']=='folder_planned')
        plan['snapshot']['dirs'].append('../escape')
        journal.write_text('\n'.join(json.dumps(r) for r in records),encoding='utf-8')
        with self.assertRaises(ValueError): fg.undo_folders(journal)
        self.assertTrue((self.target/'A'/'a.mp4').exists())


if __name__ == '__main__': unittest.main()
