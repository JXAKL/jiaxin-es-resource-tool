import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer.downloads.model import Item, load_queue, save_queue
from organizer.downloads.ui import DownloadPage


class DownloadQueueUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.rows = [Item(f'https://gofile.io/d/Fixture{i}', status=status) for i, status in
                     enumerate(('待处理', '失败', '完成', '需核对', '下载中'))]
        save_queue(self.folder / 'download-queue.json', self.rows, {})
        self.page = DownloadPage(self.root, self.folder)
        self.root.update()

    def tearDown(self):
        for job in self.root.tk.call('after', 'info'):
            self.root.tk.call('after', 'cancel', job)
        self.root.destroy()
        self.temp.cleanup()

    def test_multiselect_remove_persist_and_clear_all_states(self):
        page = self.page
        self.assertEqual(str(page.tree.cget('selectmode')), 'extended')
        page.request_interval.set('12.5'); page.per_minute.set('4')
        page.tree.selection_set([self.rows[0].key, self.rows[1].key])
        page.remove_selected()
        self.assertEqual(len(page.items), 3)
        restored, options = load_queue(self.folder / 'download-queue.json')
        self.assertEqual(len(restored), 3)
        self.assertEqual(options['requestInterval'], '12.5')
        self.assertEqual(options['perMinute'], '4')
        page.select_all()
        self.assertEqual(len(page.tree.selection()), 3)
        # Clearing removes even uncertain/interrupted tasks, but only queue data.
        preserved = self.folder / 'resource_download_state.json'
        preserved.write_text('keep transfer history')
        page.links.insert('1.0', 'https://gofile.io/d/OldInput')
        page.clear_all()
        self.root.update()
        self.assertEqual(page.items, [])
        self.assertEqual(page.by_url, {})
        self.assertEqual(page.tree.get_children(), ())
        self.assertEqual(load_queue(self.folder / 'download-queue.json')[0], [])
        self.assertEqual(page.links.get('1.0', 'end').strip(), '')
        self.assertEqual(preserved.read_text(), 'keep transfer history')
        self.assertEqual(page.add_urls(self.rows[0].url), 1)

    def test_clear_completed_busy_and_failed_save_preserve_queue(self):
        page = self.page
        page.clear_completed()
        self.assertEqual(len(page.items), 4)
        page.busy = True
        page.clear_all()
        self.assertEqual(len(page.items), 4)
        page.busy = False
        with patch('organizer.downloads.ui.save_queue', side_effect=OSError('fixture error')), \
             patch('organizer.downloads.ui.messagebox.showerror') as error:
            page.clear_all()
            error.assert_called_once()
        self.assertEqual(len(page.items), 4)
        self.assertEqual(len(page.tree.get_children()), 4)

    def test_clearing_during_chunked_paint_cannot_resurrect_rows(self):
        self.page.add_items([Item(f'https://gofile.io/d/Many{i}') for i in range(1500)])
        self.page.clear_all()
        for _ in range(4):
            self.root.after(15, self.root.quit)
            self.root.mainloop()
        self.assertEqual(self.page.tree.get_children(), ())
        self.assertEqual(load_queue(self.folder / 'download-queue.json')[0], [])

    def test_select_all_includes_rows_not_yet_painted(self):
        self.page.add_items([Item(f'https://gofile.io/d/Many{i}') for i in range(1500)])
        self.page.select_all()
        self.assertEqual(len(self.page.tree.selection()), 1505)
        self.page.remove_selected()
        self.assertEqual(self.page.items, [])

    def test_progress_bursts_coalesce_without_losing_completed_state(self):
        page = self.page
        item = page.items[0]
        for value in range(100):
            page.events.put({'type':'progress','url':item.url,'downloaded':value,'total':100})
        page.events.put({'type':'task','url':item.url,'status':'完成','detail':'saved'})
        page.events.put({'type':'summary','completed':1,'failed':0})
        with patch.object(page, 'update_item', wraps=page.update_item) as update:
            while not page.events.empty():
                page.poll()
            self.assertLess(update.call_count, 10)
        self.assertEqual(item.status, '完成')
        self.assertEqual(page.tree.set(item.key,'status'), '完成')
        self.assertIn('成功 1', page.status.get())

    def test_upgrade_settings_validation_roundtrip_and_sessions(self):
        from organizer.downloads.ui import QUALITY, MEGA_MODES
        page=self.page
        page.quality.set(QUALITY['720']);page.mega_mode.set(MEGA_MODES['local'])
        page.rate_limit.set('1.25');page.mega_connections.set('3');page.proxy.set('http://127.0.0.1:7890')
        options=page.options()
        self.assertEqual(options['rateLimit'],1310720);self.assertEqual(options['quality'],'720')
        self.assertEqual(options['megaMode'],'local');self.assertEqual(options['megaConnections'],3)
        page.events.put(dict(type='session',platform='iwara',cookies=2,checkedAt='2026-10-14T10:00:00Z'))
        page.poll();page.save()
        self.assertIn('已保存网站会话',page.session_note.get())
        self.assertIn('由官网确认',page.session_note.get())
        saved=load_queue(self.folder/'download-queue.json')[1]
        self.assertEqual(saved['rateLimitMiB'],'1.25');self.assertEqual(saved['quality'],'720')
        self.assertEqual(saved['sessions']['iwara']['cookies'],2)
        for field,value in [(page.rate_limit,'nan'),(page.rate_limit,'-1'),(page.mega_connections,'0'),(page.mega_connections,'9'),(page.proxy,'http://user:pass@localhost')]:
            before=field.get();field.set(value)
            with self.assertRaises(ValueError):page.options()
            field.set(before)

    def test_run_selected_only_and_cloud_local_history_separation(self):
        from organizer.downloads.ui import MEGA_MODES
        page=self.page
        page.tree.selection_set(self.rows[1].key)
        with patch.object(page,'run') as run:
            page.selected_button.invoke();self.assertEqual([x.key for x in run.call_args.args[0]],[self.rows[1].key])
        page.add_items([Item('https://mega.nz/file/Fixture#Key',status='需核对')]);page.tree.selection_set(page.items[-1].key)
        page.items[-1].status='需核对'  # Imported links intentionally start pending; simulate a completed cloud submission.
        page.mega_mode.set(MEGA_MODES['cloud']);page.mega.set(True)
        with patch.object(page,'run') as run:page.selected_button.invoke();run.assert_not_called()
        page.mega_mode.set(MEGA_MODES['local'])
        with patch.object(page,'run') as run:page.selected_button.invoke();self.assertEqual(run.call_args.args[0][0].platform,'MEGA')
        page.controls(True)
        self.assertEqual(page.selected_button.cget('state'),'disabled')
