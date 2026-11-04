import json
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from organizer import workspace


from organizer.integration_check import exercise_completion


class CompletionUITests(unittest.TestCase):
    def test_actual_navigation_scan_persistence_and_report_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(app,'STATE',Path(tmp)),patch.object(workspace,'STATE',Path(tmp)):
                root=tk.Tk();root.withdraw()
                try:
                    shell=workspace.Workspace(root);root.deiconify()
                    self.assertTrue(exercise_completion(shell,root,tmp))
                finally:
                    for job in root.tk.call('after','info'):root.tk.call('after','cancel',job)
                    root.destroy()


if __name__=='__main__':unittest.main()
