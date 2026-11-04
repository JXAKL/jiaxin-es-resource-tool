"""Build the real Tk UI without opening a visible window or accessing the network."""
import sys
import tempfile
import tkinter as tk
import json
import customtkinter as ctk
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from organizer.model import FileGroup, Topic, match_group
from organizer.forum import ConnectionFailure

with tempfile.TemporaryDirectory() as temp:
    app.STATE = Path(temp)
    root = tk.Tk()
    root.withdraw()
    ui = app.App(root)
    ui.request_interval.set('0.25')
    ui.search_interval.set('0')
    ui.per_minute.set('30')
    ui.cooldown_seconds.set('10')
    assert ui.sync_search.get() is True
    ui.sync_search.set(False)
    ui.network_options()
    client = ui.client(False)
    assert client.interval == .25 and client.pacer.search_interval == 0
    assert client.pacer.per_minute == 30 and client.cooldown_seconds == 10
    ui._save()
    ui.per_minute.set('6')
    ui._load()
    assert ui.per_minute.get() == '30'
    assert ui.sync_search.get() is False
    ui.sync_search.set(True)
    if '--visible' in sys.argv:
        root.deiconify()
        root.update()
    ui.matches = [match_group(FileGroup('sample', 'Moonlight Dance'),
                             [Topic(1, 'Moonlight Dance', 'ExampleAuthor', 'https://discuss.eroscripts.com/t/1',
                                    body='Moonlight Dance', details_loaded=True)])]
    ui.render()
    root.update_idletasks()
    assert len(ui.tree.get_children()) == 1
    assert ui.tree.set('0', 'author') == 'ExampleAuthor'
    assert 'type' in ui.tree.cget('columns')
    ui.sync_search.set(False)
    ui.grouping_changed()
    assert ui.move_btn.cget('state') == 'disabled'
    with patch('app.messagebox.showerror') as error:
        ui.move()
        error.assert_called_once()
    ui.set_busy(True)
    assert ui.sync_search_switch.cget('state') == 'disabled'
    ui.set_busy(False)
    ui.sync_search.set(True)
    ui.grouping_changed()
    ui.filter_var.set('未匹配')
    ui.render()
    assert not ui.tree.get_children()
    ui.filter_var.set('全部')
    ui.render()
    ui.uncheck()
    assert not ui.matches[0].selected
    ui.tree.selection_set('0')
    ui.details()
    root.update_idletasks()
    dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    evidence = next(w for w in descendants(dialog) if isinstance(w, tk.Text))
    assert '85' in evidence.get('1.0', 'end')
    assert '正文' in evidence.get('1.0', 'end')
    assert not any(isinstance(w, ctk.CTkLabel) and w.cget('text') == '让每一份素材，都有清晰的归属。'
                   for w in descendants(root))
    if '--visible' in sys.argv:
        dialog.update()
        for widget in descendants(dialog):
            if isinstance(widget, (app.ttk.Button, ctk.CTkButton)):
                bottom = widget.winfo_rooty() - dialog.winfo_rooty() + widget.winfo_height()
                assert bottom <= dialog.winfo_height(), (str(widget), bottom, dialog.winfo_height())
    dialog.destroy()
    if '--visible' in sys.argv:
        root.update()
        for widget in (ui.log, ui.progress, ui.start_btn, ui.move_btn, ui.tree):
            assert widget.winfo_width() > 1 and widget.winfo_height() > 1
            bottom = widget.winfo_rooty() - root.winfo_rooty() + widget.winfo_height()
            assert bottom <= root.winfo_height(), (str(widget), bottom, root.winfo_height())
        # Settings intentionally scroll; the last control must become reachable.
        ui.settings_panel._parent_canvas.yview_moveto(1)
        root.update()
        assert ui.workers_entry.winfo_rooty() >= root.winfo_rooty()
        assert ui.workers_entry.winfo_rooty() + ui.workers_entry.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        assert root.title() == app.APP_NAME
        assert ui.stat_values[0].get() == '1'
        root.geometry('1120x740')
        root.update()
        for widget in (ui.start_btn, ui.stop_btn, ui.resume_btn, ui.move_btn, ui.undo_btn, ui.import_btn, ui.tree, ui.log, ui.progress):
            assert widget.winfo_width() > 1 and widget.winfo_height() > 1
            x = widget.winfo_rootx() - root.winfo_rootx()
            y = widget.winfo_rooty() - root.winfo_rooty()
            assert x >= 0 and x + widget.winfo_width() <= root.winfo_width(), str(widget)
            assert y >= 0 and y + widget.winfo_height() <= root.winfo_height(), str(widget)
    print('GUI_SMOKE_OK', 'requested_size=', root.winfo_reqwidth(), root.winfo_reqheight())
    source = Path(temp) / 'input'
    source.mkdir()
    (source / 'Moonlight Dance.mp4').write_bytes(b'local test fixture')
    (source / 'Moonlight Dance.funscript').write_text('{}')
    (source / 'Moonlight Dance.pitch.funscript').write_text('{}')
    app.REPORTS = Path(temp) / 'reports'
    ui.sources.delete(0, 'end')
    ui.sources.insert('end', str(source))
    ui.target.set(str(Path(temp) / 'output'))
    failed_browser = MagicMock()
    failed_browser.__enter__.side_effect = ConnectionFailure('fixture connection failure')
    ui.client = lambda visible: failed_browser
    ui.run = lambda work: work()
    try:
        ui.start()
        raise AssertionError('Expected the simulated browser startup failure')
    except ConnectionFailure:
        pass
    report = next(app.REPORTS.glob('*/归类预览.json'))
    saved = json.loads(report.read_text(encoding='utf-8'))
    assert len(saved) == 1 and len(saved[0]['group']['files']) == 3
    assert saved[0]['group']['title'] == 'Moonlight Dance'
    published = []
    while not ui.events.empty():
        kind, value = ui.events.get_nowait()
        if kind == 'matches':
            published = value
    assert len(published) == 1, 'Scan must reach the preview before browser startup'
    ui.matches = published
    ui.render()
    assert ui.tree.set('0', 'type') == 'funscript / 视频'
    assert ui.tree.set('0', 'title') == 'Moonlight Dance.funscript'
    ui.sync_search.set(False)
    try:
        ui.start()
        raise AssertionError('Expected fixture failure')
    except ConnectionFailure:
        pass
    while not ui.events.empty():
        kind, value = ui.events.get_nowait()
        if kind == 'matches':
            published = value
    assert len(published) == 2
    assert {m.group.kind_label for m in published} == {'funscript', '视频'}
    print('SCAN_RETENTION_OK: preview and report survive browser connection failure')
    root.destroy()
