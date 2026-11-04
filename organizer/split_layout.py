"""Resizable work areas; persist only deliberate drags, never transient sizes."""
import json
import math
import tkinter as tk
from pathlib import Path


class SplitPane(tk.PanedWindow):
    def __init__(self, parent, key, orient='horizontal', **kw):
        from .presentation import background
        surface = background(parent)
        super().__init__(parent, orient=orient, bg=surface, bd=0,
                         sashwidth=2, sashpad=3, sashrelief='flat',
                         showhandle=False, proxybackground='#4b5b53',
                         proxyborderwidth=0, proxyrelief='flat',
                         opaqueresize=False, **kw)
        self.key, self.axis = key, orient
        self.specs = []
        self.initialized = False
        self.pending = None
        self.save_job = None
        self.grips = []
        self.hot_sash = None
        self.grip_job = None
        root = self.winfo_toplevel()
        if not hasattr(root, '_es_splits'):
            root._es_splits = []
            from .runtime import STATE
            root._es_layout_path = getattr(root, '_es_layout_path', STATE / 'pane-layout.json')
            try:
                data = json.loads(Path(root._es_layout_path).read_text(encoding='utf-8'))
                root._es_split_values = {
                    key: {name: value for name, value in parts.items()
                          if isinstance(value, (int, float)) and math.isfinite(value) and 0 < value <= 1}
                    for key, parts in data.items() if isinstance(parts, dict)
                } if isinstance(data, dict) else {}
            except (ValueError, OSError):
                root._es_split_values = {}
        root._es_splits.append(self)
        self.bind('<Map>', self.schedule, add='+')
        self.bind('<Configure>', self.schedule, add='+')
        self.bind('<ButtonRelease-1>', self.released, add='+')
        self.bind('<Double-Button-1>', self.reset, add='+')
        self.bind('<Motion>', self.pointer, add='+')
        self.bind('<Leave>', self.leave_pointer, add='+')
        self.bind('<Configure>', self.schedule_grips, add='+')
        self.bind('<Destroy>', self.cleanup, add='+')

    def cleanup(self, event):
        if event.widget is not self:
            return
        for job in (self.pending, self.save_job, self.grip_job):
            if job is not None:
                self.after_cancel(job)
        root = self.winfo_toplevel()
        if self in getattr(root, '_es_splits', []):
            root._es_splits.remove(self)

    def pointer(self, event):
        hit = self.identify(event.x, event.y)
        self.hot_sash = int(hit[0]) if hit else None
        self.configure(cursor=('sb_h_double_arrow' if self.axis == 'horizontal' else 'sb_v_double_arrow')
                       if hit else '')
        self.color_grips()

    def leave_pointer(self, event=None):
        self.hot_sash = None
        self.color_grips()

    def schedule_grips(self, event=None):
        if event is not None and event.widget is not self:
            return
        if self.grip_job is None:
            self.grip_job = self.after_idle(self.draw_grips)

    def draw_grips(self):
        self.grip_job = None
        count = max(0, len(self.panes())-1)
        while len(self.grips) < count:
            index = len(self.grips)
            grip = tk.Canvas(self, bg=self.cget('bg'), bd=0, highlightthickness=0,
                             cursor='sb_h_double_arrow' if self.axis == 'horizontal' else 'sb_v_double_arrow')
            # A short rounded stroke replaces the native square handle and border.
            if self.axis == 'horizontal':
                grip.configure(width=8, height=42)
                grip.create_line(4, 9, 4, 33, width=3, capstyle='round', tags='grip')
            else:
                grip.configure(width=42, height=8)
                grip.create_line(9, 4, 33, 4, width=3, capstyle='round', tags='grip')
            for sequence in ('<ButtonPress-1>', '<B1-Motion>', '<ButtonRelease-1>', '<Double-Button-1>'):
                grip.bind(sequence, lambda event, seq=sequence: self.relay_grip(event, seq))
            grip.bind('<Enter>', lambda event, i=index: self.highlight_grip(i))
            grip.bind('<Leave>', self.leave_pointer)
            self.grips.append(grip)
        for index, grip in enumerate(self.grips):
            if index >= count:
                grip.place_forget()
                continue
            x, y = self.sash_coord(index)
            if self.axis == 'horizontal':
                grip.place(x=x-3, y=max(0, (self.winfo_height()-42)//2))
            else:
                grip.place(x=max(0, (self.winfo_width()-42)//2), y=y-3)
            tk.Misc.lift(grip)
        self.color_grips()

    def highlight_grip(self, index):
        self.hot_sash = index
        self.color_grips()

    def color_grips(self):
        for index, grip in enumerate(self.grips):
            grip.itemconfigure('grip', fill='#91b5a0' if index == self.hot_sash else '#41464b')

    def relay_grip(self, event, sequence):
        # Preserve Tk's native drag/keyboard machinery, including the release proxy.
        if sequence == '<Double-Button-1>':
            return self.reset()
        self.event_generate(sequence, x=event.x_root-self.winfo_rootx(),
                            y=event.y_root-self.winfo_rooty())
        return 'break'

    def add_panel(self, widget, key, size=None, minimum=80):
        if widget.winfo_manager() == 'pack':
            widget.pack_forget()
        elif widget.winfo_manager() == 'grid':
            widget.grid_forget()
        self.specs.append((widget, key, size, minimum))
        self.add(widget, minsize=minimum, stretch='always', sticky='nsew')
        widget.lift()
        self.initialized = False
        self.schedule()

    def show_panel(self, widget, visible):
        shown = str(widget) in {str(p) for p in self.panes()}
        if shown == bool(visible):
            return
        if visible:
            spec = next(s for s in self.specs if s[0] is widget)
            following = [s[0] for s in self.specs[self.specs.index(spec)+1:]
                         if str(s[0]) in {str(p) for p in self.panes()}]
            self.add(widget, minsize=spec[3], stretch='always', sticky='nsew',
                     **({'before': following[0]} if following else {}))
            widget.lift()
        else:
            self.forget(widget)
        self.initialized = False
        self.schedule()

    def visible_specs(self):
        panes = {str(p) for p in self.panes()}
        return [s for s in self.specs if str(s[0]) in panes]

    def schedule(self, event=None):
        if event is not None and event.widget is not self:
            return
        if not self.initialized and self.pending is None:
            self.pending = self.after_idle(self.restore)

    def restore(self):
        self.pending = None
        if not self.winfo_ismapped():
            return
        specs = self.visible_specs()
        extent = self.winfo_width() if self.axis == 'horizontal' else self.winfo_height()
        available = extent - 8 * (len(specs)-1)
        if len(specs) < 2 or available <= 1:
            return
        saved = self.winfo_toplevel()._es_split_values.get(self.key, {})
        valid = isinstance(saved, dict) and all(isinstance(saved.get(s[1]), (int, float))
            and math.isfinite(saved[s[1]]) and saved[s[1]] > 0 for s in specs)
        if valid:
            total = sum(saved[s[1]] for s in specs)
            sizes = [available * saved[s[1]] / total for s in specs]
        else:
            spare = available - sum(s[2] or 0 for s in specs)
            flex = sum(s[2] is None for s in specs)
            sizes = [s[2] if s[2] is not None else max(1, spare / max(1, flex)) for s in specs]
        # Keep the whole split within the viewport, even after screen-size changes.
        mins = [s[3] for s in specs]
        surplus = max(0, available - sum(mins))
        weights = [max(0, value-low) for value, low in zip(sizes, mins)]
        total = sum(weights)
        sizes = [low + surplus * (weight / total if total else 1/len(specs))
                 for low, weight in zip(mins, weights)]
        position = 0
        for index, size in enumerate(sizes[:-1]):
            position += round(size)
            self.sash_place(index, position if self.axis == 'horizontal' else 0,
                            position if self.axis == 'vertical' else 0)
            position += 8
        self.initialized = True
        self.schedule_grips()

    def released(self, event=None):
        # The class binding commits the proxy after the instance binding returns.
        if self.save_job is not None:
            self.after_cancel(self.save_job)
        self.save_job = self.after_idle(self.remember)

    def remember(self):
        self.save_job = None
        self.schedule_grips()
        if not self.winfo_exists() or not self.initialized:
            return
        root = self.winfo_toplevel()
        values = root._es_split_values.setdefault(self.key, {})
        if not isinstance(values, dict):
            values = root._es_split_values[self.key] = {}
        specs = self.visible_specs()
        sizes = [s[0].winfo_width() if self.axis == 'horizontal' else s[0].winfo_height() for s in specs]
        total = sum(sizes)
        if not total:
            return
        # When a preview is hidden, preserve its share for a later reappearance.
        mass = sum(values.get(s[1], 0) for s in specs) or 1
        for spec, size in zip(specs, sizes):
            values[spec[1]] = size / total * mass
        save(root)

    def reset(self, event=None):
        self.winfo_toplevel()._es_split_values.pop(self.key, None)
        self.initialized = False
        self.schedule()
        save(self.winfo_toplevel())
        return 'break'


def save(root):
    from .collector import atomic_text
    try:
        atomic_text(Path(root._es_layout_path), json.dumps(root._es_split_values, ensure_ascii=False))
    except OSError:
        # Geometry remains usable if preferences cannot be written.
        pass


def reset_all(root):
    root._es_split_values = {}
    for split in getattr(root, '_es_splits', []):
        if split.winfo_exists():
            split.initialized = False
            split.schedule()
    if hasattr(root, '_es_layout_path'):
        save(root)


def split_grid(parent, widgets, key, sizes, minimums, orient='vertical'):
    """Replace adjacent grid cells, retaining each widget and all bindings."""
    info = widgets[0].grid_info()
    splitter = SplitPane(parent, key, orient)
    for index, widget in enumerate(widgets):
        splitter.add_panel(widget, str(index), sizes[index], minimums[index])
    splitter.grid(row=info['row'], column=info['column'], sticky='nsew',
                  rowspan=len(widgets) if orient == 'vertical' else 1,
                  columnspan=len(widgets) if orient == 'horizontal' else 1)
    return splitter
