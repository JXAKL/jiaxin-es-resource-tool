import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer.local_tools.name_match import compare_names, profile
from organizer.local_tools.pairing import group_scripts, match_records, pair_candidate, select_confident, PairIndex
from organizer.local_tools.smart_match import match_files, parse_funscript, process_match


def video(name='Aurora Harbor Midnight', duration=60, folder='videos'):
    path = str(Path(folder) / (name + '.mp4'))
    return dict(path=path, name=Path(path).name, name_profile=profile(path), duration=duration)


def script(name='Aurora Harbor Midnight', duration=60, folder='scripts', **kwargs):
    path = str(Path(folder) / (name + '.funscript'))
    return dict(path=path, name=Path(path).name, name_profile=profile(path), duration=duration,
                size=100, duration_source='metadata', **kwargs)


class PairingTests(unittest.TestCase):
    def row(self, v, scripts, **kwargs):
        return match_records([v], scripts, **kwargs)[0]

    def test_missing_duration_does_not_discard_strong_name(self):
        row = self.row(video(duration=None), [script()])
        self.assertEqual(row['name_score'], 100)
        self.assertTrue(row['auto_eligible'])
        self.assertIn('未读到', row['duration_reason'])

    def test_tags_and_source_wrappers_do_not_dilute_name(self):
        row = self.row(video('Iwara - Aurora Harbor Midnight [Source]'),
                       [script('[Creator] Aurora Harbor Midnight [multi-axis]')])
        self.assertTrue(row['auto_eligible'])
        self.assertGreaterEqual(row['total'], 95)

    def test_metadata_name_can_identify_renamed_script(self):
        row = self.row(video(), [script('My renamed work', title='Aurora Harbor Midnight')])
        self.assertTrue(row['auto_eligible'])
        self.assertIn('元数据', row['name_reason'])

    def test_metadata_cannot_erase_explicit_episode_conflict(self):
        row = self.row(video('Aurora Harbor Episode 1'),
                       [script('Aurora Harbor Episode 2', title='Aurora Harbor Episode 1')], low=0)
        self.assertFalse(row['auto_eligible'])
        self.assertEqual(row['status'], '编号冲突待核对')

    def test_multi_axis_group_has_one_representative(self):
        items = [script(), *[script('Aurora Harbor Midnight'+suffix) for suffix in
                            ('.pitch', '.roll', '.valve', '.A1', '_pump', ' [V0]', '．Ｌ３')]]
        grouped = group_scripts(items)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]['path'], items[0]['path'])
        row = self.row(video(), items)
        self.assertEqual(len(row['script_paths']), len(items))
        self.assertTrue(row['auto_eligible'])
        self.assertFalse(row['runner_up'])

    def test_axis_only_uses_base_video_name_when_copying(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input.mp4'; source.write_bytes(b'synthetic video')
            axis = root / 'Aurora Harbor Midnight.pitch.funscript'; axis.write_text('{}')
            dest = process_match({'video_path':str(source),'funscript_path':str(axis)}, str(root/'output'))
            self.assertEqual(Path(dest).name, 'Aurora Harbor Midnight.mp4')
            self.assertTrue(source.exists() and axis.exists())
            self.assertEqual(Path(dest).read_bytes(), b'synthetic video')

    def test_an_overlong_axis_prevents_auto_selection(self):
        row = self.row(video(), [script(), script('Aurora Harbor Midnight.pitch', duration=120)])
        self.assertFalse(row['auto_eligible'])
        self.assertTrue(row['duration_conflict'])

    def test_equal_names_in_different_directories_compete(self):
        row = self.row(video(), [script(folder='authorA'), script(folder='authorB')])
        self.assertFalse(row['auto_eligible'])
        self.assertEqual(row['status'], '多脚本候选待核对')

    def test_exact_filename_does_not_hide_metadata_rival(self):
        row = self.row(video(), [script(), script('Renamed upload', title='Aurora Harbor Midnight')])
        self.assertEqual(row['funscript'], 'Aurora Harbor Midnight.funscript')
        self.assertEqual(row['status'], '多脚本候选待核对')
        self.assertFalse(row['auto_eligible'])

    def test_low_duration_exact_rival_cannot_hide_stronger_metadata_rival(self):
        row = self.row(video(), [script(),script(folder='other',duration=10),
                                script('Renamed upload',title='Aurora Harbor Midnight')])
        self.assertFalse(row['auto_eligible'])
        self.assertIn('Renamed upload', row['runner_up'])

    def test_one_script_claimed_by_two_videos_requires_review(self):
        rows = match_records([video(folder='copy1'), video(folder='copy2')], [script()])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not r['auto_eligible'] and r['multi_match'] for r in rows))

    def test_weak_video_hit_cannot_block_one_exact_correspondence(self):
        rows=match_records([video(),video('Aurora Evening Performance')], [script()],low=0)
        exact=next(r for r in rows if r['video']=='Aurora Harbor Midnight.mp4')
        self.assertTrue(exact['auto_eligible'])
        self.assertTrue(all(not r['auto_eligible'] for r in rows if r is not exact))

    def test_action_end_is_not_total_video_duration(self):
        s = script(duration=40); s['duration_source']='actions'
        self.assertTrue(self.row(video(duration=90), [s])['auto_eligible'])
        self.assertFalse(self.row(video(duration=90), [script(duration=40)])['auto_eligible'])

    def test_only_duration_never_auto_selects(self):
        row = self.row(video(), [script()], use_name=False)
        self.assertFalse(row['auto_eligible'])

    def test_sequel_or_preview_is_not_high_confidence(self):
        for suffix in ('Part 2', 'Extended', 'Preview'):
            row = self.row(video(), [script('Aurora Harbor Midnight '+suffix)], low=0)
            self.assertFalse(row['auto_eligible'])

    def test_whole_identifier_is_case_sensitive(self):
        bad = compare_names('Aurora Harbor [mraT9qCLMmnDyn].mp4',
                            'Aurora Harbor [nraT9qCLMmnDyn].funscript')
        self.assertTrue(bad['conflict'])

    def test_same_high_title_ignores_low_filter_for_rival_check(self):
        # Candidate filtering must not erase a near rival immediately below low.
        groups = group_scripts([script(), script('Aurora Harbor Midnights', folder='other')])
        with patch('organizer.local_tools.pairing.pair_candidate') as scoring:
            original = pair_candidate
            def fake(v, s, *args):
                c = original(v, s, *args)
                c['exact'] = False
                c['total'] = .97 if s['path']==groups[0]['path'] else .949
                return c
            scoring.side_effect = fake
            row = self.row(video('Aurora Harbor Midnight [1080p]'), [script(), script('Aurora Harbor Midnights', folder='other')], low=95)
        self.assertFalse(row['auto_eligible'])
        self.assertTrue(row['runner_up'])

    def test_index_retains_all_exact_rivals_above_fuzzy_limit(self):
        groups = group_scripts([script(folder=f'author{i}') for i in range(100)])
        self.assertEqual(len(PairIndex(groups).shortlist(video(), True, True)), 100)

    def test_auto_selection_is_preview_only_and_preserves_user_decisions(self):
        base = self.row(video(), [script()])
        checked = {**base, 'selected':True}
        unchecked = {**base, 'selected':False, 'selection_touched':True}
        done = {**base, 'status':'已处理', 'selected':False}
        conflict = {**base, 'status':'时长冲突待核对', 'auto_eligible':False}
        rows = [base, checked, unchecked, done, conflict]
        self.assertEqual(select_confident(rows), 1)
        self.assertTrue(base['selected'] and checked['selected'])
        self.assertFalse(unchecked['selected'] or done['selected'] or conflict.get('selected'))
        self.assertEqual(select_confident(rows), 0)

    def test_name_only_does_not_start_duration_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'Aurora Harbor Midnight.mp4').touch()
            (root/'Aurora Harbor Midnight.funscript').write_text('{"actions": []}')
            with patch('organizer.local_tools.smart_match.get_video_durations', side_effect=AssertionError('unnecessary probe')):
                rows=match_files(tmp,tmp,95,80,use_duration=False)
            self.assertTrue(rows[0]['auto_eligible'])

    def test_script_duration_source_and_unsorted_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.funscript'
            path.write_text(json.dumps({'metadata': {'duration': 'NaN'}, 'actions':[{'at':3000},{'at':1000}]}))
            item=parse_funscript(path)
            self.assertEqual(item['duration'],3)
            self.assertEqual(item['duration_source'],'actions')
            path.write_text('[]')
            self.assertIsNone(parse_funscript(path))


if __name__ == '__main__':
    unittest.main()
