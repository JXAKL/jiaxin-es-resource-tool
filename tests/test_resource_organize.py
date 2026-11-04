import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from organizer.resource_organize import (Source, OrganizeOptions, scan_sources, match_rows, prepare,
    execute, choose, month_of, mark_collisions)
from organizer.resource_organize_report import export_report
from organizer.forum import topic_from_json, Cancelled
from organizer.matching import MatchingSession
from organizer.store import Store


def topic(title='Aurora Harbor Midnight',identity=1,date='2024-01-31T18:00:00Z',author='Alpha'):
    return topic_from_json(dict(id=identity,title=title,created_at=date,
        post_stream=dict(posts=[dict(post_number=1,username=author,created_at=date,
            cooked=f'<p>Video is free; script is paid.</p><p><a href="https://iwara.tv/video/Fixture">video</a></p>'
                   f'<p><a href="https://mega.nz/folder/Test#Secret">script</a></p>'
                   f'<p><a href="/uploads/short-url/file.funscript">{title}.funscript</a></p>')])) )


class ResourceOrganizeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'source';self.source.mkdir()
        self.options=OrganizeOptions([Source(str(self.source))],'Alpha',self.root/'output',workers=1)

    def file(self,name,root=None,data=None):
        path=(root or self.source)/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data or (b'{"actions":[]}' if path.suffix=='.funscript' else b'synthetic-video'))
        return path

    def state(self,topics=None):
        groups,rows=scan_sources(self.options.sources)
        match_rows(groups,rows,topics or [topic()],self.options)
        return dict(author='Alpha',output=str(self.options.output),sources=[vars(s) for s in self.options.sources],
                    status='preview',rows=rows,failed={},report_folder=str(self.root/'report'))

    def test_sources_filter_axes_and_external_vs_missing(self):
        self.file('Aurora Harbor Midnight.mp4')
        for suffix in ('','.pitch','_R2',' [A0]','.valve'):
            self.file('axes/Aurora Harbor Midnight'+suffix+'.funscript')
        singles=self.root/'singles';singles.mkdir()
        self.file('Dawn Harbor Morning.mp4',singles)
        self.file('Do Not Read.funscript',singles,data=b'{"metadata":{"title":"wrong evidence"}}')
        self.options.sources += [Source(str(singles),'videos','external')]
        groups,rows=scan_sources(self.options.sources)
        self.assertEqual(len(rows),2)
        self.assertEqual(len(rows[0]['files']),6)
        self.assertEqual(rows[0]['missing'],'')
        self.assertTrue(rows[1]['external']);self.assertEqual(rows[1]['missing'],'')
        self.assertEqual(groups[1].hints,[])
        self.options.sources[1].pairing='missing'
        self.assertEqual(scan_sources(self.options.sources)[1][1]['missing'],'script')

    def test_script_only_no_base_axis_stays_one_group(self):
        for suffix in ('.R1','.sway','.L0'):self.file('Aurora Harbor Midnight'+suffix+'.funscript')
        self.file('Ignored Video.mp4')
        self.options.sources=[Source(str(self.source),'scripts','missing')]
        _,rows=scan_sources(self.options.sources)
        self.assertEqual(len(rows),1);self.assertEqual(len(rows[0]['files']),3)
        self.assertEqual(rows[0]['missing'],'video')

    def test_overlap_subdirectory_rules_own_files_and_metadata(self):
        sub=self.source/'nested';sub.mkdir()
        self.file('Aurora Harbor Midnight.mp4')
        self.file('Aurora Harbor Midnight.pitch.funscript',sub,data=b'{"metadata":{"title":"Excluded metadata"}}')
        self.options.sources.append(Source(str(sub),'scripts','external'))
        groups,rows=scan_sources(self.options.sources)
        self.assertEqual(len(rows),2);self.assertEqual(groups[0].hints,[])
        self.assertEqual(sum(len(r['files']) for r in rows),2)
        self.assertEqual(rows[0]['missing'],'script');self.assertTrue(rows[1]['external'])

    def test_date_timezone_unknown_and_conflicting_months(self):
        self.file('Aurora Harbor Midnight.mp4')
        self.assertEqual(month_of('2024-12-31T18:00:00Z')[0],'2025/01')
        self.assertEqual(month_of('invalid'),('',''))
        posts=[topic(),topic(identity=2,date='2024-03-02T00:00:00Z')]
        row=self.state(posts)['rows'][0]
        self.assertFalse(row['selected']);self.assertIn('日期不同',row['status'])
        for policy,month in [('earliest','2024/02'),('latest','2024/03')]:
            self.options.date_policy=policy
            row=self.state(posts)['rows'][0]
            self.assertTrue(row['selected']);self.assertEqual(row['month'],month)
        row=self.state([topic(date='')])['rows'][0]
        self.assertFalse(row['selected']);self.assertIn('发布日期',row['status'])

    def test_matching_payment_and_all_resource_links(self):
        self.file('Aurora Harbor Midnight 1080p.mp4')
        state=self.state();row=state['rows'][0];chosen=row['candidates'][0]
        self.assertTrue(row['selected']);self.assertEqual(row['month'],'2024/02')
        self.assertEqual((chosen['payment']['script'],chosen['payment']['video']),('付费','免费'))
        self.assertEqual(len(chosen['resources']),3)
        self.assertIn('Aurora Harbor Midnight.funscript',chosen['filenames'])
        export_report(state)
        self.assertIn('#Secret',(self.root/'report/下载用链接/mega.nz.txt').read_text(encoding='utf-8'))
        self.assertTrue((self.root/'report/下载用链接/iwara.tv.txt').read_text(encoding='utf-8').startswith('#'))
        self.assertIn('2024-02',(self.root/'report/资源综合整理报告.txt').read_text(encoding='utf-8'))

    def test_only_requested_author_and_episode_conflicts(self):
        self.file('Aurora Harbor Midnight Episode 1.mp4')
        row=self.state([topic('Aurora Harbor Midnight Episode 1',author='Other'),
                        topic('Aurora Harbor Midnight Episode 2',identity=2)])['rows'][0]
        self.assertFalse(row['selected'])
        self.assertTrue(all(c['author']=='Alpha' for c in row['candidates']))

    def test_copy_all_axes_preserves_sources_and_hashes(self):
        files=[self.file('Aurora Harbor Midnight'+s) for s in ('.mp4','.funscript','.pitch.funscript','.A0.funscript')]
        state=self.state();result=execute(state,'copy')
        row=result['rows'][0]
        self.assertEqual(row['status'],'复制完成')
        for original in files:
            target=self.options.output/'Alpha/2024/02'/original.name
            self.assertEqual(original.read_bytes(),target.read_bytes())
        self.assertTrue(all(o['status']=='完成' for o in row['operations']))
        self.assertEqual(len(row['operations']),4)
        self.assertTrue(Path(result['last_journal']).is_file())

    def test_move_and_journal_can_restore(self):
        from organizer.moves import undo
        source=self.file('Aurora Harbor Midnight.mp4');original=source.read_bytes()
        result=execute(self.state(),'move')
        self.assertFalse(source.exists())
        self.assertEqual(undo(Path(result['last_journal']))['restored'],1)
        self.assertEqual(source.read_bytes(),original)

    def test_collision_skips_entire_group_and_never_overwrites(self):
        self.file('Aurora Harbor Midnight.mp4');self.file('Aurora Harbor Midnight.pitch.funscript')
        state=self.state();target=self.options.output/'Alpha/2024/02/Aurora Harbor Midnight.mp4'
        target.parent.mkdir(parents=True);target.write_bytes(b'keep')
        result=execute(state,'move')
        self.assertIn('同名',result['rows'][0]['status'])
        self.assertEqual(target.read_bytes(),b'keep')
        self.assertEqual(len(list(self.source.iterdir())),2)
        self.assertFalse(result['rows'][0]['operations'])

    def test_conflicting_sources_do_not_silently_choose_winner(self):
        other=self.root/'second';other.mkdir()
        self.file('Aurora Harbor Midnight.mp4');self.file('Aurora Harbor Midnight.mp4',other)
        self.options.sources.append(Source(str(other),'videos','external'))
        state=self.state();mark_collisions(state)
        self.assertTrue(all(r['collision'] for r in state['rows']))
        state['rows'][1]['selected']=False
        result=execute(state,'copy')
        self.assertEqual(result['rows'][0]['status'],'复制完成')
        self.assertTrue((other/'Aurora Harbor Midnight.mp4').is_file())

    def test_changed_source_and_changed_target_rejected(self):
        source=self.file('Aurora Harbor Midnight.mp4');state=self.state()
        source.write_bytes(b'changed after preview')
        self.assertIn('变化',execute(state,'move')['rows'][0]['status'])
        state=self.state();state['rows'][0]['target']=str(self.root/'outside')
        self.assertIn('目标已改变',execute(state,'copy')['rows'][0]['status'])
        self.assertTrue(source.is_file())

    def test_stop_retains_completed_files_and_report(self):
        self.file('Aurora Harbor Midnight.mp4');self.file('Aurora Harbor Midnight.pitch.funscript')
        flag=[False]
        def check():
            if flag[0]:raise Cancelled()
        from organizer.resource_organize import checked_copy
        def copied(*args):
            checked_copy(*args);flag[0]=True
        with patch('organizer.resource_organize.checked_copy',side_effect=copied):
            result=execute(self.state(),'copy',check)
        self.assertIn('停止',result['status']);self.assertEqual(len(result['rows'][0]['operations']),1)
        self.assertEqual(result['rows'][0]['operations'][0]['status'],'完成')
        self.assertTrue((self.root/'report/资源综合整理报告.json').is_file())
        self.assertEqual(len(list(self.source.iterdir())),2)

    def test_stop_matching_retains_completed_decisions(self):
        self.file('Aurora Harbor Midnight.mp4');self.file('Dawn Harbor Morning.mp4')
        groups,rows=scan_sources(self.options.sources);flag=[False]
        def check():
            if flag[0]:raise Cancelled()
        original=MatchingSession._score_jobs
        def score(session,jobs,stop,heartbeat,finished):
            def done(n):finished(n);flag[0]=True
            return original(session,jobs,stop,heartbeat,done)
        with patch.object(MatchingSession,'_score_jobs',score):
            stopped,_=match_rows(groups,rows,[topic(),topic('Dawn Harbor Morning',2)],self.options,check=check)
        self.assertTrue(stopped)
        self.assertEqual(sum(r['selected'] for r in rows),1)
        self.assertTrue(any('停止' in r['status'] for r in rows))

    def test_new_module_really_uses_parallel_workers(self):
        from organizer.parallel_check import parallel_fixture
        groups,topics=parallel_fixture()
        self.file(groups[0].title+'.mp4')
        groups,rows=scan_sources(self.options.sources)
        self.options.workers=2
        _,work=match_rows(groups,rows,[replace(t,author='Alpha') for t in topics],self.options)
        self.assertEqual(sum(p['pairs'] for p in work['processes'].values()),32)
        self.assertEqual(len(work['processes']),2,work)
        self.assertTrue(all(p['cpu_seconds']>0 for p in work['processes'].values()),work)

    def test_interrupted_copy_removes_only_its_temporary_file(self):
        from organizer.resource_organize import checked_copy
        from organizer.moves import digest
        source=self.file('Aurora Harbor Midnight.mp4',data=b'x'*(9*1024*1024))
        target=self.root/'copy/target.mp4';calls=[0]
        def check():
            calls[0]+=1
            if calls[0]==2:raise Cancelled()
        with self.assertRaises(Cancelled):checked_copy(source,target,digest(source),check)
        self.assertTrue(source.is_file());self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.iterdir()),[])

    def test_local_preview_no_network_and_report_created(self):
        self.file('Aurora Harbor Midnight.mp4')
        store=Store(self.root/'state/catalog.sqlite3');self.addCleanup(store.close);store.upsert(topic())
        browser=Mock(side_effect=AssertionError('must not access network'))
        result=prepare(self.options,store,browser,self.root/'state',local_only=True)
        browser.assert_not_called();self.assertTrue(result['rows'][0]['selected'])
        self.assertTrue((Path(result['report_folder'])/'资源综合整理报告.html').is_file())

    def test_collection_failure_keeps_cached_preview_and_error(self):
        self.file('Aurora Harbor Midnight.mp4')
        store=Store(self.root/'state/catalog.sqlite3');self.addCleanup(store.close);store.upsert(topic())
        result=prepare(self.options,store,Mock(side_effect=OSError('offline')),self.root/'state')
        self.assertIn('offline',result['status']);self.assertTrue(result['rows'][0]['selected'])

    def test_report_write_failure_still_publishes_actual_file_outcome(self):
        self.file('Aurora Harbor Midnight.mp4');state=self.state();events=[]
        with patch('organizer.resource_organize_report.export_report',side_effect=OSError('report locked')):
            result=execute(state,'copy',emit=lambda *args:events.append(args))
        self.assertEqual(result['rows'][0]['status'],'复制完成')
        self.assertIn('report locked',result['status'])
        self.assertEqual(events[-1][0],'organized')
        self.assertEqual(events[-1][1]['rows'][0]['operations'][0]['status'],'完成')

    def test_invalid_sources_and_output_nesting(self):
        for change in [dict(sources=[]),dict(output=self.source/'out'),dict(threshold=101),
                       dict(sources=[Source(str(self.source),'bad')]),
                       dict(sources=[Source(str(self.source)),Source(str(self.source))])]:
            with self.subTest(change=change),self.assertRaises(ValueError):replace(self.options,**change).validate()


if __name__=='__main__':unittest.main()
