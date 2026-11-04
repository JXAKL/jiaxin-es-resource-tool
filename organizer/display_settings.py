"""Small, validated display preferences shared by all lazily created pages."""
import json
import tkinter as tk
from tkinter import ttk
from pathlib import Path

FIELDS = {
    'table_font': ('表格字号', 10, 9, 16),
    'row_height': ('表格行高', 37, 26, 56),
    'sidebar_width': ('侧栏宽度', 324, 306, 420),
    'log_lines': ('日志高度 / 行', 4, 3, 12),
    'log_limit': ('日志保留行数', 1000, 100, 5000),
}
DEFAULTS = {key: spec[1] for key, spec in FIELDS.items()}


def validate(values):
    result = {}
    for key, (label, default, low, high) in FIELDS.items():
        try:
            value = int(str(values.get(key, default)).strip())
        except (TypeError, ValueError):
            raise ValueError(f'{label}需要填写 {low}–{high} 的整数。') from None
        if not low <= value <= high:
            raise ValueError(f'{label}需要介于 {low} 和 {high}。')
        result[key] = value
    if result['row_height'] < result['table_font'] * 2 + 6:
        raise ValueError('表格行高需至少为字号的两倍加 6，避免文字被截断。')
    return result


def load(path):
    try:
        return validate(json.loads(Path(path).read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(DEFAULTS)


def save(path, values):
    values = validate(values)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)
    return values


def preferences(widget):
    return getattr(widget.winfo_toplevel(), '_es_display', DEFAULTS)


def apply(root, surfaces, resize=False):
    from .ui import FONT
    from customtkinter import CTkTextbox
    settings = preferences(root)
    style = ttk.Style(root)
    style.configure('Treeview', font=(FONT, settings['table_font']), rowheight=settings['row_height'])
    style.configure('Treeview.Heading', font=(FONT, settings['table_font'], 'bold'))
    for surface in surfaces:
        sidebar = getattr(surface, '_es_sidebar', None)
        if sidebar is not None and not hasattr(surface, '_es_sidebar_split'):
            sidebar.configure(width=settings['sidebar_width'])
        elif sidebar is not None and resize:
            sidebar.configure(width=settings['sidebar_width'])
            split = surface._es_sidebar_split
            split.specs = [(widget, key, settings['sidebar_width'] if widget is sidebar else size, minimum)
                           for widget, key, size, minimum in split.specs]
            split.reset()
        log = getattr(getattr(surface, 'controller', None), 'log', None)
        if isinstance(log, CTkTextbox):
            log.configure(height=settings['log_lines'] * 20)
        elif isinstance(log, tk.Text):
            log.configure(height=settings['log_lines'])


def append_log(widget, text):
    """Bound on-screen history; don't jump to the bottom while reading old lines."""
    limit = preferences(widget)['log_limit']
    follow = widget.yview()[1] >= .995
    widget.configure(state='normal')
    widget.insert('end', text)
    excess = int(widget.index('end-1c').split('.')[0]) - limit - 1
    if excess > 0:
        widget.delete('1.0', f'{excess + 1}.0')
    if follow:
        widget.see('end')
    widget.configure(state='disabled')
