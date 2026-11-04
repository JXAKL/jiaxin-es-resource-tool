import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.local_tools.video_duplicates import core, backend
from organizer.local_tools.funscript_duplicates.report_io import load_report as load_script_report


class VideoDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='video-duplicate-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def file(self, name, data=b'synthetic video bytes'):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_renamed_recursive_cross_extension_exact_match(self):
        a = self.file('A/one.mp4')
        b = self.file('B/sub/renamed.MKV')
        before = (a.read_bytes(), b.read_bytes())
        result = core.scan_folders([self.root/'A', self.root/'B'])
        self.assertEqual(result['scanned'], 2)
        self.assertEqual(result['hashed_files'], 2)
        self.assertEqual(len(result['exact_groups']), 1)
        self.assertEqual((a.read_bytes(), b.read_bytes()), before)
        self.assertEqual(result['exact_groups'][0][0]['hash_kind'], 'full')

    def test_different_sizes_are_excluded_without_reading(self):
        self.file('a.mp4', b'aaa')
        self.file('b.mp4', b'bbbb')
        with patch.object(core, 'read_video', side_effect=AssertionError('unique size must not be read')):
            result = core.scan_folder(self.root)
        self.assertEqual(result['scanned'], 2)
        self.assertEqual(result['size_unique_files'], 2)
        self.assertEqual(result['hashed_files'], 0)

    def test_same_size_different_content_not_duplicate(self):
        self.file('a.mp4', b'AAA')
        self.file('b.mp4', b'BBB')
        result = core.scan_folder(self.root)
        self.assertEqual(result['sampled_files'], 2)
        self.assertEqual(result['hashed_files'], 0)
        self.assertEqual(result['exact_groups'], [])

    def test_sample_equal_does_not_mean_full_equal(self):
        data = b'A'*(1024*1024)
        changed = bytearray(data)
        changed[100000] = ord('B')  # Outside first/middle/last sample ranges.
        a = self.file('a.mp4', data)
        b = self.file('b.mp4', changed)
        self.assertEqual(core.read_video(a, sample=True)['full_hash'], core.read_video(b, sample=True)['full_hash'])
        result = core.scan_folder(self.root)
        self.assertEqual(result['hashed_files'], 2)
        self.assertEqual(result['exact_groups'], [])

    def test_overlap_never_creates_self_duplicate(self):
        self.file('parent/child/a.mp4')
        for roots in ([self.root/'parent', self.root/'parent/child'], [self.root/'parent/child', self.root/'parent']):
            result = core.scan_folders(roots)
            self.assertEqual(result['scanned'], 1)
            self.assertEqual(result['exact_groups'], [])

    def test_empty_nonvideo_and_case_extension(self):
        self.file('empty.mp4', b'')
        self.file('normal.MP4')
        self.file('other.txt')
        result = core.scan_folder(self.root)
        self.assertEqual(result['scanned'], 2)
        self.assertEqual(result['valid'], 1)
        self.assertEqual(len(result['issues']), 1)

    def test_bad_roots_and_read_failure(self):
        for roots in ([], [''], [self.root/'missing']):
            with self.assertRaises(ValueError):
                core.scan_folders(roots)
        self.file('a.mp4')
        self.file('b.mp4')
        with patch.object(core, 'read_video', side_effect=PermissionError('file busy')):
            result = core.scan_folder(self.root)
        self.assertEqual(len(result['issues']), 2)
        self.assertEqual(result['valid'], 0)

    def test_cancel_during_streaming(self):
        path = self.file('large.mp4', b'A'*(core.CHUNK+100))
        cancel = threading.Event()
        with self.assertRaises(core.Cancelled):
            core.read_video(path, cancel, progress=lambda *_: cancel.set())
        with self.assertRaises(core.Cancelled):
            core.scan_folder(self.root, cancel)

    def test_changed_since_inventory_rejected(self):
        path = self.file('a.mp4')
        expected = core.stat_key(path.stat())
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '变化'):
            core.read_video(path, expected=expected)

    def pair(self):
        source = self.file('A/loose/mine.mp4')
        reference = self.file('B/author/series/original.mp4')
        return source, reference, core.scan_folders([self.root/'A', self.root/'B'])

    def test_report_roundtrip_and_type_separation(self):
        source, reference, result = self.pair()
        report = self.root/'video.txt'
        report.write_text(backend.report_text(result), encoding='utf-8-sig')
        source.unlink()
        reference.unlink()
        with patch.object(core, 'read_video', side_effect=AssertionError('loading must not hash videos')):
            loaded = backend.load_report(report)
        self.assertEqual(loaded['exact_groups'], json.loads(json.dumps(result['exact_groups'])))
        with self.assertRaises(ValueError):
            load_script_report(report)
        report.write_text('Funscript 查重报告', encoding='utf-8')
        with self.assertRaises(ValueError):
            backend.load_report(report)

    def test_sample_or_bad_size_report_rejected(self):
        _, _, result = self.pair()
        for field, value in [('hash_kind', 'sample'), ('size', 9999), ('byte_hash', '0'*64)]:
            bad = copy.deepcopy(result)
            bad['exact_groups'][0][0][field] = value
            with self.assertRaises(ValueError):
                backend.validate_report(bad)

    def test_mirror_move_verified_original_name_and_log(self):
        source, reference, result = self.pair()
        output = self.root/'output'
        plan = backend.build_plan(result, self.root/'A', self.root/'B', output)
        self.assertTrue(source.exists())
        self.assertFalse(output.exists())
        summary = backend.execute_plan(plan)
        self.assertEqual(summary['moved'], 1)
        self.assertFalse(source.exists())
        self.assertEqual((output/'author/series/mine.mp4').read_bytes(), reference.read_bytes())
        self.assertTrue(Path(summary['log_path']).name.startswith('视频移动记录_'))

    def test_changed_video_is_not_moved(self):
        source, reference, result = self.pair()
        plan = backend.build_plan(result, self.root/'A', self.root/'B', self.root/'output')
        source.write_bytes(b'changed video contents')
        summary = backend.execute_plan(plan)
        self.assertEqual(summary['moved'], 0)
        self.assertTrue(source.exists() and reference.exists())
        self.assertFalse(Path(plan[0]['target']).exists())

    def test_existing_video_never_overwritten(self):
        source, reference, result = self.pair()
        output = self.root/'output'
        plan = backend.build_plan(result, self.root/'A', self.root/'B', output)
        target = Path(plan[0]['target'])
        target.parent.mkdir(parents=True)
        target.write_bytes(b'user target')
        summary = backend.execute_plan(plan)
        self.assertEqual(summary['failed'], 1)
        self.assertEqual(target.read_bytes(), b'user target')
        self.assertTrue(source.exists() and reference.exists())


if __name__ == '__main__':
    unittest.main()
