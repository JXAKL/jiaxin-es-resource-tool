import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from organizer.local_tools.smart_match import process_match
from organizer.local_tools import pair_output
from organizer.local_tools.script_intensity import grade_batch


class PairOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root/'output'
        self.video = self.root/'video'/'Work 1080p.mp4'
        self.video.parent.mkdir()
        self.video.write_bytes(b'video fixture')
        self.scripts = []
        for suffix in ('', '.pitch', '.roll', '.L2', '.twist'):
            path = self.root/'scripts'/f'Work{suffix}.funscript'
            path.parent.mkdir(exist_ok=True)
            path.write_text('{"actions":[{"at":0,"pos":0},{"at":1000,"pos":100}]}')
            self.scripts.append(path)
        self.row = dict(video_path=str(self.video), funscript_path=str(self.scripts[0]),
                        script_paths=[str(p) for p in self.scripts])

    def test_default_only_video_even_if_separate_requested(self):
        target = Path(process_match(self.row, self.output, move=True, separate_folders=True))
        self.assertEqual(target, self.output/'Work.mp4')
        self.assertFalse(self.video.exists())
        self.assertTrue(all(p.exists() for p in self.scripts))
        self.assertEqual(len(list(self.output.iterdir())), 1)

    def test_copy_complete_group(self):
        target = Path(process_match(self.row, self.output, include_scripts=True))
        self.assertEqual(target.parent, self.output)
        self.assertTrue(self.video.exists())
        self.assertTrue(all(p.exists() for p in self.scripts))
        self.assertEqual({p.name for p in self.output.iterdir()}, {'Work.mp4', *(p.name for p in self.scripts)})
        for source, output in zip(self.scripts, self.row['output_script_paths']):
            self.assertEqual(source.read_bytes(), Path(output).read_bytes())

    def test_move_separate_complete_group(self):
        target = Path(process_match(self.row, self.output, move=True, include_scripts=True, separate_folders=True))
        self.assertEqual(target.parent, self.output/'Work 1080p')
        self.assertFalse(self.video.exists())
        self.assertTrue(all(not p.exists() for p in self.scripts))
        self.assertEqual(len(list(target.parent.iterdir())), 6)

    def test_manual_pair_includes_axes_but_not_other_versions(self):
        self.row.pop('script_paths')
        other = self.root/'scripts'/'Work (max).pitch.funscript'
        other.write_text('{}')
        process_match(self.row, self.output, move=True, include_scripts=True)
        self.assertEqual(len(self.row['output_script_paths']), 5)
        self.assertTrue(other.exists())

    def test_script_conflict_keeps_whole_group_together(self):
        self.output.mkdir()
        existing = self.output/self.scripts[1].name
        existing.write_bytes(b'existing')
        target = Path(process_match(self.row, self.output, include_scripts=True))
        self.assertNotEqual(target.parent, self.output)
        self.assertEqual(existing.read_bytes(), b'existing')
        self.assertTrue(all(Path(p).parent == target.parent for p in self.row['output_script_paths']))

    def test_missing_axis_does_not_move_video(self):
        self.scripts[-1].unlink()
        with self.assertRaises(FileNotFoundError):
            process_match(self.row, self.output, move=True, include_scripts=True)
        self.assertTrue(self.video.exists())
        self.assertTrue(self.scripts[0].exists())
        self.assertNotIn('output_path', self.row)

    def test_rollback_mid_move_and_overwrite_publication(self):
        self.output.mkdir()
        existing = self.output/'Work.mp4'
        existing.write_bytes(b'existing video')
        real_publish = pair_output.publish
        def fail(source, target):
            if Path(target).name == self.scripts[1].name:
                raise OSError('fixture disk failure')
            return real_publish(source, target)
        with patch.object(pair_output, 'publish', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'fixture'):
                process_match(self.row, self.output, move=True, include_scripts=True, overwrite=True)
        self.assertTrue(self.video.exists())
        self.assertTrue(all(p.exists() for p in self.scripts))
        self.assertEqual(existing.read_bytes(), b'existing video')
        self.assertEqual(list(self.output.iterdir()), [existing])

    def test_copy_failure_retains_originals_and_existing_output(self):
        real_copy = pair_output.shutil.copy2
        def fail(source, target):
            if Path(source) == self.scripts[1]:
                Path(target).write_bytes(b'partial')
                raise OSError('fixture copy failure')
            return real_copy(source, target)
        with patch.object(pair_output.shutil, 'copy2', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'fixture'):
                process_match(self.row, self.output, include_scripts=True)
        self.assertTrue(self.video.exists())
        self.assertTrue(all(p.exists() for p in self.scripts))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_shared_axes_survive_multiple_video_move_with_intensity(self):
        second = copy.deepcopy(self.row)
        second_video = self.root/'video'/'Work 2160p.mp4'
        second_video.write_bytes(b'second video')
        second['video_path'] = str(second_video)
        cohort = grade_batch([self.row, second])
        moved = {}
        first_target = Path(process_match(self.row, self.output, move=True, include_scripts=True,
                            separate_folders=True, moved_scripts=moved, classify=True, cohort=cohort))
        self.row['status'] = '已处理'
        moved = pair_output.relocated_scripts([self.row])
        second_target = Path(process_match(second, self.output, move=True, include_scripts=True,
                             separate_folders=True, moved_scripts=moved, classify=True, cohort=cohort))
        self.assertNotEqual(first_target.parent, second_target.parent)
        self.assertEqual(first_target.parent.parent, second_target.parent.parent)
        for path in [*self.row['output_script_paths'], *second['output_script_paths']]:
            self.assertTrue(Path(path).is_file())
        self.assertFalse(second_video.exists())

    def test_video_already_at_destination_still_transfers_scripts(self):
        self.output.mkdir()
        target = self.output/'Work.mp4'
        self.video.rename(target)
        self.row['video_path'] = str(target)
        result = process_match(self.row, self.output, move=True, include_scripts=True)
        self.assertEqual(Path(result), target)
        self.assertEqual(len(self.row['output_script_paths']), 5)
        self.assertTrue(all(not p.exists() for p in self.scripts))

    def test_conflicting_script_names_from_different_folders_fail_preflight(self):
        other = self.root/'other'/self.scripts[0].name
        other.parent.mkdir(); other.write_text('{}')
        self.row['script_paths'].append(str(other))
        with self.assertRaisesRegex(ValueError, '同名脚本'):
            process_match(self.row, self.output, move=True, include_scripts=True)
        self.assertTrue(self.video.exists())
        self.assertTrue(other.exists())

    def test_overwrite_success(self):
        self.output.mkdir()
        (self.output/'Work.mp4').write_bytes(b'old video')
        (self.output/self.scripts[1].name).write_bytes(b'old script')
        target = Path(process_match(self.row, self.output, overwrite=True, include_scripts=True))
        self.assertEqual(target.read_bytes(), self.video.read_bytes())
        self.assertEqual((self.output/self.scripts[1].name).read_bytes(), self.scripts[1].read_bytes())
        self.assertFalse(list(self.output.glob('.pair-*')))

    def test_rollback_preserves_recreated_source_and_recovery_file(self):
        real_publish = pair_output.publish
        def fail(source, target):
            if Path(target).name == self.scripts[1].name:
                self.video.write_bytes(b'new external file')
                raise OSError('fixture interruption')
            return real_publish(source, target)
        with patch.object(pair_output, 'publish', side_effect=fail):
            with self.assertRaisesRegex(OSError, '保留恢复目录'):
                process_match(self.row, self.output, move=True, include_scripts=True)
        self.assertEqual(self.video.read_bytes(), b'new external file')
        self.assertEqual((self.output/'Work.mp4').read_bytes(), b'video fixture')
        recovery = next(self.output.glob('.pair-*'))
        self.assertTrue((recovery/'recovery.json').is_file())

    def test_shared_manual_pair_keeps_all_axes_after_first_move(self):
        self.row.pop('script_paths')
        second = copy.deepcopy(self.row)
        other = self.video.with_name('Work 2160p.mp4'); other.write_bytes(b'other video')
        second['video_path'] = str(other)
        moved = {}
        process_match(self.row, self.output, move=True, include_scripts=True, separate_folders=True, moved_scripts=moved)
        process_match(second, self.output, move=True, include_scripts=True, separate_folders=True, moved_scripts=moved)
        self.assertEqual(len(second['output_script_paths']), 5)
        self.assertEqual({Path(p).name for p in second['output_script_paths']}, {p.name for p in self.scripts})
        self.assertTrue(all(Path(p).exists() for p in self.row['output_script_paths']))

    def test_cleanup_failure_does_not_lose_successful_move_paths(self):
        with patch.object(pair_output.shutil, 'rmtree', side_effect=PermissionError('fixture backup lock')):
            target = process_match(self.row, self.output, move=True, include_scripts=True)
        self.assertTrue(Path(target).is_file())
        self.assertFalse(self.video.exists())
        self.assertEqual(len(self.row['output_script_paths']), 5)
        self.assertIn('整组已完成', self.row['output_note'])


if __name__ == '__main__':
    unittest.main()
