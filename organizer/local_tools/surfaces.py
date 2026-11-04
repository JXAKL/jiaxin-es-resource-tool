"""Route review windows into the single desktop workspace."""
import tkinter as tk
from ..runtime import BUNDLE, ROOT, FROZEN

def create_surface(parent):
    if getattr(parent, 'workspace', None) is not None:
        return parent.workspace.push_surface(parent)
    return tk.Toplevel(parent) if parent is not None else tk.Tk()

def player_executable():
    bundled = BUNDLE / 'media' / 'mpv.exe'
    if bundled.is_file() or FROZEN:
        return bundled
    prepared = ROOT / '.build-assets' / 'media' / 'mpv.exe'
    if prepared.is_file():
        return prepared
    return ROOT.parent / 'OSR' / 'mpv.exe'
