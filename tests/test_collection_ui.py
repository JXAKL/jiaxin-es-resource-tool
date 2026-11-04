import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
import app
from organizer import workspace


class CollectionUITests(unittest.TestCase):
    def test_filter_persistence_validation_and_worker_options(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)
            with patch.object(app,'STATE',state), patch.object(workspace,'STATE',state):
                root=tk.Tk(); root.withdraw()
                try:
                    shell=workspace.Workspace(root); shell.show('collector'); page=shell.collector
                    self.assertFalse(page.title_filter_enabled.get())
                    self.assertEqual(page.title_keyword_entry.cget('state'),'disabled')
                    page.title_filter_switch.toggle()
                    self.assertTrue(page.title_filter_enabled.get())
                    self.assertEqual(page.title_keyword_entry.cget('state'),'normal')
                    page.author.set('Alpha'); page.title_keyword.set('  ')
                    with patch('organizer.collector_ui.BrowserClient') as browser, patch('organizer.collector_ui.messagebox.showerror') as error:
                        page.start(); browser.assert_not_called(); error.assert_called_once()
                    page.title_keyword.set('[Multi Axis]'); page.save()
                    page.title_filter_enabled.set(False); page.title_keyword.set(''); page._load(); page.update_title_controls()
                    self.assertTrue(page.title_filter_enabled.get()); self.assertEqual(page.title_keyword.get(),'[Multi Axis]')
                    with patch('organizer.collector_ui.BrowserClient'), patch('organizer.collector_ui.collect') as collect:
                        page.start()
                        self.assertEqual(page.title_keyword_entry.cget('state'),'disabled')
                        self.assertEqual(page.title_filter_switch.cget('state'),'disabled')
                        end=time.monotonic()+5
                        while page.busy and time.monotonic()<end: root.update(); time.sleep(.01)
                        self.assertFalse(page.busy)
                        options=collect.call_args.args[0]
                        self.assertTrue(options.title_filter_enabled); self.assertEqual(options.title_keyword,'[Multi Axis]')
                        self.assertEqual(page.title_keyword_entry.cget('state'),'normal')
                finally:
                    for job in root.tk.call('after','info'): root.tk.call('after','cancel',job)
                    root.destroy()


if __name__=='__main__': unittest.main()
