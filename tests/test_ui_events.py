import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch
from organizer.ui_events import UIEventQueue, LogText


class UIEventsTests(unittest.TestCase):
    def test_previews_coalesce_but_control_boundaries_and_logs_are_preserved(self):
        events = UIEventQueue()
        for i in range(1000):
            events.put(('matches', [i]))
            events.put(('progress', i))
            events.put(('log', str(i)))
        self.assertEqual(events.qsize(), 1002)
        events.put(('finished', None))
        events.put(('matches', ['next operation']))
        self.assertEqual(events.get_nowait(), ('matches', [999]))
        self.assertEqual(events.get_nowait(), ('progress', 999))
        for i in range(1000):
            self.assertEqual(events.get_nowait(), ('log', str(i)))
        self.assertEqual(events.get_nowait(), ('finished', None))
        self.assertEqual(events.get_nowait(), ('matches', ['next operation']))
        self.assertTrue(events.empty())

    def test_twenty_thousand_logs_finish_without_losing_results_or_disk_records(self):
        import app
        from organizer.model import FileGroup, Match
        with tempfile.TemporaryDirectory() as folder, patch.object(app, 'STATE', Path(folder)):
            root = tk.Tk(); root.withdraw()
            try:
                ui = app.App(root)
                expected = [Match(FileGroup('fixture', 'Fixture'), '人工指定', [], author='FixtureAuthor', selected=True)]
                ui.emit('matches', expected)
                first = LogText('BEGIN BURST')
                ui.events.put(('log', first))
                for i in range(20000):
                    ui.emit('log', f'BURST {i}')
                    if i % 100 == 0:
                        ui.emit('matches', expected)
                        ui.emit('progress', (i,20000,'fixture'))
                ui.emit('progress', (20000,20000,'fixture'))
                ui.emit('finished', None)
                polls = 0
                with patch.object(ui, 'render', wraps=ui.render) as render:
                    while not ui.events.empty():
                        ui.poll(); polls += 1
                        self.assertLess(polls, 30)
                    self.assertEqual(render.call_count, 1)
                records = (Path(folder)/'logs/runtime.log').read_text(encoding='utf-8').splitlines()
                self.assertEqual(len(records),20001)
                self.assertTrue(records[0].startswith(first.created.isoformat(timespec='seconds')))
                self.assertTrue(records[-1].endswith('BURST 19999'))
                self.assertIs(ui.matches, expected)
                self.assertTrue(ui.matches[0].selected)
                self.assertEqual(ui.move_btn.cget('state'), 'normal')
                self.assertIn('任务已结束', ui.status.get())
                self.assertLessEqual(len(ui.log.get('1.0','end-1c').splitlines()), 1000)
                print(f'UI_BURST_OK: 20000 logs, {polls} UI slices, one preview repaint; all disk records preserved')
            finally:
                for job in root.tk.call('after', 'info'):
                    root.tk.call('after', 'cancel', job)
                root.destroy()
