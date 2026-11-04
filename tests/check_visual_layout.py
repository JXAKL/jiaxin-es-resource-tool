import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as folder:
    os.environ['ES_ORGANIZER_DATA_DIR'] = folder
    import tkinter as tk
    from organizer.workspace import Workspace
    from organizer.visual_layout_check import exercise
    root = tk.Tk()
    root.attributes('-alpha', 0)
    errors = []
    root.report_callback_exception = lambda *error: errors.append(error)
    try:
        shell = Workspace(root)
        print(exercise(shell, root))
        assert not errors, errors
        shell.close()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
