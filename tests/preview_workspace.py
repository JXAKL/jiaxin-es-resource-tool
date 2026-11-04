"""Launch an isolated, empty workspace for visual inspection."""
import sys
import tempfile
import tkinter as tk
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import organizer.workspace as workspace

with tempfile.TemporaryDirectory(prefix='jiaxin-visual-') as folder:
    app.STATE = workspace.STATE = Path(folder)
    root = tk.Tk()
    shell = workspace.Workspace(root)
    root.title(workspace.APP_NAME + ' · 融合界面验证')
    root.mainloop()
