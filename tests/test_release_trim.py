import tempfile
import unittest
from pathlib import Path
from build_release import trim_runtime, windows_version


class ReleaseTrimTests(unittest.TestCase):
    def test_two_component_display_version_keeps_windows_quad(self):
        self.assertEqual(windows_version('2.0'),(2,0,0,0))
        self.assertEqual(windows_version('2.1'),(2,1,0,0))
        self.assertEqual(windows_version('2.1.1'),(2,1,1,0))
        self.assertEqual(windows_version('2.2.13'),(2,2,13,0))
        with self.assertRaises(ValueError): windows_version('2.0.0.0.0')

    def test_only_allowlisted_files_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); browser=root/'_internal/browsers/chromium-1/chrome-win64'
            (browser/'locales').mkdir(parents=True)
            for name in ('locales/zh-CN.pak','locales/en-US.pak','locales/fr.pak','chrome.exe','chrome.dll','setup.exe','ABOUT'):
                (browser/name).write_bytes(b'fixture')
            removed=trim_runtime(root)
            self.assertEqual(len(removed),2)
            self.assertTrue((browser/'chrome.dll').exists())
            self.assertTrue((browser/'locales/zh-CN.pak').exists())
            self.assertTrue((browser/'ABOUT').exists())
