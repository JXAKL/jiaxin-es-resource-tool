from pathlib import Path
from dataclasses import replace
import unittest

from tests import test_resource_organize as fixtures
from organizer.resource_organize import Source, scan_sources, execute
from organizer.resource_source_links import links_for


class LinkedSourceTests(unittest.TestCase):
    setUp=fixtures.ResourceOrganizeTests.setUp
    file=fixtures.ResourceOrganizeTests.file
    state=fixtures.ResourceOrganizeTests.state
    def linked(self,primary='videos'):
        auxiliary=self.root/'auxiliary';auxiliary.mkdir()
        other='scripts' if primary=='videos' else 'videos'
        self.options.sources=[Source(str(self.source),primary,'external',str(auxiliary)),Source(str(auxiliary),other)]
        return auxiliary

    def test_video_primary_only_pulls_corresponding_multiaxis(self):
        aux=self.linked()
        self.file('Aurora Harbor Midnight.mp4')
        self.file('Missing Harbor Morning.mp4')
        for suffix in ('.funscript','.pitch.funscript','_R1.funscript',' [A0].funscript'):
            self.file('axes/Aurora Harbor Midnight'+suffix,aux)
        self.file('Unrelated Harbor Evening.funscript',aux)
        self.file('Unrelated Harbor Evening.pitch.funscript',aux)
        groups,rows=scan_sources(self.options.sources)
        self.assertEqual(len(rows),2)
        matched=next(r for r in rows if 'Midnight' in r['title'])
        self.assertEqual(len(matched['files']),5)
        self.assertEqual(matched['missing'],'')
        self.assertIn('配套',matched['pairing_note'])
        self.assertEqual(next(r for r in rows if 'Morning' in r['title'])['missing'],'script')
        self.assertFalse(any('Unrelated' in f['path'] for r in rows for f in r['files']))

    def test_script_primary_uses_metadata_and_excludes_unrelated_video(self):
        aux=self.linked('scripts')
        self.file('opaque-name.pitch.funscript',data=b'{"metadata":{"title":"Aurora Harbor Midnight"},"actions":[]}')
        self.file('Aurora Harbor Midnight.mp4',aux)
        self.file('Other Harbor Evening.mp4',aux)
        _,rows=scan_sources(self.options.sources)
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['files']),2)
        self.assertEqual(rows[0]['missing'],'')
        self.assertIn('元数据',rows[0]['pairing_note'])

    def test_swap_changes_scope_and_empty_primary_does_not_pull_library(self):
        aux=self.linked()
        self.file('Aurora Harbor Midnight.mp4')
        self.file('Aurora Harbor Midnight.funscript',aux)
        self.file('Auxiliary Only Work.funscript',aux)
        self.assertEqual(len(scan_sources(self.options.sources)[1]),1)
        self.options.sources[0].partner=''
        self.options.sources[1].partner=str(self.source)
        self.assertEqual(len(scan_sources(self.options.sources)[1]),2)
        for p in self.source.iterdir():p.unlink()
        self.options.sources[1].partner='';self.options.sources[0].partner=str(aux)
        self.assertEqual(scan_sources(self.options.sources)[1],[])

    def test_competing_auxiliary_names_not_arbitrarily_attached(self):
        aux=self.linked()
        self.file('Aurora Harbor Midnight.mp4')
        self.file('[MakerOne] Aurora Harbor Midnight.funscript',aux)
        self.file('[MakerTwo] Aurora Harbor Midnight.funscript',aux)
        _,rows=scan_sources(self.options.sources)
        self.assertEqual(len(rows),1);self.assertEqual(len(rows[0]['files']),1)
        self.assertIn('候选',rows[0]['pairing_note'])

    def test_same_auxiliary_cannot_be_claimed_twice(self):
        aux=self.linked()
        self.file('[MakerOne] Aurora Harbor Midnight.mp4')
        self.file('[MakerTwo] Aurora Harbor Midnight.mp4')
        self.file('Aurora Harbor Midnight.funscript',aux)
        _,rows=scan_sources(self.options.sources)
        self.assertEqual(len(rows),2)
        self.assertTrue(all(len(r['files'])==1 for r in rows))
        self.assertTrue(all('竞争' in r['pairing_note'] for r in rows))

    def test_linked_copy_and_move_touch_only_selected_pair(self):
        aux=self.linked()
        self.file('Aurora Harbor Midnight.mp4')
        self.file('Aurora Harbor Midnight.funscript',aux)
        self.file('Aurora Harbor Midnight.pitch.funscript',aux)
        unused=self.file('Other Harbor Evening.funscript',aux)
        state=self.state();result=execute(state,'copy')
        self.assertEqual(result['rows'][0]['status'],'复制完成')
        self.assertEqual(len(result['rows'][0]['operations']),3)
        self.assertTrue(unused.exists());self.assertTrue(list(self.source.iterdir()))
        self.options.output=self.root/'move-out'
        result=execute(self.state(),'move')
        self.assertEqual(result['rows'][0]['status'],'移动完成')
        self.assertEqual(list(aux.iterdir()),[unused])
        self.assertEqual(list(self.source.iterdir()),[])

    def test_reject_cycles_reuse_missing_and_same_type_links(self):
        aux=self.linked()
        original=self.options.sources
        for sources in [
            [replace(original[0],partner=str(self.source)),original[1]],
            [original[0],replace(original[1],partner=str(self.source))],
            [original[0],replace(original[1],mode='videos')],
            [replace(original[0],partner=str(self.root/'missing')),original[1]],
            [*original,Source(str(self.root/'third'),'videos',partner=str(aux))]]:
            with self.subTest(sources=sources),self.assertRaises(ValueError):links_for(sources)


if __name__=='__main__':unittest.main()
