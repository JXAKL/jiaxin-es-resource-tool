"""Use the author workspace's rounded controls in the local tool views."""
from tkinter import ttk as native
import customtkinter as ctk
from ..ui import ACCENT, BORDER, TEXT, FONT

class Button(ctk.CTkButton):
    def __init__(self, master=None, **kwargs):
        accent = kwargs.pop('style', '') == 'Accent.TButton'
        text = kwargs.get('text', '')
        width = kwargs.pop('width', None)
        kwargs.pop('padding', None)
        super().__init__(master, width=width * 8 if width else max(68, sum(13 if ord(c) > 127 else 7 for c in text) + 26),
                         height=32, corner_radius=9, font=(FONT, 12),
                         fg_color=ACCENT if accent else '#272a2f', hover_color='#a3dabc' if accent else '#363b42',
                         text_color='#15241c' if accent else TEXT, border_width=0 if accent else 1,
                         border_color=BORDER, **kwargs)

class Widgets:
    Button = Button
    def __getattr__(self, name):
        return getattr(native, name)

ttk = Widgets()
