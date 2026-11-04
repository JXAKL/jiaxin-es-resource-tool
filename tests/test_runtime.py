import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer import runtime


class RuntimeTests(unittest.TestCase):
    def test_source_keeps_existing_data(self):
        with patch.object(runtime, 'FROZEN', False), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(runtime.data_root(), runtime.ROOT)

    def test_packaged_data_is_outside_program_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(runtime, 'FROZEN', True), patch.dict(os.environ, {'LOCALAPPDATA': folder}, clear=True):
                self.assertEqual(runtime.data_root(), Path(folder) / 'EroScriptsOrganizer')

    def test_isolated_selftest_data_override(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {'ES_ORGANIZER_DATA_DIR': folder}):
                self.assertEqual(runtime.data_root(), Path(folder).resolve())

    def test_packaged_browser_overrides_external_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            (bundle / 'browsers').mkdir()
            with patch.object(runtime, 'FROZEN', True), patch.object(runtime, 'BUNDLE', bundle):
                with patch.dict(os.environ, {'PLAYWRIGHT_BROWSERS_PATH': 'external-cache'}):
                    runtime.configure_bundled_browser()
                    self.assertEqual(os.environ['PLAYWRIGHT_BROWSERS_PATH'], str(bundle / 'browsers'))
