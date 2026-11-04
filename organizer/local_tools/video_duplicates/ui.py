from ..widgets import ttk
from ..surfaces import create_surface
"""Double-click entry point and embedded video duplicate window."""
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ..video_duplicates import backend
from ..funscript_duplicates.ui import DuplicateWindow


class VideoDuplicateWindow(DuplicateWindow):
    def __init__(self, parent=None, folder=''):
        super().__init__(parent, folder, backend=backend)


if __name__ == '__main__':
    VideoDuplicateWindow().window.mainloop()
