import copy
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer.local_tools.video_duplicates import core, backend
from organizer.local_tools.video_duplicates.media_probe import probe_videos
from organizer.local_tools.video_duplicates.similarity import options, similar_groups
from organizer.local_tools.surfaces import player_executable
from organizer.video_fixture import make_video
from organizer.evidence import name_similarity as author_similarity
from organizer.local_tools.video_duplicates.names import profile, compare_profiles


class VideoSimilarityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='video-similarity-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def record(self, name, duration=100., size=1000, **extra):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(size))
        return dict(path=str(path),name=path.name,size=size,duration=duration,
                    width=1280,height=720,video_codec='h264',fps=30,container=path.suffix[1:],**extra)

    def scan(self, records, **kwargs):
        metadata = {row['path']:{key:value for key,value in row.items() if key not in ('path','name','size')} for row in records}
        with patch.object(core,'probe_videos',return_value=metadata):
            return core.scan_folder(self.root, **kwargs)

    def test_transcodes_different_sizes_survive_duration_then_title_filter(self):
        a=self.record('A/Aurora Moonlight Dance 1080p.mp4',100.,1000)
        b=self.record('B/Aurora Moonlight Dance 720p.mkv',100.7,2000)
        result=self.scan([a,b])
        self.assertEqual(len(result['similar_groups']),1)
        self.assertEqual(result['exact_groups'],[])
        row=result['similar_groups'][0][1]
        self.assertGreaterEqual(row['name_score'],95)
        self.assertIn('文件大小接近度',row['note'])
        self.assertIn('编码',row['note'])
        self.assertAlmostEqual(row['duration_delta'],.7)

    def test_author_rules_not_local_pairing_weights_or_broad_catalog_filter(self):
        # Both previously missed at the default 90: loose catalog extraction
        # rejected the first pair; a different fuzzy formula lowered the second.
        pairs = [
            ('Aurora Harbor Midnight 01', 'Aurora Harbor Midnight 1 [Collection 159]'),
            ('ExtraordinaryCreator BeautifulPerformance Midnight',
             'ExtraordinaryCreator BeautifulPerformance Midnigth'),
            ('SkyArtist Aurora Harbor Midnight', 'Aurora Harbor Midnight by SkyArtist'),
            ('(SkyArtist) Midnight', '(SkyArtist) Aurora Harbor Midnight'),
            ('Aurora Harbor Midnight', '[Creator] [CS-PAID-0008] Aurora Harbor Midnight [multi-axis] [New update]'),
            ('青空放課後のタイム 第01話', '[作者] 青空放課後のタイム 第1話 [更新]'),
        ]
        for left, right in pairs:
            for reverse in (False, True):
                with self.subTest(left=left, reverse=reverse):
                    # Exercise either reference direction through duration sorting.
                    a = self.record('A/'+left+'.mp4', 100.5 if reverse else 100., 1000)
                    b = self.record('B/'+right+'.mkv', 100. if reverse else 100.5, 2000)
                    groups, count = similar_groups([a,b])
                    self.assertEqual((len(groups), count), (1,1))
                    wanted = max(author_similarity(left,right)[0],author_similarity(right,left)[0])
                    self.assertAlmostEqual(groups[0][1]['name_score'], wanted*100, places=3)

    def test_shared_matching_conflict_guards_and_source_identity(self):
        for left, right in [
                ('Aurora Harbor Episode 1','[Creator] Aurora Harbor Episode 2'),
                ('Aurora Harbor 第三話','Aurora Harbor 第四話'),
                ('Aurora Harbor v1','Aurora Harbor v2'),
                ('Aurora Harbor ABP123','Aurora Harbor ABP124'),
                ('Aurora Harbor Midnight','Aurora Harbor Midnight Extended'),
                ('Aurora Harbor [f5eM423lJsAG2y]','Aurora Harbor [mraT9qCLMmnDyn]'),
                ('Aurora Harbor Episode 1 [f5eM423lJsAG2y]', 'Aurora Harbor Episode 2 [f5eM423lJsAG2y]')]:
            with self.subTest(left=left,right=right):
                evidence = compare_profiles(profile(left+'.mp4'),profile(right+'.mp4'))
                self.assertTrue(evidence['conflict'],evidence)
                self.assertFalse(similar_groups([self.record('A/'+left+'.mp4'),
                                               self.record('B/'+right+'.mp4')],{'name_threshold':50})[0])
        self.assertEqual(compare_profiles(profile('First name [f5eM423lJsAG2y].mp4'),
                                          profile('Another title [f5eM423lJsAG2y].mkv'))['score'],.99)

    def test_pruning_never_skips_accepted_shared_evidence(self):
        names = ['Aurora Harbor Midnight '+tail for tail in (
            '01','1','2','1 [Collection 159]','1 [Pack 250]',
            'Part 1','Part 2','Part 1 Volume 2','2 1','1 2','1 1')]
        for index,left in enumerate(names):
            for right in names[index+1:]:
                a=self.record('A/'+left+'.mp4'); b=self.record('B/'+right+'.mp4')
                evidence=compare_profiles(profile(a['path']),profile(b['path']))
                expected=not evidence['conflict'] and evidence['score']>=.9
                self.assertEqual(bool(similar_groups([a,b])[0]),expected,(left,right,evidence))

    def test_new_name_rules_export_reload_and_old_v2_scoring_preserved(self):
        result=self.scan([self.record('A/Aurora Harbor Midnight 01.mp4',size=1000),
                          self.record('B/Aurora Harbor Midnight 1 [Collection 159].mkv',size=2000)])
        self.assertEqual(len(result['similar_groups']),1)
        report=self.root/'new-rules.txt'
        report.write_text(backend.report_text(result),encoding='utf-8-sig')
        loaded=backend.load_report(report)
        self.assertEqual(loaded['similar_groups'],json.loads(json.dumps(result['similar_groups'])))
        # A v2 snapshot keeps its original validator, including its former 97%
        # quality-only score; importing never silently applies new scan rules.
        old=self.scan([self.record('C/SkyArtist Midnight 1080p.mp4',size=3000),
                       self.record('D/SkyArtist Midnight 720p.mkv',size=4000)])
        old['algorithm']='duration-name-info-and-sha256-v2'
        old['similar_groups']=[g for g in old['similar_groups'] if 'SkyArtist' in g[0]['name']]
        report.write_text(backend.report_text(old),encoding='utf-8-sig')
        self.assertEqual(backend.load_report(report)['similar_groups'][0][1]['name_score'],97.)

    def test_same_duration_unrelated_names_and_episode_conflicts_excluded(self):
        records=[self.record('A/Aurora Moonlight Dance Episode 1.mp4',size=1001),
                 self.record('B/Aurora Moonlight Dance Episode 2.mp4',size=1002),
                 self.record('C/Celestial Ocean Sunrise.mp4',size=1003)]
        self.assertEqual(self.scan(records)['similar_groups'],[])

    def test_same_name_outside_duration_window_excluded_and_configurable(self):
        records=[self.record('A/Aurora Moonlight Dance.mp4',100,1000),
                 self.record('B/Aurora Moonlight Dance.mkv',104,2000)]
        self.assertFalse(self.scan(records)['similar_groups'])
        self.assertEqual(len(self.scan(records,settings={'duration_seconds':5})['similar_groups']),1)

    def test_relative_tolerance_and_name_threshold(self):
        a=self.record('A/Aurora Moonlight Dance 1080p.mp4',1000,1000)
        b=self.record('B/Aurora Moonlight Dance 720p.mp4',1004,2000)
        self.assertEqual(len(similar_groups([a,b])[0]),1)
        self.assertFalse(similar_groups([a,b],{'duration_percent':0})[0])
        self.assertFalse(similar_groups([a,b],{'name_threshold':100})[0])

    def test_no_transitive_duration_bridge(self):
        rows=[self.record(f'{n}/Aurora Moonlight Dance.mp4',100+n*1.5,1000+n) for n in range(3)]
        groups,_=similar_groups(rows,{'duration_percent':0})
        self.assertEqual(len(groups),1)
        self.assertEqual(len(groups[0]),2)
        self.assertEqual([r['duration'] for r in groups[0]],[100,101.5])

    def test_exact_groups_not_double_counted_but_three_quality_versions_visible(self):
        a=self.record('A/Aurora Moonlight Dance 1080p.mp4',size=1000)
        b=self.record('B/Aurora Moonlight Dance 720p.mp4',size=1000)
        result=self.scan([a,b])
        self.assertEqual(len(result['exact_groups']),1)
        self.assertFalse(result['similar_groups'])
        c=self.record('C/Aurora Moonlight Dance 4k.mp4',size=2000)
        result=self.scan([a,b,c])
        self.assertEqual(len(result['similar_groups'][0]),3)

    def test_unknown_duration_never_zero_or_false_duplicate(self):
        rows=[self.record('A/Aurora Moonlight Dance.mp4',None,1000),
              self.record('B/Aurora Moonlight Dance.mp4',None,2000)]
        self.assertFalse(self.scan(rows)['similar_groups'])
        rows[1]['size']=1000;Path(rows[1]['path']).write_bytes(bytes(1000))
        self.assertEqual(len(self.scan(rows)['exact_groups']),1)

    def test_new_report_roundtrip_old_report_support_and_no_near_moves(self):
        rows=[self.record('A/Aurora Moonlight Dance.mp4',size=1000),
              self.record('B/Aurora Moonlight Dance.mkv',size=2000)]
        result=self.scan(rows)
        report=self.root/'report.txt';report.write_text(backend.report_text(result),encoding='utf-8-sig')
        loaded=backend.load_report(report)
        self.assertEqual(loaded['similar_groups'],json.loads(json.dumps(result['similar_groups'])))
        self.assertEqual(backend.build_plan(loaded,self.root/'A',self.root/'B',self.root/'out'),[])
        legacy=dict(result,algorithm='size-sample-full-sha256-v1',similar_groups=[])
        for key in ('similar_groups','settings','media_issues'):legacy.pop(key,None)
        payload=dict(format='video-duplicates',version=1,result=legacy)
        report.write_text(backend.START+'\n'+json.dumps(payload)+'\n'+backend.END,encoding='utf-8')
        self.assertEqual(backend.load_report(report)['similar_groups'],[])

    def test_report_tampering_cannot_claim_near_match(self):
        result=self.scan([self.record('A/Aurora Moonlight Dance.mp4',size=1000),
                          self.record('B/Aurora Moonlight Dance.mkv',size=2000)])
        for field,value in [('duration',300),('size',-1),('path',str(self.root/'outside.txt')),('width',float('nan'))]:
            bad=copy.deepcopy(result);bad['similar_groups'][0][1][field]=value
            with self.assertRaises(ValueError):backend.validate_report(bad)
        bad=copy.deepcopy(result)
        bad['similar_groups'][0][1]['path']=str(self.root/'B'/'Unrelated Ocean Sunrise.mkv')
        with self.assertRaises(ValueError):backend.validate_report(bad)

    def test_cancel_and_invalid_settings(self):
        event=threading.Event();event.set()
        with self.assertRaises(core.Cancelled):similar_groups([],cancel=event)
        for values in ({'duration_seconds':-1},{'duration_percent':float('nan')},{'name_threshold':101}):
            with self.assertRaises(ValueError):options(values)

    def test_probe_start_failure_preserves_exact_detection(self):
        self.record('A/Aurora Moonlight Dance.mp4')
        self.record('B/Renamed original.mp4')
        with patch.object(core,'probe_videos',side_effect=OSError('cannot start player')):
            result=core.scan_folder(self.root)
        self.assertEqual(len(result['exact_groups']),1)
        self.assertEqual(len(result['media_issues']),2)
        for field,value in [('duration','invalid'),('fps',float('inf')),('video_codec',{})]:
            bad=copy.deepcopy(result);bad['exact_groups'][0][0][field]=value
            with self.assertRaises(ValueError):backend.validate_report(bad)

    @unittest.skipUnless(player_executable().is_file(),'MPV unavailable')
    def test_actual_transcode_and_recontainer_mp4_mkv(self):
        source=self.root/'Aurora Moonlight Dance 1080p.avi';make_video(source,seconds=3)
        for extension in ('mp4','mkv'):
            target=self.root/f'Aurora Moonlight Dance 720p.{extension}'
            encoded=subprocess.run([str(player_executable()),'--no-config','--audio=no','--ovc=mpeg4',
                                    f'--o={target}',str(source)],stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.assertEqual(encoded.returncode,0,encoded.stdout.decode('utf-8','replace'))
            self.assertTrue(target.is_file())
        result=core.scan_folder(self.root)
        self.assertEqual(result['media_issues'],[])
        self.assertEqual(len(result['similar_groups']),1)
        self.assertEqual(len(result['similar_groups'][0]),3)
        self.assertEqual(result['exact_groups'],[])
        self.assertEqual({item['video_codec'] for item in result['similar_groups'][0]},{'rawvideo','mpeg4'})
        self.assertTrue(all(abs(item['duration']-3)<.1 for item in result['similar_groups'][0]))

    @unittest.skipUnless(player_executable().is_file(),'MPV unavailable')
    def test_actual_mpv_headers_unicode_paths_invalid_file_and_cancel(self):
        good=self.root/'中文 名称.avi';make_video(good,seconds=3,fps=20)
        bad=self.root/'invalid.mp4';bad.write_bytes(b'not a video')
        calls=[]
        result=probe_videos([good,bad,good],progress=lambda *args:calls.append(args))
        self.assertEqual(len(calls),2)
        self.assertAlmostEqual(result[str(good)]['duration'],3.,places=2)
        self.assertEqual(result[str(good)]['width'],160)
        self.assertEqual(result[str(good)]['height'],120)
        self.assertEqual(result[str(good)]['video_codec'],'rawvideo')
        self.assertIn('error',result[str(bad)])
        event=threading.Event()
        with self.assertRaises(core.Cancelled):
            probe_videos([good,bad],event,lambda *_:event.set())


if __name__=='__main__':unittest.main()
