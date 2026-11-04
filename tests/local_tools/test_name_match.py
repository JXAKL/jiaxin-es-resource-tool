import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.local_tools.name_match import compare_names, name_similarity
from organizer.local_tools import smart_match
class NameScoreTests(unittest.TestCase):
    def score(self, left, right):
        return name_similarity(left+'.mp4', right+'.funscript')

    def test_exact_is_full_score_for_any_script(self):
        for name in ('同名视频', '[完整标题]', 'sample', 'sample123', 'かなカナ', 'Пример', '☀♥', 'title.part.01'):
            with self.subTest(name=name):
                self.assertEqual(self.score(name, name), 1.)
        self.assertEqual(self.score('SAMPLE', 'sample'), 1.)
        self.assertEqual(name_similarity('other/标题.mp4', 'scripts/标题.funscript'), 1.)

    def test_normalized_equivalence_not_false_exact(self):
        self.assertEqual(self.score('ＡＢＣ　标题', 'abc 标题'), .99)
        self.assertEqual(self.score('标题_scene-01', '标题.scene_01'), .99)
        self.assertLess(self.score('标题一', '标题二'), 1.)

    def test_keep_bracket_titles_and_episodes(self):
        self.assertLess(self.score('[完整标题]', '完全无关作品'), .2)
        result = compare_names('标题 [第01集].mp4', '标题 [第02集].funscript')
        self.assertTrue(result['conflict'])
        self.assertLessEqual(result['score'], .45)
        self.assertGreater(self.score('[完整标题]', '完整标题'), .9)

    def test_technical_tags_not_identifiers(self):
        self.assertEqual(self.score('作品 1080p x265', '作品 2160p h264'), .97)
        self.assertTrue(compare_names('作品 101.mp4', '作品 102.funscript')['conflict'])

    def test_identifier_prefix_episode_and_version_conflicts(self):
        for left, right in [('AB-12345','CD-12345'), ('作品 v1', '作品 v2'),
                            ('作品 第一集', '作品 第二集'), ('scene 01 part 2', 'scene 02 part 1')]:
            with self.subTest(left=left):
                result = compare_names(left+'.mp4', right+'.funscript')
                self.assertTrue(result['conflict'])
                self.assertLessEqual(result['score'], .45)

    def test_missing_numbers_no_fixed_penalty(self):
        self.assertEqual(self.score('舞蹈作品', '舞蹈作品'), 1.)
        self.assertEqual(self.score('The Sunset Dance', 'Dance The Sunset'), .94)
        self.assertGreater(self.score('中文标题完整', '中文标题完美'), .5)

    def test_id_only_bridge_is_not_automatic_certainty(self):
        self.assertEqual(self.score('123456', '[作者] 标题 123456'), .86)
        self.assertLess(self.score('123457', '[作者] 标题 123456'), .5)

    def test_score_symmetric_and_bounded(self):
        for left, right in [('舞蹈作品','舞蹈作业'), ('alpha beta','beta alpha'), ('id123','id124'), ('!!!','???')]:
            self.assertEqual(self.score(left, right), self.score(right, left))
            self.assertGreaterEqual(self.score(left, right), 0.)
            self.assertLessEqual(self.score(left, right), 1.)


class CandidateSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='name-matching-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.videos, self.scripts = self.root/'视频', self.root/'脚本'
        self.videos.mkdir()
        self.scripts.mkdir()
        self.durations = {}

    def video(self, stem, duration=60):
        path = self.videos/(stem+'.mp4')
        path.touch()
        self.durations[str(path)] = duration

    def script(self, stem, duration=60, padding=0):
        path = self.scripts/(stem+'.funscript')
        path.write_text(json.dumps({'actions': [{'at': 0, 'pos': 0}, {'at': 1000, 'pos': 100}],
                                    'metadata': {'duration': duration}, 'padding': 'x'*padding}), encoding='utf-8')

    def match(self, names=True, duration=True, low=80):
        with patch.object(smart_match, 'get_video_durations', return_value=self.durations):
            return smart_match.match_files(str(self.videos), str(self.scripts), 95, low, names, duration)

    def test_exact_chinese_and_bracket_names_not_filtered_out(self):
        for name in ('中文标题', '[完整标题]', 'sample'):
            self.video(name)
            self.script(name)
        rows = self.match(duration=False)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r['name_score'] == 100 for r in rows))
        self.assertTrue(all(r['status'] == '已同名（保留）' for r in rows))

    def test_same_name_preserved_despite_duration_mismatch_or_missing(self):
        self.video('原来整理好的', 600)
        self.script('原来整理好的', 60)
        self.script('不对应但时长相同', 600, padding=5000)
        rows = self.match()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['funscript'], '原来整理好的.funscript')
        self.assertEqual(rows[0]['status'], '时长冲突待核对')
        self.assertFalse(rows[0]['auto_eligible'])
        self.assertEqual(rows[0]['name_score'], 100)
        self.assertEqual(rows[0]['duration_score'], 0)

    def test_name_score_visible_but_not_used_in_duration_only(self):
        self.video('中文标题')
        self.script('中文标题')
        row = self.match(names=False)[0]
        self.assertEqual(row['name_score'], 100)
        self.assertIn('仅供参考', row['name_reason'])

    def test_large_unrelated_file_does_not_displace_better_title(self):
        self.video('夕阳下的舞蹈 1080p')
        self.script('夕阳下的舞蹈')
        self.script('夕阳下的舞蹈续篇', padding=10000)
        row = self.match(low=60)[0]
        self.assertEqual(row['funscript'], '夕阳下的舞蹈.funscript')

    def test_largest_recognized_variant_still_wins(self):
        self.video('夕阳下的舞蹈 1080p')
        self.script('夕阳下的舞蹈 [easy]')
        self.script('夕阳下的舞蹈 [hard]', padding=5000)
        row = self.match()[0]
        self.assertEqual(row['funscript'], '夕阳下的舞蹈 [hard].funscript')
        self.assertIn('变体 2 份', row['name_reason'])

    def test_close_competitors_require_review_even_with_high_combined_score(self):
        self.video('alpha beta gamma delta epsilon')
        self.script('beta alpha gamma delta epsilon')
        self.script('alpha beta gamma epsilon delta', padding=5000)
        row = self.match()[0]
        self.assertGreaterEqual(row['total'], 95)
        self.assertEqual(row['status'], '多脚本候选待核对')
        self.assertTrue(row['runner_up'])
        self.assertAlmostEqual(row['candidate_margin'], 0)

    def test_number_conflict_cannot_become_auto_by_duration(self):
        self.video('AB-12345')
        self.script('CD-12345')
        row = self.match(low=0)[0]
        self.assertEqual(row['status'], '编号冲突待核对')
        self.assertLessEqual(row['name_score'], 45)

    def test_name_confidence_required_not_only_combined_score(self):
        self.video('alpha beta gamma')
        self.script('beta alpha gamma')
        row = self.match()[0]
        self.assertGreaterEqual(row['total'], 95)
        self.assertEqual(row['status'], '待核对')


if __name__ == '__main__':
    unittest.main()
