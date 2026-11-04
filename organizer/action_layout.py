"""Reflow small action rows when users narrow a resizable work area."""
import tkinter as tk
import customtkinter as ctk


def wrap_actions(row, changed=lambda: None):
    widgets = row.pack_slaves()
    allowed = (ctk.CTkButton, ctk.CTkComboBox, tk.Label)
    if len(widgets) < 2 or not all(isinstance(w, allowed) for w in widgets):
        return
    saved = []
    for widget in widgets:
        info = widget.pack_info()
        info.pop('in', None)
        saved.append((widget, info))
    # Preserve the visual order of pack(side='right') controls.
    ordered = [w for w, p in saved if p['side'] != 'right'] + [w for w, p in reversed(saved) if p['side'] == 'right']
    pending = None
    last_width = 0
    wrapped = False

    def arrange():
        nonlocal pending, last_width, wrapped
        pending = None
        width = row.winfo_width()
        if width < 30 or width == last_width:
            return
        last_width = width
        needed = sum(w.winfo_reqwidth() for w in ordered) + 8 * (len(ordered)-1)
        if needed <= width and not wrapped:
            return
        for widget in widgets:
            widget.pack_forget()
            widget.grid_forget()
        for column in range(len(widgets)):
            row.columnconfigure(column, weight=0)
        if needed <= width:
            for widget, info in saved:
                widget.pack(in_=row, **info)
            wrapped = False
        else:
            wrapped = True
            line = column = used = 0
            for widget in ordered:
                size = widget.winfo_reqwidth()
                if column and used+8+size > width:
                    line += 1
                    column = used = 0
                widget.grid(in_=row, row=line, column=column, sticky='w',
                            padx=(0, 8), pady=(0, 6))
                used += size + (8 if column else 0)
                column += 1
        changed()

    def schedule(event=None):
        nonlocal pending
        if event is not None and event.widget is not row:
            return
        if pending is None:
            pending = row.after_idle(arrange)

    def cleanup(event):
        nonlocal pending
        if event.widget is row and pending is not None:
            row.after_cancel(pending)
            pending = None

    row.bind('<Configure>', schedule, add='+')
    row.bind('<Destroy>', cleanup, add='+')
    schedule()
