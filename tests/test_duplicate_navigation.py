import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
import customtkinter as ctk
import app
from organizer import workspace


def selector(surface):
    pending = [surface]
    while pending:
        widget = pending.pop()
        if isinstance(widget, ctk.CTkSegmentedButton) and '文件夹组数' in widget.cget('values'):
            return widget
        pending.extend(widget.winfo_children())
    raise AssertionError('Missing duplicate navigation')


class DuplicateNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        state = Path(self.temp.name)
        self.patches = [patch.object(app, 'STATE', state), patch.object(workspace, 'STATE', state)]
        for item in self.patches: item.start(); self.addCleanup(item.stop)
        self.root = tk.Tk(); self.root.withdraw()
        self.addCleanup(self.close_root)
        self.shell = workspace.Workspace(self.root)

    def close_root(self):
        for job in self.root.tk.call('after','info'): self.root.tk.call('after','cancel',job)
        self.root.destroy()

    def assert_page(self, key, label):
        self.assertEqual(self.shell.current,key)
        self.assertIs(self.shell.displayed,self.shell.pages[key])
        control = selector(self.shell.pages[key])
        self.assertEqual(control.get(),label)
        for name, button in control._buttons_dict.items():
            self.assertEqual(button.cget('fg_color'), control.cget('selected_color' if name==label else 'unselected_color'))

    def test_repeated_clicks_track_actual_page_and_highlight(self):
        self.shell.show('scripts')
        targets = [('videos','视频'),('scripts','Funscript'),('videos','视频'),
                   ('folders','文件夹组数'),('scripts','Funscript'),('folders','文件夹组数')]*10
        for index,(key,label) in enumerate(targets):
            # Exercise the real click handler including its delayed animation.
            selector(self.shell.pages[self.shell.current])._buttons_dict[label]._clicked()
            if index%5==0: self.root.update()
            self.assert_page(key,label)
        self.shell.show('author'); self.shell.show('duplicates')
        self.assert_page('folders','文件夹组数')

    def test_rejected_switch_restores_highlight(self):
        self.shell.show('folders'); self.shell.folders.busy = True
        try:
            for label in ('视频','Funscript','视频'):
                selector(self.shell.pages['folders'])._buttons_dict[label].invoke()
                self.assert_page('folders','文件夹组数')
        finally: self.shell.folders.busy = False
        selector(self.shell.pages['folders'])._buttons_dict['视频'].invoke()
        self.assert_page('videos','视频')


if __name__=='__main__': unittest.main()
