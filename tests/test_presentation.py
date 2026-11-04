import unittest
from types import SimpleNamespace
from unittest.mock import patch
from organizer.presentation import paint_items


class Surface:
    active = True
    def __init__(self):
        self.callbacks = []
        self.exists = True
    def winfo_exists(self):
        return self.exists
    def after(self, delay, callback):
        self.callbacks.append(callback)
    def drain(self):
        while self.callbacks:
            self.callbacks.pop(0)()


class TableSchedulingTests(unittest.TestCase):
    def test_large_result_yields_and_eventually_draws_every_item(self):
        surface, owner, rows, done = Surface(), SimpleNamespace(), [], []
        with patch('organizer.presentation.time.perf_counter', side_effect=(i*.001 for i in range(1000))):
            paint_items(owner, surface, range(100), rows.append, lambda: done.append(True))
            self.assertLess(len(rows), 100)
            self.assertTrue(surface.callbacks)
            surface.drain()
        self.assertEqual(rows, list(range(100)))
        self.assertEqual(done, [True])

    def test_replaced_result_cancels_old_pending_rows(self):
        surface, owner, rows, done = Surface(), SimpleNamespace(), [], []
        with patch('organizer.presentation.time.perf_counter', side_effect=(i*.001 for i in range(1000))):
            paint_items(owner, surface, range(100), rows.append, lambda: done.append('old'))
            rows.clear()
            paint_items(owner, surface, ['new'], rows.append, lambda: done.append('new'))
            surface.drain()
        self.assertEqual(rows, ['new'])
        self.assertEqual(done, ['new'])

    def test_destroyed_page_cancels_pending_paint(self):
        surface, rows = Surface(), []
        with patch('organizer.presentation.time.perf_counter', side_effect=(i*.001 for i in range(1000))):
            paint_items(SimpleNamespace(), surface, range(100), rows.append)
            before = len(rows)
            surface.exists = False
            surface.drain()
        self.assertEqual(len(rows), before)
