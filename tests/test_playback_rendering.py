"""Real Tk canvas checks without launching a media player or a visible window."""
import queue
import sys
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from organizer.local_tools import playback_review as playback


class SmallPlayback(playback.PlaybackReview):
    def build(self):
        self.video_area = tk.Frame(self.window)
        self.curve = tk.Canvas(self.window, width=800, height=155)
        self.heat = tk.Canvas(self.window, width=800, height=42)
        self.slider = Mock()


class PlaybackRenderingTests(unittest.TestCase):
    def setUp(self):
        root = tk.Tk()
        root.withdraw()
        with patch.object(playback, 'create_surface', return_value=root), \
                patch.object(playback, 'player_executable', return_value=Path(sys.executable)), \
                patch.object(playback, 'MpvPlayer', return_value=Mock()):
            self.player = SmallPlayback(root)
        self.addCleanup(self.player.close)
        self.player.script = dict(times=[0., 1., 2.], positions=[0., 100., 0.],
                                  travelled=[0., 100., 200.], gaps=[], last_action=2., duration=4.)
        self.player.duration = 4.
        self.player.loaded_video = 'fixture.avi'
        self.player.curve.winfo_width = Mock(return_value=800)
        self.player.heat.winfo_width = Mock(return_value=800)

    def paint(self):
        self.player.draw()
        self.player.window.after_cancel(self.player._draw_job)
        self.player._paint()

    def poll_once(self):
        self.player.window.after_cancel(self.player._poll_job)
        self.player.poll()

    def event(self, name, data):
        self.player.events.put(dict(event='property-change', name=name, data=data))

    def test_paused_empty_polls_do_not_schedule_or_create_canvas_objects(self):
        self.paint()
        before = self.player.curve.find_all(), self.player.heat.find_all()
        self.assertEqual(len(before[1]), 3)
        with patch.object(self.player, 'draw', wraps=self.player.draw) as draw:
            for _ in range(10):
                self.poll_once()
            self.assertEqual(draw.call_count, 0)
        self.assertEqual(before, (self.player.curve.find_all(), self.player.heat.find_all()))

    def test_heat_sampling_cached_across_resize_and_recomputed_for_inputs(self):
        with patch.object(playback, 'script_speed', wraps=playback.script_speed) as sampling:
            self.paint()
            before = self.player.curve.find_all(), self.player.heat.find_all()
            image = self.player._heat_photo
            for _ in range(5):
                self.paint()
            self.assertEqual(sampling.call_count, 1)
            self.assertIs(self.player._heat_photo, image)
            self.player.curve.winfo_width.return_value = 950
            self.player.heat.winfo_width.return_value = 950
            self.paint()
            self.assertEqual(sampling.call_count, 1)
            self.assertIsNot(self.player._heat_photo, image)
            self.player.offset.set('0.5')
            self.paint()
            self.assertEqual(sampling.call_count, 2)
            self.player.duration = 5.
            self.paint()
            self.assertEqual(sampling.call_count, 3)
            self.assertEqual(before, (self.player.curve.find_all(), self.player.heat.find_all()))

    def test_events_coalesce_and_pause_clock_updates_without_new_position(self):
        self.paint()
        for value in (0.1, 0.25, 0.5):
            self.event('time-pos', value)
        self.event('pause', False)
        self.event('speed', 1.5)
        self.poll_once()
        self.assertEqual(self.player.seconds, .5)
        self.assertEqual(self.player.actual_speed, 1.5)
        self.assertIn('播放中', self.player.clock.get())
        self.assertIsNotNone(self.player._draw_job)
        self.paint()
        dot = self.player.curve.coords(self.player._curve_items['dot'])
        self.assertEqual((dot[1]+dot[3])/2, 80.)
        self.event('pause', True)
        self.poll_once()
        self.assertIn('暂停', self.player.clock.get())
        self.assertIsNone(self.player._draw_job)

    def test_seek_offset_and_close_preserve_controls_and_cancel_jobs(self):
        self.player.seek_to(99)
        self.player.player.send.assert_called_with('seek', 4., 'absolute+exact')
        self.player.seek_to(-2)
        self.player.player.send.assert_called_with('seek', 0, 'absolute+exact')
        self.player.offset.set('0.5')
        self.assertIsNotNone(self.player._draw_job)
        self.paint()
        self.assertEqual(self.player.curve.itemcget(self.player._curve_items['dot'], 'state'), 'hidden')
        self.player.draw_heat()
        player = self.player.player
        self.player.close()
        self.assertIsNone(self.player._draw_job)
        self.assertIsNone(self.player._poll_job)
        player.close.assert_called_once()
        self.player.close()
