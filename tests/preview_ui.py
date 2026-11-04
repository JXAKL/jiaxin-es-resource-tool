"""Local visual preview with synthetic matches; never uses the user's settings."""
import sys
import tempfile
import tkinter as tk
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from organizer.model import FileGroup, Topic, match_group

with tempfile.TemporaryDirectory() as folder:
    app.STATE = Path(folder)
    app.REPORTS = Path(folder) / 'reports'
    root = tk.Tk()
    ui = app.App(root)
    ui.sources.insert('end', 'D:/素材库/视频与脚本（界面示例）')
    ui.target.set('D:/素材库/按作者归类')
    for i, title in enumerate(('Moonlight Dance', 'Harbor Lights', 'Summer Breeze', 'Blue Horizon')):
        topic = Topic(i + 1, title, 'ExampleAuthor', 'https://example.invalid/', body=title, details_loaded=True)
        ui.matches.append(match_group(FileGroup(str(i), title), [topic] if i < 2 else []))
    ui.render()
    ui.write_log('界面预览 · 示例数据，不会访问网站或移动素材。')
    ui.write_log('已完成本地匹配，可双击条目核对作者与匹配证据。')
    root.after(300000, root.destroy)
    root.mainloop()
