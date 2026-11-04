import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.local_tools.funscript_duplicates.core import read_script, scan_folder, scan_folders, report_text, Cancelled


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='funscript-duplicates-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = dict(actions=[{'at': 0, 'pos': 0}, {'at': 1000, 'pos': 100}],
                         inverted=False, range=90, metadata={'title': '标题', 'duration': 2})

    def file(self, name, data=None, raw=None):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else json.dumps(self.data if data is None else data), encoding='utf-8')
        return path

    def test_recursive_renamed_scripts_and_readonly(self):
        a = self.file('一份.funscript')
        b = self.file('sub/deep/另一名字.FUNSCRIPT')
        self.file('not-script.txt')
        before = {p: p.read_bytes() for p in (a, b)}
        result = scan_folder(self.root)
        self.assertEqual(result['scanned'], 2)
        self.assertEqual(len(result['exact_groups']), 1)
        self.assertEqual(len(result['exact_groups'][0]), 2)
        self.assertEqual(result['metadata_groups'], [])
        self.assertEqual({p: p.read_bytes() for p in (a, b)}, before)

    def test_formatting_keys_encoding_and_numbers_equivalent(self):
        a = self.file('a.funscript')
        alternate = {'metadata': {'duration': 2.0, 'title': '标题'}, 'range': 90.0, 'inverted': False,
                     'actions': [{'pos': 0.0, 'at': 0.0}, {'pos': 100.0, 'at': 1e3}]}
        b = self.file('b.funscript', raw=json.dumps(alternate, ensure_ascii=False, indent=4))
        c = self.root/'bom.funscript'
        c.write_bytes(json.dumps(alternate).encode('utf-16'))
        result = scan_folder(self.root)
        self.assertEqual(len(result['exact_groups'][0]), 3)
        self.assertNotEqual(read_script(a)['byte_hash'], read_script(b)['byte_hash'])

    def test_metadata_not_confused_with_full_duplicates(self):
        self.file('a.funscript')
        self.file('a-copy.funscript')
        changed = copy.deepcopy(self.data)
        changed['metadata']['title'] = '另一标题'
        self.file('b.funscript', changed)
        result = scan_folder(self.root)
        self.assertEqual(len(result['exact_groups']), 1)
        self.assertEqual(len(result['exact_groups'][0]), 2)
        self.assertEqual(len(result['metadata_groups']), 1)
        self.assertEqual(len(result['metadata_groups'][0]), 3)

    def test_same_duration_or_speed_not_enough(self):
        self.file('a.funscript')
        data = copy.deepcopy(self.data)
        data['actions'] = [{'at': 0, 'pos': 100}, {'at': 1000, 'pos': 0}]
        self.file('inverted-positions.funscript', data)
        result = scan_folder(self.root)
        self.assertEqual(result['exact_groups'], [])
        self.assertEqual(result['metadata_groups'], [])

    def test_control_flags_and_unknown_fields_are_preserved(self):
        self.file('original.funscript')
        for key, value in [('inverted', True), ('range', 80), ('custom', 'extra'), ('version', '1.0')]:
            data = copy.deepcopy(self.data)
            data[key] = value
            self.file(key+'.funscript', data)
        result = scan_folder(self.root)
        self.assertEqual(result['exact_groups'], [])
        self.assertEqual(result['metadata_groups'], [])

    def test_string_spaces_array_order_and_boolean_types_preserved(self):
        for i, value in enumerate(['title', 'title ', [1, 2], [2, 1], True, 1, None, '1']):
            data = copy.deepcopy(self.data)
            data['custom'] = value
            self.file(str(i)+'.funscript', data)
        result = scan_folder(self.root)
        self.assertEqual(result['exact_groups'], [])
        self.assertEqual(result['metadata_groups'], [])

    def test_high_precision_numbers_not_rounded_to_false_duplicates(self):
        base = '{"actions":[{"at":0,"pos":0},{"at":1000,"pos":100}],"custom":%s}'
        a = self.file('a.funscript', raw=base % '1.1234567890123456789012345678901')
        b = self.file('b.funscript', raw=base % '1.1234567890123456789012345678902')
        self.assertNotEqual(read_script(a)['full_hash'], read_script(b)['full_hash'])

    def test_invalid_files_reported_not_grouped(self):
        invalid = ['{broken', '{"actions":[],"actions":[]}', '{"actions":[],"x":NaN}',
                   '{"actions":[{"at":true,"pos":0}]}', '{"actions":[{"at":0,"pos":101}]}',
                   '{"actions":[{"at":100,"pos":0},{"at":0,"pos":100}]}',
                   '{"actions":[{"at":0,"pos":0},{"at":0,"pos":100}]}', '[]']
        for i, raw in enumerate(invalid):
            self.file(f'{i}.funscript', raw=raw)
        self.file('good.funscript')
        result = scan_folder(self.root)
        self.assertEqual(result['valid'], 1)
        self.assertEqual(len(result['issues']), len(invalid))
        self.assertEqual(result['exact_groups'], [])

    def test_empty_and_duplicate_points_are_not_silently_removed(self):
        self.file('empty-a.funscript', {'actions': []})
        self.file('empty-b.funscript', {'actions': []})
        self.file('a.funscript')
        changed = copy.deepcopy(self.data)
        changed['actions'].insert(0, dict(changed['actions'][0]))
        self.file('extra-point.funscript', changed)
        result = scan_folder(self.root)
        self.assertEqual(len(result['exact_groups']), 1)
        self.assertEqual(result['exact_groups'][0][0]['actions'], 0)
        self.assertTrue(result['exact_groups'][0][0]['note'])
        self.assertEqual(result['metadata_groups'], [])

    def test_stop_and_read_failure(self):
        self.file('a.funscript')
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            scan_folder(self.root, cancel)
        cancel.clear()
        for i in range(25):
            self.file(f'{i}.funscript')
        def stop(count, valid, path):
            cancel.set()
        with self.assertRaises(Cancelled):
            scan_folder(self.root, cancel, stop)
        with patch('organizer.local_tools.funscript_duplicates.core.read_script', side_effect=PermissionError('test inaccessible')):
            result = scan_folder(self.root)
        self.assertEqual(result['valid'], 0)
        self.assertEqual(len(result['issues']), 26)

    def test_report_contains_all_groups_and_errors(self):
        a = self.file('a.funscript')
        self.file('b.funscript')
        data = copy.deepcopy(self.data)
        data['metadata']['duration'] = 4
        self.file('c.funscript', data)
        self.file('bad.funscript', raw='broken')
        result = scan_folder(self.root)
        report = report_text(result)
        self.assertIn(str(a.resolve()), report)
        self.assertIn('完全重复', report)
        self.assertIn('仅 metadata 不同', report)
        self.assertIn('bad.funscript', report)
        self.assertIn(result['exact_groups'][0][0]['full_hash'], report)

    def test_multiple_folders_find_cross_folder_and_internal_duplicates(self):
        self.file('A/sub/one.funscript')
        self.file('A/two.funscript')
        self.file('B/deep/renamed.funscript')
        self.file('not-selected/outside.funscript')
        result = scan_folders([self.root/'A', self.root/'B'])
        self.assertEqual(result['scanned'], 3)
        self.assertEqual(len(result['exact_groups']), 1)
        self.assertEqual(len(result['exact_groups'][0]), 3)
        self.assertEqual(result['scan_mode'], 'multiple')
        self.assertEqual(len(result['folders']), 2)

    def test_overlapping_roots_never_count_same_file_twice(self):
        self.file('parent/child/only.funscript')
        self.file('parent/outside.funscript', {'actions': []})
        parent, child = self.root/'parent', self.root/'parent/child'
        for roots in ([parent, child], [child, parent], [parent, parent, child]):
            with self.subTest(roots=roots):
                result = scan_folders(roots)
                self.assertEqual(result['scanned'], 2)
                self.assertEqual(result['exact_groups'], [])

    def test_multifolder_same_path_alias_deduplicated(self):
        self.file('parent/child/only.funscript')
        result = scan_folders([self.root/'parent', self.root/'parent/child/..'])
        self.assertEqual(result['scanned'], 1)
        self.assertEqual(len(result['folders']), 1)
        self.assertEqual(result['exact_groups'], [])

    def test_multifolder_validates_every_root(self):
        self.file('a.funscript')
        for roots in ([], [''], [self.root, self.root/'missing']):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                scan_folders(roots)

    def test_single_mode_does_not_scan_sibling_directory(self):
        self.file('A/one.funscript')
        self.file('B/two.funscript')
        result = scan_folder(self.root/'A')
        self.assertEqual(result['scanned'], 1)
        self.assertEqual(result['scan_mode'], 'single')
        self.assertEqual(result['exact_groups'], [])

    def test_multi_report_records_all_roots_and_mode(self):
        self.file('A/one.funscript')
        self.file('B/two.funscript')
        result = scan_folders([self.root/'A', self.root/'B'])
        report = report_text(result)
        self.assertIn('多文件夹联合查重', report)
        for root in result['folders']:
            self.assertIn(root, report)


if __name__ == '__main__':
    unittest.main()
