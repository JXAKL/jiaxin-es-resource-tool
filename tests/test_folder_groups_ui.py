import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
import app
from organizer import workspace
from organizer.integration_check import exercise_folder_groups


class FolderUITests(unittest.TestCase):
    def test_actual_scan_filter_move_and_undo_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); state=base/'state'; state.mkdir()
            with patch.object(app,'STATE',state), patch.object(workspace,'STATE',state):
                root=tk.Tk(); root.attributes('-alpha',0)
                errors=[]; root.report_callback_exception=lambda *exc:errors.append(exc)
                try:
                    shell=workspace.Workspace(root)
                    exercise_folder_groups(shell,root,base)
                    self.assertTrue(shell.show('folders'))
                    from organizer.display_settings import apply
                    for width in (306, 420):
                        root._es_display.update(sidebar_width=width, table_font=16, row_height=56)
                        apply(root,[shell.pages['folders']],resize=True); root.update()
                        page=shell.folders; viewport=page.layout.settings._parent_canvas
                        for widget in (page.paired_switch,page.zero_switch,page.count_hint,*page.inputs):
                            self.assertGreaterEqual(widget.winfo_rooty(),viewport.winfo_rooty())
                            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),viewport.winfo_rooty()+viewport.winfo_height())
                            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(),viewport.winfo_rootx()+viewport.winfo_width())
                    self.assertFalse(errors,errors)
                finally:
                    for job in root.tk.call('after','info'): root.tk.call('after','cancel',job)
                    root.destroy()


if __name__=='__main__': unittest.main()
