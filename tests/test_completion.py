import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from organizer.completion import CompletionOptions, complete_resources, inventory, build_rows, export_report
from organizer.forum import topic_from_json, Cancelled, TopicUnavailable
from organizer.model import Topic, FileGroup, LocalFile
from organizer.resource_info import payment_info, resource_links
from organizer.store import Store


def post(identity=1, title='Aurora Harbor Midnight', body='', **kw):
    return topic_from_json(dict(id=identity, title=title, post_stream=dict(posts=[
        dict(post_number=1, username='Alpha', cooked=body),
        dict(post_number=2, username='Beta', cooked='<p>Paid video <a href="https://gofile.io/d/Reply">video</a></p>')]), **kw))


def group(title='Aurora Harbor Midnight', kind='video'):
    return FileGroup(title, title, [LocalFile(title + ('.mp4' if kind == 'video' else '.funscript'), 1, 1, kind)])


class ResourceInfoTests(unittest.TestCase):
    def test_first_post_relative_attachments_quotes_and_keys(self):
        t = post(body='<p>Free script <a href="/uploads/short-url/test.funscript?x=1&amp;y=2">Aurora Harbor Midnight.funscript</a></p>'
                      '<p><a href="https://mega.nz/folder/Test#Secret">video</a></p>'
                      '<blockquote>Paid video<a href="https://gofile.io/d/Quote">video</a></blockquote>')
        resources = resource_links(t)
        self.assertEqual([r['kind'] for r in resources], ['script', 'video'])
        self.assertEqual(resources[0]['url'], 'https://discuss.eroscripts.com/uploads/short-url/test.funscript?x=1&y=2')
        self.assertTrue(resources[1]['url'].endswith('#Secret'))
        p = payment_info(t)
        self.assertEqual(p['script'], '免费'); self.assertEqual(p['video'], '未知')

    def test_paid_category_only_applies_to_script(self):
        p = payment_info(post(body='<p>Free video available</p>'), 'Scripts / Paid Scripts')
        self.assertEqual((p['script'], p['video'], p['tag']), ('付费', '免费', '付费'))
        self.assertTrue(any(s.startswith('分类') for s in p['evidence']))

    def test_both_paid_and_mixed_clauses(self):
        self.assertEqual(payment_info(post(body='<p>Video and script are paid</p>'))['tag'], '视频与脚本付费')
        p = payment_info(post(body='<p>Free video, paid script.</p>'))
        self.assertEqual((p['script'], p['video']), ('付费', '免费'))
        p = payment_info(post(body='<p>The video is paid and the script is free.</p>'))
        self.assertEqual((p['script'], p['video']), ('免费', '付费'))

    def test_purchase_link_alone_is_not_proof(self):
        t = post(body='<a href="https://www.patreon.com/creator">Support my work</a>')
        self.assertEqual(payment_info(t)['script'], '未知')
        self.assertIn('待核实', payment_info(t)['tag'])
        self.assertEqual(resource_links(t), [])

    def test_missing_links_not_paid_and_free_negation(self):
        self.assertEqual(payment_info(post())['tag'], '付费状态未知')
        self.assertEqual(payment_info(post(body='<p>The script is not paid</p>'))['script'], '免费')
        self.assertEqual(payment_info(post(body='<p>The video is not free</p>'))['video'], '付费')

    def test_opposing_evidence_preserved(self):
        p = payment_info(post(body='<p>Free script sample. Full script is paid.</p>'))
        self.assertEqual(p['script'], '混合/需核实')

    def test_video_link_not_offered_for_missing_script(self):
        t = post(body='<p><a href="https://www.iwara.tv/video/AbC/name">Video</a></p>'
                      '<p><a href="https://gofile.io/d/File">Script</a></p>')
        row = build_rows([group()], [t], 85)[0]
        self.assertTrue(row['candidates'][0]['confirmed'])
        self.assertEqual([r['kind'] for r in row['candidates'][0]['resources']], ['script'])

    def test_episode_conflict_cannot_confirm(self):
        t = post(title='Aurora Harbor Episode 2', body='<p>Paid script</p>')
        row = build_rows([group('Aurora Harbor Episode 1')], [t], 50)[0]
        self.assertFalse(any(c['confirmed'] for c in row['candidates']))

    def test_later_free_release_not_truncated_by_eight_paid_posts(self):
        posts = [post(i,body='<p>Paid script</p>') for i in range(1,10)]
        posts.append(post(10,body='<p>Free script <a href="/uploads/free.funscript">Aurora Harbor Midnight.funscript</a></p>'))
        row = build_rows([group()], posts, 85)[0]
        self.assertEqual(len(row['candidates']),10)
        self.assertTrue(any(c['id']==10 and c['confirmed'] for c in row['candidates']))

    def test_matching_stop_keeps_finished_rows_available(self):
        count=[0];done=[]
        def stop():
            if done:
                raise Cancelled('stop after first complete row')
        with self.assertRaises(Cancelled):
            build_rows([group(),group('Second Harbor Morning')], [post()], 85, stop=stop, on_row=done.append)
        self.assertEqual(len(done),1)

    def test_unread_failure_not_confirmed_from_cached_links(self):
        t = post(body='<a href="https://gofile.io/d/File">script</a>')
        row = build_rows([group()], [t], 85, failed={'1':dict(error='403')})[0]
        self.assertFalse(row['candidates'][0]['confirmed'])
        self.assertIn('失败', row['status'])

    def test_legacy_topic_loads_with_defaults(self):
        self.assertFalse(Topic(**dict(id=1, title='Old', author='Alpha', url='https://discuss.eroscripts.com/t/1')).resource_details_loaded)


class CompletionFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.media = self.root / 'media'; self.media.mkdir()
        self.store = Store(self.root/'catalog.db'); self.addCleanup(self.store.close)
        self.client = Mock(); self.client.__enter__ = Mock(return_value=self.client); self.client.__exit__ = Mock(return_value=False)
        self.calls = []
        def get(endpoint, params=None):
            self.calls.append(endpoint)
            if endpoint == '/categories.json':
                return {'category_list':{'categories':[{'id':55, 'name':'Paid Scripts'}]}}
            if endpoint == '/u/Alpha.json':
                return {'user':{'username':'Alpha'}}
            if endpoint == '/user_actions.json':
                return {'user_actions': [] if params['offset'] else [dict(action_type=4, username='Alpha', topic_id=1, post_id=1, title='Aurora Harbor Midnight')]}
            if endpoint == '/t/1.json':
                return dict(id=1, title='Aurora Harbor Midnight', category_id=55, post_stream=dict(posts=[dict(post_number=1, username='Alpha', cooked='<p>Free video <a href="https://www.iwara.tv/video/AbC/name">video</a></p><p><a href="https://patreon.com/creator/posts/1">Paid script</a></p>')]))
            raise AssertionError(endpoint)
        self.client.get.side_effect = get
        self.client.read_web.side_effect = get

    def file(self, name, content='{}'):
        path = self.media/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
        return path

    def run_job(self, **kw):
        return complete_resources(CompletionOptions(self.media, 'Alpha', self.root/'out', **kw),
                                  self.store, lambda: self.client, self.root/'state')

    def test_recursive_groups_axes_and_strong_aliases(self):
        self.file('a/Aurora Harbor Midnight.mp4')
        self.file('b/[Creator] Aurora Harbor Midnight.funscript')
        self.file('b/[Creator] Aurora Harbor Midnight.pitch.funscript')
        self.file('c/Other Harbor Morning.funscript')
        self.file('d/Another Harbor Evening.mp4')
        groups, missing = inventory(self.media)
        self.assertEqual(len(groups), 3)
        self.assertEqual(len(missing), 2)
        self.assertEqual(len(next(g for g in groups if len(g.files) == 3).files), 3)

    def test_screenshot_axes_one_video_and_seven_scripts_are_one_complete_group(self):
        self.file('1080p60fps.mp4')
        for axis in ('', '.pitch', '.roll', '.surge', '.sway', '.twist', '.valve'):
            self.file('1080p60fps'+axis+'.funscript')
        groups,missing=inventory(self.media)
        self.assertEqual((len(groups),len(missing)),(1,0))
        self.assertEqual(len(groups[0].files),8)

    def test_full_channel_family_without_main_script_and_with_different_subfolders(self):
        from organizer.script_axes import AXIS_NAMES
        title='Aurora Harbor Midnight'
        self.file('video/'+title+'.mp4')
        axes=sorted(AXIS_NAMES)+['L0','L3','R2','V0','V1','A0','A1','A2','vib2']
        for axis in axes:
            self.file(f'scripts/{axis}/{title}.{axis}.funscript')
        groups,missing=inventory(self.media)
        self.assertEqual((len(groups),len(missing)),(1,0))
        self.assertEqual(len(groups[0].files),len(axes)+1)
        (self.media/'video'/f'{title}.mp4').unlink()
        groups,missing=inventory(self.media)
        self.assertEqual((len(groups),len(missing)),(1,1))
        self.assertEqual(len(missing[0].files),len(axes))

    def test_main_script_does_not_hide_video_from_remaining_decorated_axis_group(self):
        title='Aurora Harbor Midnight'
        self.file(title+'.mp4');self.file(title+'.funscript')
        self.file('nested/[Creator] '+title+'.valve.funscript')
        self.file('nested/[Creator] '+title+' [A1].funscript')
        self.file('nested/Aurora Harbor Episode 2.valve.funscript')
        groups,missing=inventory(self.media)
        self.assertEqual((len(groups),len(missing)),(2,1))
        self.assertEqual(len(next(g for g in groups if len(g.files)==4).files),4)
        self.assertEqual(missing[0].title,'Aurora Harbor Episode 2')

    def test_no_axis_false_gaps_never_opens_browser_or_exports_download_links(self):
        self.file('Aurora Harbor Midnight.mp4')
        for axis in ('pitch','valve','vib','pump','suck','A2','L3'):
            self.file('nested/Aurora Harbor Midnight.'+axis+'.funscript')
        factory=Mock(side_effect=AssertionError('Completed axis group must not fetch posts'))
        result=complete_resources(CompletionOptions(self.media,'Alpha',self.root/'out'),self.store,factory,self.root/'state')
        factory.assert_not_called()
        self.assertEqual((result['total'], result['rows']),(1,[]))
        saved=json.loads((Path(result['report_folder'])/'资源补齐报告.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['rows'],[])

    def test_no_missing_never_opens_browser(self):
        self.file('Aurora Harbor Midnight.mp4'); self.file('Aurora Harbor Midnight.pitch.funscript')
        factory = Mock(side_effect=AssertionError('Must not open'))
        state = complete_resources(CompletionOptions(self.media,'Alpha',self.root/'out'),self.store,factory,self.root/'state')
        factory.assert_not_called(); self.assertEqual(state['rows'], [])
        self.assertTrue((Path(state['report_folder'])/'资源补齐报告.html').is_file())

    def test_end_to_end_export_payment_cache_reuse_and_no_mutation(self):
        path = self.file('Aurora Harbor Midnight.mp4', 'synthetic')
        state = self.run_job()
        row = state['rows'][0]
        self.assertEqual(row['missing_label'], '缺脚本')
        self.assertEqual(row['payment'], '付费')
        c = row['candidates'][0]; self.assertTrue(c['confirmed'])
        self.assertEqual(c['payment']['script'], '付费'); self.assertEqual(c['payment']['video'], '免费')
        folder = Path(state['report_folder'])
        self.assertFalse((folder/'按域名链接/patreon.com.txt').is_file())
        self.assertFalse((folder/'按域名链接/iwara.tv.txt').exists())
        self.assertEqual(path.read_text(), 'synthetic')
        first_calls = list(self.calls)
        second = self.run_job()
        self.assertEqual(first_calls, self.calls)
        self.assertNotEqual(second['report_folder'], state['report_folder'])
        self.assertEqual(len(list(folder.glob('资源补齐报告.*'))), 4)
        self.assertEqual(json.loads((folder/'资源补齐报告.json').read_text(encoding='utf-8'))['rows'][0]['payment'], '付费')

    def test_old_cache_gets_new_metadata_once(self):
        old = post(); old.resource_details_loaded = False
        self.store.upsert(old); self.store.author_progress('Alpha',1,True)
        self.file('Aurora Harbor Midnight.mp4')
        self.run_job()
        self.assertIn('/t/1.json', self.calls)
        self.assertTrue(self.store.topic(1).resource_details_loaded)

    def test_new_context_keeps_old_topic_json_readable(self):
        t = post(body='<p>Paid script <a href="https://patreon.com/creator">Buy</a></p>')
        self.store.upsert(t)
        raw = self.store.db.execute('SELECT data FROM topics WHERE id=1').fetchone()[0]
        fields = json.loads(raw)
        self.assertNotIn('resource_links', fields)
        self.assertEqual(self.store.topic(1).resource_links, t.resource_links)
        # An old application may change the topic without knowing the new table.
        fields['title'] = 'Updated by older version'
        self.store.db.execute('UPDATE topics SET data=? WHERE id=1', (json.dumps(fields),))
        self.store.db.commit()
        self.assertFalse(self.store.topic(1).resource_details_loaded)

    def test_stop_exports_partial_report_without_more_requests(self):
        self.file('Aurora Harbor Midnight.mp4')
        self.client.check.side_effect = Cancelled('fixture stop')
        state = self.run_job()
        self.assertIn('停止', state['status'])
        self.assertTrue((Path(state['report_folder'])/'资源补齐报告.txt').is_file())
        self.assertNotIn('/t/1.json', self.calls)

    def test_inaccessible_post_does_not_disappear_or_become_paid(self):
        self.file('Aurora Harbor Midnight.mp4')
        original = self.client.get.side_effect
        self.client.get.side_effect = lambda endpoint, params=None: (_ for _ in ()).throw(TopicUnavailable('404')) if endpoint=='/t/1.json' else original(endpoint,params)
        state = self.run_job()
        self.assertIn('1', state['failed'])
        self.assertEqual(len(state['rows']), 1)
        self.assertIn('部分', state['status'])
        self.assertEqual(state['rows'][0]['payment'], '尚未确认对应帖子')

    def test_reports_escape_markup_and_excel_formulas(self):
        rows=build_rows([group('=<script>alert(1)</script>')], [], 85)
        folder=self.root/'export'
        export_report(dict(author='Alpha',source=str(self.media),total=1,status='测试',rows=rows,failed={}), folder)
        self.assertNotIn('<script>', (folder/'资源补齐报告.html').read_text(encoding='utf-8'))
        self.assertIn("'=<script>", (folder/'资源补齐报告.csv').read_text(encoding='utf-8-sig'))


if __name__ == '__main__': unittest.main()
