import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from tkinter import ttk
from organizer import display_settings as settings


class DisplayTests(unittest.TestCase):
    def test_validate_atomic_save_and_corrupt_file_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'ui-settings.json'
            values = {**settings.DEFAULTS, 'table_font': 13, 'row_height': 40}
            settings.save(file, values)
            self.assertEqual(settings.load(file), values)
            before = file.read_bytes()
            for bad in ({'table_font': 'nan'}, {'row_height': 10000}, {'table_font': 16, 'row_height': 26}):
                with self.assertRaises(ValueError):
                    settings.save(file, bad)
                self.assertEqual(file.read_bytes(), before)
            file.write_text('[]')
            self.assertEqual(settings.load(file), settings.DEFAULTS)

    def test_inline_controls_apply_to_existing_and_lazy_pages(self):
        import app
        import organizer.workspace as module
        with tempfile.TemporaryDirectory() as folder, patch.object(module, 'STATE', Path(folder)), patch.object(app, 'STATE', Path(folder)):
            root = tk.Tk(); root.withdraw()
            try:
                shell = module.Workspace(root)
                shell.toggle_display_settings()
                shell.display_values['table_font'].set('13')
                shell.display_values['row_height'].set('42')
                shell.display_values['sidebar_width'].set('360')
                shell.display_values['log_lines'].set('7')
                self.assertTrue(shell.apply_display_settings())
                self.assertEqual(int(ttk.Style(root).lookup('Treeview', 'rowheight')), 42)
                self.assertEqual(shell.author.log.cget('height'), 7)
                shell.show('downloads')
                self.assertEqual(shell.downloads.layout.sidebar.cget('width'), 360)
                self.assertEqual(shell.downloads.log.cget('height'), 140)
                self.assertEqual(settings.load(Path(folder)/'ui-settings.json')['table_font'], 13)
                shell.reset_display_settings()
                self.assertEqual(root._es_display, settings.DEFAULTS)
                # Verify settings remain within the minimum supported window.
                root.geometry('1180x820+30000+30000'); root.attributes('-alpha', 0); root.deiconify(); root.update()
                for widget in shell.display_bar.winfo_children():
                    self.assertLessEqual(widget.winfo_x()+widget.winfo_width(), shell.display_bar.winfo_width())
            finally:
                for job in root.tk.call('after', 'info'):
                    root.tk.call('after', 'cancel', job)
                root.destroy()

    def test_visible_log_is_bounded(self):
        root=tk.Tk(); root.withdraw()
        try:
            root._es_display={**settings.DEFAULTS, 'log_limit':100}
            text=tk.Text(root)
            settings.append_log(text, ''.join(f'line {i}\n' for i in range(300)))
            contents=text.get('1.0','end-1c').splitlines()
            self.assertEqual(len(contents),100)
            self.assertEqual(contents[-1], 'line 299')
            self.assertEqual(text.cget('state'),'disabled')
        finally: root.destroy()
