import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from organizer.completion import CompletionOptions, complete_resources, inventory
from organizer.local_tools.name_match import compare_names, profile
from organizer.local_tools.pairing import group_scripts
from organizer.model import scan, file_title
from organizer.resource_organize import Source, scan_sources, execute
from organizer.resource_variants import parts
from organizer.script_axes import AXIS_NAMES
from organizer.store import Store
from tests import test_resource_organize as fixtures


class ResourceVariantTests(unittest.TestCase):
    setUp = fixtures.ResourceOrganizeTests.setUp
    file = fixtures.ResourceOrganizeTests.file
    state = fixtures.ResourceOrganizeTests.state

    def test_video_and_script_variants_are_one_work_with_nested_axes(self):
        for name in ('test.mp4', 'test(max).mp4', 'test(v2).mkv', 'test.funscript',
                     'test(max).funscript', 'test(max).pitch.funscript',
                     'test.roll(max).funscript', 'test(v2).funscript', 'test(v2).pitch.funscript'):
            self.file(name)
        groups, missing = inventory(self.source)
        self.assertEqual((len(groups), len(missing)), (1, 0))
        self.assertEqual(len(groups[0].files), 9)
        scripts = {v['version']: v['files'] for v in groups[0].versions if v['kind'] == 'script'}
        self.assertEqual({k: len(v) for k,v in scripts.items()}, {'默认版本': 1, 'max': 3, 'v2': 2})
        self.assertEqual(sum(bool(f['axes']) for f in scripts['max']), 2)
        self.assertEqual(groups[0].display_name, 'test.funscript')
        self.assertEqual(len(scan([str(self.source)], '', sync_search=False)), 2)

    def test_suffixes_and_all_axes_in_either_order(self):
        title = 'Aurora Harbor Episode 02'
        for label in ('max','MIN','v2','ver 1.2','version3','rev2','alt','alternate','variant A',
                      '100%','speed 1.5x','轻度','强力','差分B','高强度'):
            for axis in sorted(AXIS_NAMES) + ['L0','R2','V1','A2','vib3']:
                for stem in (f'{title}({label}).{axis}', f'{title}.{axis}({label})',
                             f'{title}【{label}】_{axis}', f'{title}（{label}）．{axis}'):
                    with self.subTest(stem=stem):
                        p = parts(stem+'.funscript')
                        self.assertEqual(p.work, title)
                        self.assertEqual(len(p.versions), 1)
                        self.assertTrue(p.axes)
                        self.assertNotIn(axis, p.title)
        for stem in (title+'_max',title+'.v1.2',title+' v2',title+'(max) [1080p]',title+'(max) 1080p'):
            self.assertEqual(len(parts(stem+'.mp4').versions), 1, stem)
        self.assertEqual(file_title(Path('test.pitch(max).funscript')), 'test(max)')
        self.assertEqual(parts('test.pitch(max).mp4').work, 'test.pitch')
        download=parts('Iwara - Aurora(max).pitch [mraT9qCLMmnDyn] [Source].funscript')
        self.assertEqual(download.work, 'Iwara - Aurora [mraT9qCLMmnDyn] [Source]')
        self.assertEqual(download.version, 'max')
        self.assertTrue(download.axes)

    def test_unknown_words_episode_ids_and_edits_are_not_guessed_away(self):
        names = ('Mad Max','Aurora (Part 2)','Aurora (02)','Aurora [AbC123def]',
                 'Aurora (uncut)','Aurora (extended)','Aurora (preview)',
                 'Aurora.roll.custom','Aurora.valve2','Aurora.R10','max')
        for name in names:
            self.assertEqual(parts(name+'.funscript').work, name)
        for name in ('Aurora Episode 1(max).funscript','Aurora Episode 2.mp4',
                     'Aurora [AbC123def](max).funscript','Aurora [XyZ456def].mp4'):
            self.file(name)
        self.assertEqual(len(scan([str(self.source)], '')), 4)

    def test_quality_only_families_remain_directory_scoped(self):
        for sub in ('one','two'):
            for suffix in ('.mp4','(max).funscript','(max).pitch.funscript'):
                self.file(sub+'/1080p60fps'+suffix)
        groups, missing = inventory(self.source)
        self.assertEqual((len(groups),len(missing)), (2,0))

    def test_missing_versions_reported_once_and_complete_families_skip_network(self):
        for suffix in ('.funscript','(max).funscript','(max).pitch.funscript','(v2).roll.funscript'):
            self.file('Aurora Harbor Midnight'+suffix)
        self.assertEqual(len(inventory(self.source)[1]), 1)
        self.file('Aurora Harbor Midnight.mp4')
        with _store(self.root/'store.db') as store:
            factory = Mock(side_effect=AssertionError('Complete work must not request missing variants'))
            result = complete_resources(CompletionOptions(self.source,'Alpha',self.root/'out'),
                                        store, factory, self.root/'state')
        factory.assert_not_called()
        self.assertEqual((result['total'], result['rows']), (1,[]))
        report = Path(result['report_folder'])
        saved = json.loads((report/'资源补齐报告.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['inventory'][0]['versions']), 4)
        for ext in ('txt','html'):
            content=(report/('资源补齐报告.'+ext)).read_text(encoding='utf-8')
            self.assertIn('(max).pitch.funscript', content)
            self.assertIn('本地作品版本清单', content)

    def test_linked_sources_attach_all_versions_only_for_primary_work(self):
        for primary in ('videos','scripts'):
            with self.subTest(primary=primary), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);a=root/'a';b=root/'b';a.mkdir();b.mkdir()
                videos,scripts=(a,b) if primary=='videos' else (b,a)
                for name in ('Aurora Harbor Midnight.mp4','Aurora Harbor Midnight(v2).mp4'):
                    self.file(name,videos)
                for name in ('Aurora Harbor Midnight(max).funscript',
                             'Aurora Harbor Midnight.pitch(max).funscript',
                             'Aurora Harbor Midnight(v3).roll.funscript'):
                    self.file(name,scripts)
                other='scripts' if primary=='videos' else 'videos'
                self.file('Unrelated Harbor Midnight'+('.funscript' if other=='scripts' else '.mp4'), b)
                _,rows=scan_sources([Source(str(a),primary,partner=str(b)),Source(str(b),other)])
                self.assertEqual(len(rows),1)
                self.assertEqual((rows[0]['missing'],len(rows[0]['files'])),('',5))

    def test_copy_and_move_preserve_every_variant_and_axis(self):
        names=['Aurora Harbor Midnight'+s for s in ('.mp4','(max).mp4','.funscript',
               '(max).funscript','(max).pitch.funscript','.roll(max).funscript')]
        for name in names:self.file(name)
        state=self.state()
        self.assertEqual(len(state['rows']),1)
        self.assertTrue(state['rows'][0]['selected'])
        result=execute(state,'copy')
        target=Path(result['rows'][0]['target'])
        self.assertEqual(sorted(f.name for f in target.iterdir()),sorted(names))
        self.assertTrue(all((self.source/n).exists() for n in names))
        self.options.output=self.root/'moved'
        result=execute(self.state(),'move')
        self.assertEqual(len(result['rows'][0]['operations']),len(names))
        self.assertEqual(list(self.source.iterdir()),[])

    def test_playback_pairing_keeps_versions_distinct_and_axis_sets_intact(self):
        records=[]
        for stem in ('test(v1)','test(v1).pitch','test.pitch(v2)','test(v2).roll'):
            path=str(self.source/(stem+'.funscript'))
            records.append(dict(path=path,name=Path(path).name,name_profile=profile(path),duration=None,size=1))
        groups=group_scripts(records)
        self.assertEqual(sorted(len(g['members']) for g in groups),[2,2])
        self.assertTrue(compare_names('Aurora Harbor v1.mp4','Aurora Harbor v2.funscript')['conflict'])
        self.assertTrue(compare_names('Aurora Harbor(v1).mp4','Aurora Harbor(v2).pitch.funscript')['conflict'])
        for suffix in ('.V2','.v2','[V2]'):
            p=parts('Aurora Harbor'+suffix+'.funscript')
            self.assertEqual((p.work,p.versions),('Aurora Harbor',()))
            self.assertTrue(p.axes)


from contextlib import contextmanager
@contextmanager
def _store(path):
    store=Store(path)
    try:yield store
    finally:store.close()


if __name__ == '__main__':unittest.main()
