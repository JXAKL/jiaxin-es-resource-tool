import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.local_tools.funscript_duplicates.core import scan_folders, scan_folder, report_text
from organizer.local_tools.funscript_duplicates.report_io import START, load_report, validate_report
from organizer.local_tools.funscript_duplicates.organize import build_plan, execute_plan, set_reference


class ReportMoveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='duplicate-report-move-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.a, self.b, self.output = self.root/'移动方', self.root/'参考方', self.root/'指定输出'
        self.a.mkdir()
        self.b.mkdir()
        self.data = {'actions': [{'at': 0, 'pos': 0}, {'at': 1000, 'pos': 100}], 'metadata': {'title': 'sample'}}

    def file(self, path, data=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data or self.data), encoding='utf-8')
        return path

    def pair(self):
        source = self.file(self.a/'散乱目录/原来的名字.funscript')
        reference = self.file(self.b/'作者/系列/另一个名字.funscript')
        result = scan_folders([self.a, self.b])
        return source, reference, result

    def save_report(self, result, legacy=False):
        text = report_text(result)
        if legacy:
            text = text.split(START)[0]
        report = self.root/'报告.txt'
        report.write_text(text, encoding='utf-8-sig')
        return report

    def test_new_and_legacy_reports_load_without_rescanning(self):
        source, reference, result = self.pair()
        for legacy in (False, True):
            path = self.save_report(result, legacy)
            with patch('organizer.local_tools.funscript_duplicates.core.read_script', side_effect=AssertionError('must not rescan')):
                loaded = load_report(path)
            self.assertEqual(loaded['folders'], result['folders'])
            self.assertEqual(loaded['exact_groups'][0][0]['full_hash'], result['exact_groups'][0][0]['full_hash'])
            self.assertEqual(len(loaded['exact_groups'][0]), 2)
        source.unlink()
        reference.unlink()
        loaded = load_report(path)
        self.assertEqual(len(loaded['exact_groups']), 1)

    def test_legacy_original_single_directory_header(self):
        self.pair()
        result = scan_folder(self.root)
        text = report_text(result).split(START)[0]
        text = text.replace(f'扫描模式：单文件夹查重\n扫描目录（1 个；包含子目录，重叠路径去重）：\n  {self.root}',
                            f'扫描目录：{self.root}')
        report = self.root/'old.txt'
        report.write_text(text, encoding='utf-8')
        loaded = load_report(report)
        self.assertEqual(loaded['folders'], [str(self.root)])
        self.assertEqual(len(build_plan(loaded, self.a, self.b, self.output)), 1)

    def test_bad_or_truncated_reports_rejected(self):
        _, _, result = self.pair()
        text = report_text(result).split(START)[0]
        path = self.root/'bad.txt'
        path.write_text(text[:text.index('原字节 SHA256')], encoding='utf-8')
        with self.assertRaises(ValueError):
            load_report(path)
        changed = copy.deepcopy(result)
        changed['exact_groups'][0][0]['full_hash'] = '0'*64
        with self.assertRaises(ValueError):
            validate_report(changed)
        changed = copy.deepcopy(result)
        changed['exact_groups'][0][0]['path'] = str(self.root/'outside.funscript')
        with self.assertRaises(ValueError):
            validate_report(changed)

    def test_preview_no_write_and_move_mirrors_reference_preserves_filename(self):
        source, reference, result = self.pair()
        data = source.read_bytes()
        plan = build_plan(result, self.a, self.b, self.output)
        self.assertFalse(self.output.exists())
        self.assertTrue(source.exists())
        expected = self.output/'作者/系列/原来的名字.funscript'
        self.assertEqual(plan[0]['target'], str(expected))
        summary = execute_plan(plan)
        self.assertEqual(summary['moved'], 1)
        self.assertFalse(source.exists())
        self.assertEqual(expected.read_bytes(), data)
        self.assertEqual(reference.read_bytes(), data)
        log = Path(summary['log_path']).read_text()
        entries = [json.loads(line) for line in log.splitlines()]
        self.assertTrue(any(item.get('event') == '已移动' and item['source'] == str(source)
                            and item['target'] == str(expected) for item in entries))
        self.assertFalse(list(self.output.rglob('*.tmp')))

    def test_direction_can_be_reversed(self):
        source, reference, result = self.pair()
        plan = build_plan(result, self.b, self.a, self.output)
        summary = execute_plan(plan)
        self.assertEqual(summary['moved'], 1)
        self.assertTrue(source.exists())
        self.assertFalse(reference.exists())
        self.assertTrue((self.output/'散乱目录/另一个名字.funscript').exists())

    def test_changed_source_or_reference_blocks_move(self):
        for which in ('source', 'reference'):
            with self.subTest(which=which):
                source, reference, result = self.pair()
                plan = build_plan(result, self.a, self.b, self.output)
                changed = source if which == 'source' else reference
                changed.write_text(json.dumps(self.data, indent=4))  # even formatting change invalidates saved bytes
                summary = execute_plan(plan)
                self.assertEqual(summary['moved'], 0)
                self.assertEqual(summary['failed'], 1)
                self.assertTrue(source.exists())
                self.assertTrue(reference.exists())
                self.assertFalse(Path(plan[0]['target']).exists())

    def test_missing_source_is_reported_without_touching_reference(self):
        source, reference, result = self.pair()
        plan = build_plan(result, self.a, self.b, self.output)
        source.unlink()
        summary = execute_plan(plan)
        self.assertEqual(summary['failed'], 1)
        self.assertTrue(reference.exists())

    def test_existing_target_never_overwritten(self):
        source, _, result = self.pair()
        plan = build_plan(result, self.a, self.b, self.output)
        target = Path(plan[0]['target'])
        target.parent.mkdir(parents=True)
        target.write_bytes(b'preexisting user data')
        summary = execute_plan(plan)
        self.assertEqual(summary['moved'], 0)
        self.assertEqual(target.read_bytes(), b'preexisting user data')
        self.assertTrue(source.exists())

    def test_multiple_reference_locations_require_explicit_choice(self):
        source, reference, _ = self.pair()
        alternate = self.file(self.b/'另一位置/copy.funscript')
        result = scan_folders([self.a, self.b])
        plan = build_plan(result, self.a, self.b, self.output)
        self.assertFalse(plan[0]['selected'])
        self.assertFalse(plan[0]['target'])
        set_reference(plan[0], str(alternate))
        self.assertEqual(execute_plan(plan)['moved'], 1)
        self.assertTrue((self.output/'另一位置'/source.name).exists())
        self.assertTrue(reference.exists() and alternate.exists())

    def test_same_folder_references_unambiguous(self):
        _, reference, _ = self.pair()
        self.file(reference.with_name('copy.funscript'))
        plan = build_plan(scan_folders([self.a, self.b]), self.a, self.b, self.output)
        self.assertTrue(plan[0]['selected'])

    def test_collisions_skipped_until_only_one_selected(self):
        source, _, _ = self.pair()
        other = self.file(self.a/'another'/source.name)
        plan = build_plan(scan_folders([self.a, self.b]), self.a, self.b, self.output)
        self.assertTrue(all(not row['selected'] for row in plan))
        for row in plan:
            row['selected'] = True
        summary = execute_plan(plan)
        self.assertEqual(summary['moved'], 0)
        self.assertEqual(summary['failed'], 2)
        self.assertTrue(source.exists() and other.exists())
        plan[1]['selected'] = False
        self.assertEqual(execute_plan(plan)['moved'], 1)

    def test_metadata_only_duplicates_not_moved(self):
        source, reference, _ = self.pair()
        data = copy.deepcopy(self.data)
        data['metadata']['title'] = 'different'
        self.file(reference, data)
        self.assertEqual(build_plan(scan_folders([self.a, self.b]), self.a, self.b, self.output), [])
        self.assertTrue(source.exists())

    def test_unsafe_roots_and_target_escape_rejected(self):
        source, _, result = self.pair()
        for src, ref, out in [(self.a, self.a, self.output), (self.a, self.b, self.a/'out'),
                              (self.a, self.b, self.root), (self.root, self.b, self.output)]:
            with self.assertRaises(ValueError):
                build_plan(result, src, ref, out)
        plan = build_plan(result, self.a, self.b, self.output)
        plan[0]['target'] = str(self.root/'escape.funscript')
        summary = execute_plan(plan)
        self.assertEqual(summary['moved'], 0)
        self.assertFalse((self.root/'escape.funscript').exists())
        self.assertTrue(source.exists())

    def test_directory_link_guard(self):
        self.pair()
        result = scan_folders([self.a, self.b])
        with patch('organizer.local_tools.funscript_duplicates.organize.os.path.isjunction', side_effect=lambda path: Path(path) == self.output):
            with self.assertRaises(ValueError):
                build_plan(result, self.a, self.b, self.output)

    def test_failed_copy_preserves_source_and_cleans_own_temp(self):
        source, _, result = self.pair()
        plan = build_plan(result, self.a, self.b, self.output)
        with patch('organizer.local_tools.funscript_duplicates.organize.shutil.copystat', side_effect=OSError('simulated disk problem')):
            summary = execute_plan(plan)
        self.assertEqual(summary['failed'], 1)
        self.assertTrue(source.exists())
        self.assertFalse(Path(plan[0]['target']).exists())
        self.assertFalse(list(self.output.rglob('*.tmp')))

    def test_source_unlink_failure_keeps_both_and_logs(self):
        source, _, result = self.pair()
        plan = build_plan(result, self.a, self.b, self.output)
        original = Path.unlink
        def deny_source(path, *args, **kwargs):
            if path == source:
                raise PermissionError('source busy')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', deny_source):
            summary = execute_plan(plan)
        self.assertEqual(summary['moved'], 0)
        self.assertTrue(source.exists())
        self.assertEqual(Path(plan[0]['target']).read_bytes(), source.read_bytes())
        self.assertIn('保留源文件和目标副本', plan[0]['state'])

    def test_cancellation_and_selected_only(self):
        self.pair()
        second = self.file(self.a/'other.funscript')
        plan = build_plan(scan_folders([self.a, self.b]), self.a, self.b, self.output)
        cancel = threading.Event()
        summary = execute_plan(plan, cancel, lambda done, *_: cancel.set() if done >= 1 else None)
        self.assertEqual(summary['moved'], 1)
        self.assertTrue(summary['stopped'])
        self.assertEqual(sum(Path(row['source']['path']).exists() for row in plan), 1)


if __name__ == '__main__':
    unittest.main()
