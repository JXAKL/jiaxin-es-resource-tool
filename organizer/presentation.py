"""Cheap layout containers; rounding is reserved for visible cards and controls."""
import tkinter as tk
import time
from tkinter import ttk


def background(parent):
    node = parent
    while node is not None:
        try:
            value = node.cget('fg_color')
        except (tk.TclError, ValueError):
            try:
                return node.cget('background')
            except tk.TclError:
                node = getattr(node, 'master', None)
                continue
        if value != 'transparent':
            return value[-1] if isinstance(value, (list, tuple)) else value
        node = getattr(node, 'master', None)
    return '#101113'


def flat_frame(parent, **kwargs):
    kwargs.setdefault('background', background(parent))
    return tk.Frame(parent, **kwargs)


def scrollbar(parent, orientation='vertical', command=None, **_):
    return ttk.Scrollbar(parent, orient=orientation, command=command,
                         style='Slim.' + orientation.title() + '.TScrollbar')


def bind_wrap(container, label, minimum=260):
    """Avoid reflowing a status label for every pixel while resizing a window."""
    pending = None
    current = None
    def update(width):
        nonlocal pending, current
        pending = None
        if label.winfo_exists():
            label.configure(wraplength=width)
            current = width
    def configure(event):
        nonlocal pending
        if event.widget is not container:
            return
        width = max(minimum, event.width - 10)
        if width == current:
            return
        if pending is not None:
            container.after_cancel(pending)
        pending = container.after(80, update, width)
    container.bind('<Configure>', configure, add='+')
    def destroyed(event):
        nonlocal pending
        if event.widget is container and pending is not None:
            try:
                container.after_cancel(pending)
            except tk.TclError:
                pass
            pending = None
    container.bind('<Destroy>', destroyed, add='+')


def poll_delay(surface, busy=False):
    return 100 if busy or getattr(surface, 'active', True) else 750


def defer_scrollbar_redraws(surface):
    """Let Tk paint once per event cycle instead of recursively flushing layouts.

    CustomTkinter's scrollbar calls canvas.update_idletasks() from every draw,
    including draws caused by resizing a scrollable frame. That synchronously
    flushes the whole window and re-enters other widgets' configure callbacks.
    Only suppress this internal flush; geometry, scrolling and drawing still
    run normally in Tk's event loop. Never alter application update calls.
    """
    from customtkinter import CTkScrollbar
    pending = [surface]
    while pending:
        widget = pending.pop()
        if isinstance(widget, CTkScrollbar):
            widget._canvas.update_idletasks = lambda: None
        pending.extend(widget.winfo_children())


def paint_items(owner, surface, items, draw, done=lambda: None):
    """Bound each table paint slice so buttons and window sizing keep responding."""
    generation = getattr(owner, '_paint_generation', 0) + 1
    owner._paint_generation = generation
    iterator = iter(items)
    def advance():
        if generation != owner._paint_generation or not surface.winfo_exists():
            return
        started = time.perf_counter()
        try:
            while time.perf_counter() - started < .006:
                draw(next(iterator))
        except StopIteration:
            done()
            return
        surface.after(8 if getattr(surface, 'active', True) else 150, advance)
    advance()
