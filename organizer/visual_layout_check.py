"""Check narrow action rows and empty-state transitions in actual Tk widgets."""
import time
import tkinter as tk
import customtkinter as ctk
from .panels import ResultTable
from .ui import TaskProgress


def settle(root):
    deadline = time.monotonic() + .15
    while time.monotonic() < deadline:
        root.update()
        time.sleep(.01)


def exercise(shell, root):
    root.geometry('1180x820')
    covered = []
    for key in ('author', 'pair', 'scripts', 'videos', 'folders', 'collector',
                'search_scraper', 'completion', 'resource_organize', 'downloads'):
        assert shell.show(key)
        settle(root)
        controller = shell.pages[key].controller
        layout = getattr(controller, 'layout', controller)
        split = layout.main_split
        # Shrink the result area enough to require multiple action rows.
        split.sash_place(0, split.winfo_width()-500, 0)
        settle(root)
        if hasattr(layout, 'toolbar'):
            pending = [layout.toolbar, layout.footer]
            while pending:
                widget = pending.pop()
                if isinstance(widget, (ctk.CTkButton, ctk.CTkComboBox)):
                    assert widget.winfo_ismapped(), (key, 'hidden action', widget)
                    assert widget.winfo_width() >= widget.winfo_reqwidth()-2, (key, 'clipped action', widget)
                    assert widget.winfo_rootx()+widget.winfo_width() <= layout.result.winfo_rootx()+layout.result.winfo_width(), key
                    assert widget.winfo_rooty()+widget.winfo_height() <= layout.result.winfo_rooty()+layout.result.winfo_height(), (key, widget.cget('text') if isinstance(widget, ctk.CTkButton) else 'combo', widget.winfo_geometry(), layout.result.winfo_geometry(), layout.footer.winfo_geometry(), [(str(w), w.winfo_reqheight(), w.winfo_height()) for w in getattr(layout, 'detail_rows', ())], getattr(layout, 'detail_split', None).panecget(layout.footer, 'minsize'))
                elif isinstance(widget, ctk.CTkTextbox):
                    assert widget.winfo_height() >= 48, (key, 'unreadable details', widget.winfo_geometry())
                else:
                    pending.extend(widget.winfo_children())
        split.reset()
        settle(root)
        covered.append(key)
    probe = tk.Toplevel(root)
    probe.attributes('-alpha', 0)
    try:
        tree = ResultTable(probe, empty_text='暂无结果', columns=('title',), show='headings')
        tree.pack(fill='both', expand=True)
        progress = TaskProgress(probe)
        progress.pack(fill='x')
        settle(root)
        assert tree.empty_hint.winfo_ismapped()
        item = tree.insert('', 'end', values=('合成项目',))
        settle(root)
        assert not tree.empty_hint.winfo_ismapped()
        tree.delete(item)
        settle(root)
        assert tree.empty_hint.winfo_ismapped()
        items = [tree.insert('', 'end', values=(str(i),)) for i in range(1000)]
        original_children = tree.get_children
        inspections = []
        def counted_children(*args):
            inspections.append(True)
            return original_children(*args)
        tree.get_children = counted_children
        for item in items:
            tree.delete(item)
        assert not inspections, 'Batch deletion must not repeatedly enumerate the table'
        settle(root)
        assert len(inspections) == 1 and tree.empty_hint.winfo_ismapped()
        tree.get_children = original_children
        assert str(progress.cget('mode')) == 'determinate' and float(progress.cget('value')) == 0
        progress.start(12)
        settle(root)
        assert str(progress.cget('mode')) == 'indeterminate'
        progress.stop()
        assert str(progress.cget('mode')) == 'determinate' and float(progress.cget('value')) == 0
    finally:
        probe.destroy()
    return dict(narrow_action_rows=covered, empty_insert_delete=True, batched_empty_refresh=True, idle_progress=True)
