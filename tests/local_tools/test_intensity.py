import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.local_tools.script_intensity import grade_script, grade_rows, grade_batch, percentile
from organizer.local_tools.smart_match import process_match, write_csv


class IntensityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='intensity-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root/'样本.funscript'

    def script_data(self, points, duration=None):
        data = {'actions': [dict(at=t, pos=p) for t, p in points]}
        if duration is not None:
            data['metadata'] = {'duration': duration}
        self.script.write_text(json.dumps(data), encoding='utf-8')
        return grade_script(self.script)

    def row(self, name='原视频.mp4', multi=False):
        video = self.root/name
        video.write_bytes(b'synthetic-video-content')
        return dict(video_path=str(video), funscript_path=str(self.script), multi_match=multi,
                    video=video.name, funscript=self.script.name, name_score=100., duration_score=100.,
                    total=100., video_duration=2., script_duration=2., status='人工确认')

    def test_hand_calculation(self):
        s = self.script_data([(0, 0), (1000, 100), (2000, 0)])
        self.assertEqual((s['average'], s['maximum'], s['action_count'], s['movement_count'], s['reversals']),
                         (100, 100, 3, 2, 1))
        self.assertAlmostEqual(s['score'], 43.333333333)
        self.assertEqual(s['folder'], '03_中等强度')

    def test_waits_and_declared_duration(self):
        s = self.script_data([(1000, 0), (2000, 100)], 4)
        self.assertEqual(s['average'], 25)
        self.assertEqual(s['active_average'], 100)
        self.assertEqual(s['active_duration'], 1)
        self.assertEqual(s['coverage'], .25)
        self.assertAlmostEqual(s['score'], 43.333333333)
        self.assertEqual(s['maximum'], 100)
        s = self.script_data([(1000, 0), (2000, 100)], .5)
        self.assertEqual(s['average'], 50)
        self.assertTrue(s['notes'])

    def test_sort_dedup_and_linear_refinement(self):
        a = self.script_data([(0, 0), (1000, 100)])
        b = self.script_data([(1000, 100), (500, 50), (0, 0), (500, 50)])
        self.assertEqual(a['score'], b['score'])
        self.assertEqual(b['unique_points'], 3)
        self.assertEqual(b['action_count'], 4)

    def test_invalid_points(self):
        cases = [[(0, 0), (0, 100)], [(0, 0)], [(-1, 0), (1000, 100)],
                 [(0, 0), (1000, 101)], [(0, 0), (float('nan'), 50)]]
        for points in cases:
            with self.subTest(points=points), self.assertRaises(ValueError):
                self.script_data(points)

    def test_invalid_root_and_reference(self):
        self.script.write_text('[]')
        with self.assertRaises(ValueError):
            grade_script(self.script)
        self.script_data([(0, 0), (1000, 100)])
        for ref in (0, -10, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                grade_script(self.script, ref, 600)

    def test_all_boundaries_and_static(self):
        for speed, folder in [(0,'00_静止'), (19,'01_低强度'), (20,'02_较低强度'),
                              (40,'03_中等强度'), (60,'04_较高强度'), (80,'05_高强度'), (100,'05_高强度')]:
            self.script_data([(0, 0), (1000, speed)])
            s = grade_script(self.script, 100, 100)
            self.assertAlmostEqual(s['score'], speed)
            self.assertEqual(s['folder'], folder)

    def test_peak_cap(self):
        s = self.script_data([(0, 0), (1, 100), (100000, 100)])
        self.assertEqual(s['maximum'], 100000)
        self.assertAlmostEqual(s['score'], 100)
        self.assertEqual(s['active_duration'], .001)
        self.assertIn('不代表全片', s['notes'])
        self.assertTrue(s['notes'])

    def test_classification_copy_and_move_same_names(self):
        self.script_data([(0, 0), (1000, 100), (2000, 0)])
        one, two = self.row('一.mp4', True), self.row('二.mp4', True)
        output = self.root/'输出'
        dest1 = Path(process_match(one, str(output), classify=True))
        dest2 = Path(process_match(two, str(output), classify=True, move=True))
        self.assertEqual(dest1.name, '样本.mp4')
        self.assertEqual(dest2.name, dest1.name)
        self.assertNotEqual(dest1.parent, dest2.parent)
        self.assertTrue(dest1.is_relative_to(output/'03_中等强度'))
        self.assertEqual(dest1.read_bytes(), dest2.read_bytes())
        self.assertTrue(Path(one['video_path']).exists())
        self.assertFalse(Path(two['video_path']).exists())
        self.assertTrue(self.script.exists())

    def test_unclassified_and_collision(self):
        self.script_data([(0, 0), (1000, 100)])
        row = self.row()
        output = self.root/'输出'
        dest1 = Path(process_match(row, str(output)))
        dest2 = Path(process_match(row, str(output)))
        self.assertEqual(dest1.parent, output)
        self.assertNotEqual(dest1, dest2)
        self.assertEqual(dest1.name, dest2.name)

    def test_ungradable_quarantine(self):
        self.script.write_text('{broken')
        row = self.row()
        dest = Path(process_match(row, str(self.root/'输出'), classify=True))
        self.assertEqual(dest.parent.name, '99_无法评估')
        self.assertTrue(row['intensity']['error'])

    def test_refresh_and_csv(self):
        self.script_data([(0, 0), (1000, 100)])
        row = self.row()
        grade_rows([row])
        self.script_data([(0, 50), (1000, 50)])
        dest = process_match(row, str(self.root/'输出'), classify=True)
        self.assertEqual(row['intensity']['folder'], '00_静止')
        report = self.root/'report.csv'
        write_csv([row], report)
        with report.open(encoding='utf-8-sig', newline='') as handle:
            data = list(csv.DictReader(handle))[0]
        self.assertEqual(data['运动强度等级'], '00_静止')
        self.assertEqual(data['评分版本'], 'motion-v2-active')
        self.assertEqual(float(data['有效运动平均速度（位置点/秒）']), 0)
        self.assertEqual(float(data['运动时间占比%']), 0)
        self.assertEqual(data['输出路径'], dest)

    def test_already_in_destination_no_duplicate(self):
        self.script_data([(0, 0), (1000, 100)])
        row = self.row()
        output = self.root/'输出'
        dest = process_match(row, str(output), classify=True)
        row['video_path'] = dest
        again = process_match(row, str(output), classify=True, move=True)
        self.assertEqual(dest, again)

    def test_identical_motion_with_long_blanks_keeps_grade(self):
        short = self.script_data([(0, 0), (1000, 100), (2000, 0)])
        blank = self.script_data([(60000, 0), (61000, 100), (360000, 100), (361000, 0)], 720)
        self.assertEqual(short['score'], blank['score'])
        self.assertEqual(short['folder'], blank['folder'])
        self.assertEqual(blank['active_average'], 100)
        self.assertEqual(blank['active_duration'], 2)
        self.assertAlmostEqual(blank['average'], 200/720)
        self.assertAlmostEqual(blank['coverage'], 2/720)

    def test_long_gap_with_different_positions_is_excluded(self):
        s = self.script_data([(0, 0), (1000, 100), (301000, 0), (302000, 100)])
        self.assertEqual(s['active_distance'], 200)
        self.assertEqual(s['active_duration'], 2)
        self.assertEqual(s['gap_count'], 1)
        self.assertEqual(s['gap_duration'], 300)
        self.assertEqual(s['active_average'], 100)
        self.assertAlmostEqual(s['score'], 43.333333333)

    def test_threshold_disable_and_all_excluded(self):
        s = self.script_data([(0, 0), (20000, 100)])
        self.assertIsNone(s['score'])
        self.assertEqual(s['folder'], '99_无法评估')
        self.assertTrue(s['error'])
        for threshold in (0, 20):
            s = grade_script(self.script, max_gap=threshold)
            self.assertEqual(s['active_average'], 5)
            self.assertEqual(s['active_duration'], 20)
            self.assertEqual(s['gap_count'], 0)
            self.assertEqual(s['folder'], '01_低强度')
        row = self.row()
        dest = Path(process_match(row, str(self.root/'输出'), classify=True, max_gap=0))
        self.assertEqual(dest.parent.name, '01_低强度')
        self.assertEqual(row['intensity']['max_gap'], 0)
        for threshold in (-1, float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                grade_script(self.script, max_gap=threshold)

    def test_time_weighted_active_average_not_segment_mean(self):
        s = self.script_data([(0, 0), (1000, 100), (3000, 0)])
        self.assertAlmostEqual(s['active_average'], 200/3)
        self.assertEqual(s['coverage'], 1)

    def batch(self, speeds, prefix='batch'):
        rows = []
        for index, speed in enumerate(speeds):
            script = self.root/f'{prefix}-{index}.funscript'
            script.write_text(json.dumps({'actions': [{'at': 0, 'pos': 0},
                                                       {'at': 100000/speed, 'pos': 100}]}))
            row = self.row(f'{prefix}-{index}.mp4')
            row.update(funscript_path=str(script), funscript=script.name)
            rows.append(row)
        return rows

    def test_relative_batch_scale_invariant(self):
        slow = self.batch([20, 40, 60, 80, 100], 'slow')
        fast = self.batch([200, 400, 600, 800, 1000], 'fast')
        for rows in (slow, fast):
            cohort = grade_batch(rows)
            self.assertEqual(cohort['count'], 5)
            self.assertEqual([r['intensity']['score'] for r in rows], [0, 25, 50, 75, 100])
        self.assertNotEqual(slow[2]['intensity']['absolute_score'], fast[2]['intensity']['absolute_score'])

    def test_relative_duplicate_videos_do_not_change_ranking(self):
        rows = self.batch([20, 40, 60])
        one = grade_batch(rows)
        scores = [r['intensity']['score'] for r in rows]
        expanded = rows + [dict(rows[0]) for _ in range(12)]
        two = grade_batch(expanded)
        self.assertEqual(one['id'], two['id'])
        self.assertEqual(two['count'], 3)
        self.assertEqual(scores, [r['intensity']['score'] for r in expanded[:3]])

    def test_relative_ties_and_extreme_outliers(self):
        self.assertEqual(percentile(20, [10, 20, 20, 30]), 50)
        rows = self.batch([20, 20, 20])
        grade_batch(rows)
        self.assertEqual([r['intensity']['score'] for r in rows], [50, 50, 50])
        self.assertTrue(all(r['intensity']['folder'] == '03_中等强度' for r in rows))
        rows = self.batch([20, 40, 60, 80, 100000], 'extreme')
        grade_batch(rows)
        self.assertEqual(rows[2]['intensity']['score'], 50)

    def test_relative_static_invalid_single_and_disable(self):
        rows = self.batch([20])
        self.script_data([(0, 50), (1000, 50)])
        rows.append(self.row('static.mp4'))
        broken = self.row('bad.mp4')
        broken['funscript_path'] = str(self.root/'missing.funscript')
        rows.append(broken)
        cohort = grade_batch(rows)
        self.assertEqual(cohort['count'], 1)
        self.assertEqual(rows[0]['intensity']['score'], rows[0]['intensity']['absolute_score'])
        self.assertIn('样本不足', rows[0]['intensity']['basis'])
        self.assertEqual(rows[1]['intensity']['folder'], '00_静止')
        self.assertEqual(rows[2]['intensity']['folder'], '99_无法评估')
        self.assertIsNone(grade_batch(rows, relative=False))
        self.assertEqual(rows[0]['intensity']['basis'], '绝对强度')

    def test_relative_frozen_cohort_used_for_subset_and_report(self):
        rows = self.batch([20, 40, 60, 80, 100])
        cohort = grade_batch(rows)
        selected = rows[-1]
        dest = Path(process_match(selected, str(self.root/'输出'), classify=True, cohort=cohort))
        self.assertEqual(dest.parent.name, '05_高强度')
        self.assertEqual(selected['intensity']['score'], 100)
        self.assertEqual(selected['intensity']['cohort_count'], 5)
        report = self.root/'relative.csv'
        write_csv(rows, report)
        with report.open(encoding='utf-8-sig', newline='') as handle:
            record = list(csv.DictReader(handle))[-1]
        self.assertEqual(record['评分模式'], '批内相对')
        self.assertEqual(record['评分批次标识'], cohort['id'])
        self.assertEqual(record['批内有效脚本数'], '5')

    def test_relative_changed_script_requires_refresh_before_copy(self):
        rows = self.batch([20, 40, 60])
        cohort = grade_batch(rows)
        Path(rows[0]['funscript_path']).write_text(json.dumps({'actions': [{'at': 0, 'pos': 0},
                                                                           {'at': 1000, 'pos': 50}]}))
        output = self.root/'输出'
        with self.assertRaisesRegex(ValueError, '刷新整批'):
            process_match(rows[0], str(output), classify=True, cohort=cohort)
        self.assertFalse(output.exists())

    def test_relative_regrade_preserves_processed_results(self):
        rows = self.batch([20, 40, 60])
        grade_batch(rows)
        rows[0]['status'] = '已处理'
        saved = dict(rows[0]['intensity'])
        grade_batch(rows, relative=False)
        self.assertEqual(rows[0]['intensity'], saved)
        self.assertEqual(rows[1]['intensity']['basis'], '绝对强度')


if __name__ == '__main__':
    unittest.main()
