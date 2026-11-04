"""Extend the shared dark theme without resetting author-table styles."""
from tkinter import ttk
from ..ui import BG, PANEL, FIELD, BORDER, TEXT, MUTED, ACCENT, FONT

INK = TEXT

def apply_theme(root):
    root.configure(background=BG)
    style = ttk.Style(root)
    style.configure('Card.TFrame', background=PANEL)
    style.configure('Card.TLabel', background=PANEL, foreground=TEXT)
    style.configure('Title.TLabel', background=PANEL, foreground=TEXT, font=(FONT, 20, 'bold'))
    style.configure('Section.TLabel', background=PANEL, foreground=TEXT, font=(FONT, 11, 'bold'))
    style.configure('CardMuted.TLabel', background=PANEL, foreground=MUTED, font=(FONT, 9))
    for name in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(name, fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT,
                        padding=5, bordercolor=BORDER, arrowcolor=MUTED)
        style.map(name, fieldbackground=[('readonly', FIELD), ('disabled', PANEL)],
                  foreground=[('readonly', TEXT), ('disabled', MUTED)], selectbackground=[('!disabled', '#30483d')])
    for name in ('TCheckbutton', 'TRadiobutton'):
        style.configure(name, background=PANEL, foreground=TEXT, padding=(0, 3),
                        indicatorbackground=FIELD, indicatorforeground=ACCENT)
        style.map(name, background=[('active', PANEL)], indicatorbackground=[('selected', '#527661')],
                  foreground=[('disabled', MUTED)])
    style.configure('TScrollbar', background=BORDER, troughcolor=PANEL, arrowcolor=MUTED)
    style.configure('Horizontal.TScale', background=PANEL, troughcolor=FIELD)
    root.option_add('*Listbox.background', FIELD)
    root.option_add('*Listbox.foreground', TEXT)
    root.option_add('*Listbox.selectBackground', '#30483d')
    root.option_add('*Listbox.selectForeground', TEXT)
    root.option_add('*TCombobox*Listbox.background', FIELD)
    root.option_add('*TCombobox*Listbox.foreground', TEXT)
    return style
