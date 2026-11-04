"""All duplicate pages share the workspace's actual navigation state."""
import customtkinter as ctk
from .ui import FONT, FIELD, BORDER

PAGES = {'Funscript':'scripts', '视频':'videos', '文件夹组数':'folders'}


def create_selector(parent, workspace):
    control = ctk.CTkSegmentedButton(parent, values=list(PAGES),
        variable=workspace.duplicate_selection, command=workspace.select_duplicate,
        font=(FONT, 12), fg_color=FIELD, selected_color='#42614f',
        selected_hover_color='#52765f', unselected_color=FIELD,
        unselected_hover_color=BORDER, corner_radius=10)
    control.pack(fill='x', pady=8)
    return control
