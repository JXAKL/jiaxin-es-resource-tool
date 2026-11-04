import tempfile
import unittest
from pathlib import Path
from organizer.downloads.legacy_profile import prepare_iwara_profile, prepare_site_profile


class LegacyProfileTests(unittest.TestCase):
    def test_copy_site_state_once_without_touching_original_or_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'old'
            (source / 'Default/Local Storage').mkdir(parents=True)
            (source / 'Default/Cache').mkdir()
            (source / 'Local State').write_text('fixture')
            (source / 'Default/Local Storage/state').write_text('old')
            (source / 'Default/Cache/blob').write_text('cache')
            target = Path(prepare_iwara_profile(root/'new', source, lambda _:None))
            self.assertEqual((target/'Default/Local Storage/state').read_text(), 'old')
            self.assertFalse((target/'Default/Cache').exists())
            (target/'Default/Local Storage/state').write_text('new')
            prepare_iwara_profile(root/'new', source, lambda _:None)
            self.assertEqual((target/'Default/Local Storage/state').read_text(), 'new')
            self.assertEqual((source/'Default/Local Storage/state').read_text(), 'old')
            hanime = Path(prepare_site_profile(root/'new',source,lambda _:None,'hanime','Hanime1'))
            self.assertNotEqual(hanime,target)
            self.assertEqual((hanime/'Default/Local Storage/state').read_text(),'old')
