from ..widgets import ttk
from ..surfaces import create_surface
from ...presentation import paint_items, poll_delay
"""Read-only duplicate scanning, reloadable reports and explicit preview-first organizing."""
import os
import json
import queue
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

if __package__:
    from .core import scan_folders, report_text, Cancelled
    from .report_io import load_report
    from .organize_ui import OrganizeWindow
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ..funscript_duplicates.core import scan_folders, report_text, Cancelled
    from ..funscript_duplicates.report_io import load_report
    from ..funscript_duplicates.organize_ui import OrganizeWindow


class DuplicateWindow:
    MODES = ('全部数据相同（完全重复）', '仅元数据不同（需核对）', '读取问题 / 跳过项')

    def __init__(self, parent=None, folder='', backend=None, state=None):
        self.backend = backend
        self.is_video = backend is not None
        self.media = '视频' if self.is_video else '脚本'
        self.title = '视频查重' if self.is_video else 'Funscript 查重'
        if self.is_video:
            self.MODES = ('时长与名称相近（需核对）', '完整文件相同（完全重复）', '读取问题 / 跳过项')
        self.window = create_surface(parent)
        if parent is None:
            from ..ui_theme import apply_theme
            apply_theme(self.window)
        self.window.title(self.title+' · 报告与目录整理')
        self.window.geometry('1120x740')
        self.window.minsize(850, 600)
        self.folder = tk.StringVar(value=folder)
        self.scan_mode = tk.StringVar(value='single')
        self.folders = []
        self.mode = tk.StringVar(value=self.MODES[0])
        self.video_options = {}
        self.option_controls = []
        if self.is_video:
            from ..video_duplicates.similarity import DEFAULTS
            self.video_options = {key: tk.StringVar(value=f'{value:g}') for key, value in DEFAULTS.items()}
            from ...runtime import STATE
            self.settings_file = Path(state or STATE)/'video-duplicate-settings.json'
            try:
                from ..video_duplicates.similarity import options
                saved = options(json.loads(self.settings_file.read_text(encoding='utf-8')))
                for key, variable in self.video_options.items():
                    variable.set(f'{saved[key]:g}')
            except (OSError, ValueError, TypeError):
                pass
        self.status = tk.StringVar(value='选择目录后递归扫描所有子目录；扫描只读，不会改动文件。')
        self.detail = tk.StringVar(value='单击文件可查看完整路径；分组内所有文件地位相同，不自动决定保留哪份。')
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = self.closed = False
        self.result = None
        self.items = {}
        self.organize_windows = []
        self.build()
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_job = self.window.after(poll_delay(self.window, self.busy), self.poll)

    def build(self):
        from ...local_ui import build_duplicate
        build_duplicate(self)

    def save(self):
        if self.is_video:
            from ..video_duplicates.similarity import options
            try:
                saved = options({key: variable.get() for key, variable in self.video_options.items()})
                self.settings_file.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.settings_file.with_suffix('.tmp')
                temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(self.settings_file)
            except (OSError, ValueError, TypeError):
                pass

    def choose(self):
        if self.busy:
            return
        selected = filedialog.askdirectory(parent=self.window, title='选择'+self.media+'根目录（包含子目录）')
        if selected:
            self.folder.set(selected)
            self.invalidate_results()

    def invalidate_results(self):
        self.result = None
        self.populate()
        self.export_button.configure(state='disabled')
        self.organize_button.configure(state='disabled')

    def switch_mode(self):
        if self.busy:
            return
        self.invalidate_results()
        if self.scan_mode.get() == 'multiple':
            self.single_panel.grid_remove()
            self.multiple_panel.grid(row=1, column=0, sticky='ew')
            if not self.folders and os.path.isdir(self.folder.get().strip()):
                self.add_folder(self.folder.get().strip())
        else:
            self.multiple_panel.grid_remove()
            self.single_panel.grid(row=1, column=0, sticky='ew')
        self.status.set('模式已切换，请点击“开始查重”扫描。两种模式都包含子目录。')

    def refresh_folders(self):
        self.folder_list.delete(0, 'end')
        for folder in self.folders:
            self.folder_list.insert('end', folder)

    def add_folder(self, path=None):
        if self.busy:
            return
        path = path or filedialog.askdirectory(parent=self.window, title='添加查重目录（可多次添加）')
        if not path:
            return
        if not os.path.isdir(path):
            messagebox.showwarning('目录无效', '请选择存在的文件夹。', parent=self.window)
            return
        resolved = str(Path(path).resolve())
        if os.path.normcase(resolved) in {os.path.normcase(folder) for folder in self.folders}:
            self.status.set('该目录已经在列表中，无需重复添加。')
            return
        self.folders.append(resolved)
        self.refresh_folders()
        self.invalidate_results()
        self.status.set(f'已添加 {len(self.folders)} 个目录；点击开始查重后统一比较。')

    def remove_folders(self):
        if not self.busy:
            for index in reversed(self.folder_list.curselection()):
                del self.folders[index]
            self.refresh_folders()
            self.invalidate_results()
            self.status.set('已移出选中目录；未删除任何文件。请重新开始查重。')

    def clear_folders(self):
        if not self.busy:
            self.folders.clear()
            self.refresh_folders()
            self.invalidate_results()
            self.status.set('目录列表已清空；未删除任何文件。')

    def set_busy(self, busy):
        self.busy = busy
        for widget in (self.start_button, self.choose_button, self.folder_entry, self.add_button,
                       self.remove_button, self.clear_button, self.load_button, *self.scope_buttons, *self.option_controls):
            widget.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        self.export_button.configure(state='normal' if not busy and self.result is not None else 'disabled')
        self.organize_button.configure(state='normal' if not busy and self.result is not None and
                                       (not self.is_video or self.result['exact_groups']) else 'disabled')
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def start(self):
        if self.busy:
            return
        folders = [self.folder.get().strip()] if self.scan_mode.get() == 'single' else list(self.folders)
        if not folders or any(not folder or not os.path.isdir(folder) for folder in folders):
            messagebox.showwarning('目录无效', '请至少选择一个有效目录；列表中的每个目录都必须存在。', parent=self.window)
            return
        settings = None
        if self.is_video:
            from ..video_duplicates.similarity import options
            try:
                settings = options({key: variable.get() for key, variable in self.video_options.items()})
            except (TypeError, ValueError):
                messagebox.showwarning('查重条件无效', '时长秒数须为 0～60，百分比须为 0～10，名称相似度须为 50～100。', parent=self.window)
                return
            self.save()
        self.result = None
        self.populate()
        self.cancel = threading.Event()
        self.set_busy(True)
        self.status.set('正在递归扫描并比较内容…')
        threading.Thread(target=self.worker, args=(folders, self.cancel, self.scan_mode.get(), settings), daemon=True).start()

    def worker(self, folders, cancel, scan_mode, settings=None):
        try:
            scanner = self.backend.scan_folders if self.is_video else scan_folders
            result = scanner(folders, cancel, lambda count, valid, path: self.events.put(
                ('progress', path if self.is_video else f'已检查 {count} 份，有效 {valid} 份；正在扫描 {len(folders)} 个目录及子目录…')),
                scan_mode=scan_mode, **(dict(settings=settings) if self.is_video else {}))
            self.events.put(('done', result))
        except Cancelled:
            self.events.put(('stopped', None))
        except Exception as exc:
            self.events.put(('error', str(exc)))

    def import_report(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.window, title='读取已保存的'+self.title+' TXT 报告',
                                          filetypes=[('查重报告', '*.txt')])
        if not path:
            return
        self.invalidate_results()
        self.cancel = threading.Event()
        self.set_busy(True)
        self.status.set('正在读取报告（不扫描文件目录）…')
        def run():
            try:
                loader = self.backend.load_report if self.is_video else load_report
                result = loader(path)
                self.events.put(('loaded', result))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def open_organize(self):
        if not self.busy and self.result is not None:
            for window in self.organize_windows:
                if not window.closed:
                    window.window.lift()
                    return
            self.organize_windows.append(OrganizeWindow(self.window, self.result, backend=self.backend))

    def is_moving(self):
        return any(not window.closed and window.busy for window in self.organize_windows)

    def stop(self):
        if self.busy:
            self.cancel.set()
            self.status.set('正在停止…未完成的扫描不会当作完整结果导出。')

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    if not self.cancel.is_set():
                        self.status.set(value)
                elif kind == 'done':
                    self.result = value
                    self.set_busy(False)
                    self.populate()
                    groups = value['exact_groups']
                    copies = sum(len(group)-1 for group in groups)
                    scope = '多文件夹联合' if value['scan_mode'] == 'multiple' else '单文件夹'
                    if self.is_video:
                        self.status.set(f"{scope}完成：枚举 {value['scanned']} 个视频，完整校验 {value['hashed_files']} 个；"
                                        f"时长/名称相近 {len(value.get('similar_groups', []))} 组；完全重复 {len(groups)} 组（多出 {copies} 份）；"
                                        f"文件问题 {len(value['issues'])} / 视频头问题 {len(value.get('media_issues', []))} 项。")
                    else:
                        self.status.set(f"{scope}扫描完成（{len(value['folders'])} 个目录）：{value['scanned']} 份，有效 {value['valid']} 份；完全重复 {len(groups)} 组"
                                        f"（比每组保留 1 份多出 {copies} 份）；仅元数据不同 {len(value['metadata_groups'])} 组；"
                                        f"问题/跳过 {len(value['issues'])} 项。两类分组可能重叠。")
                elif kind == 'loaded':
                    if self.cancel.is_set():
                        self.set_busy(False)
                        self.status.set('已取消读取报告。')
                        continue
                    self.result = value
                    self.folders = list(value['folders'])
                    self.refresh_folders()
                    self.folder.set(value['folders'][0])
                    self.scan_mode.set(value['scan_mode'])
                    if value['scan_mode'] == 'multiple':
                        self.single_panel.grid_remove()
                        self.multiple_panel.grid(row=1, column=0, sticky='ew')
                    else:
                        self.multiple_panel.grid_remove()
                        self.single_panel.grid(row=1, column=0, sticky='ew')
                    self.mode.set(self.MODES[0])
                    if self.is_video:
                        for key, variable in self.video_options.items():
                            variable.set(f"{value['settings'][key]:g}")
                    self.set_busy(False)
                    self.populate()
                    self.status.set(f"报告已读取：完全重复 {len(value['exact_groups'])} 组。"+
                                    (f"时长/名称相近 {len(value.get('similar_groups', []))} 组。" if self.is_video else '')+
                                    "未重新扫描；整理时仅核验选中的源/参考文件。")
                elif kind == 'stopped':
                    self.set_busy(False)
                    self.status.set('已停止；未改动任何文件。请重新扫描以获得完整结果。')
                elif kind == 'error':
                    self.set_busy(False)
                    self.status.set('扫描失败：'+value)
                    messagebox.showerror('查重失败', value, parent=self.window)
        except queue.Empty:
            pass
        self.poll_job = self.window.after(poll_delay(self.window, self.busy), self.poll)

    def populate(self):
        self._paint_generation = getattr(self, '_paint_generation', 0) + 1
        result = self.result or {}
        if self.is_video:
            self.layout.stats(result.get('scanned', 0), len(result.get('similar_groups', [])), len(result.get('exact_groups', [])))
        else:
            self.layout.stats(result.get('scanned', 0), len(result.get('exact_groups', [])), len(result.get('issues', [])))
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.detail.set('单击文件查看完整路径；导出报告包含全部分组和读取问题。')
        if self.result is None:
            return
        key = ('similar_groups' if self.mode.get() == self.MODES[0] else 'exact_groups') if self.is_video else (
            'exact_groups' if self.mode.get() == self.MODES[0] else 'metadata_groups')
        issues = self.mode.get() == self.MODES[-1]
        data = self.result
        def rows():
            if issues:
                for index, item in enumerate(data['issues'] + (data.get('media_issues', []) if self.is_video else [])):
                    yield '', f'i{index}', Path(item['path']).name, ('', '', '', item['error']) if self.is_video else ('', '', item['error']), (), item
            else:
                for index, group in enumerate(data.get(key, []), 1):
                    if key == 'similar_groups':
                        description = '均与第一项直接比较：时长及名称达标；单击查看文件信息'
                    else:
                        byte_equal = len({item['byte_hash'] for item in group}) == 1
                        description = '原始字节也完全相同' if byte_equal else ('排版/编码等不同，全部 JSON 数据相同' if key == 'exact_groups' else '仅 metadata 不同，不是完全重复')
                    parent = f'g{index}'
                    yield '', parent, f'组 {index:04d} · {len(group)} 份', ('', '', '', description) if self.is_video else ('', '', description), ('group',), None
                    for number, item in enumerate(group):
                        if self.is_video:
                            duration = f"{item['duration']:.3f} 秒" if item.get('duration') else '未读到'
                            score = f"{item['name_score']:.1f}%" if key == 'similar_groups' else '完整指纹一致'
                            values = (item['size'], duration, score, item['path'])
                        else:
                            values = (item['size'], item['actions'], item['path'])
                        yield parent, f'{parent}-{number}', item['name'], values, (), item
        def insert_row(row):
            parent, node, text, values, tags, item = row
            self.tree.insert(parent, 'end', iid=node, text=text, values=values, open=True, tags=tags)
            if item is not None:
                self.items[node] = item
        paint_items(self, self.window, rows(), insert_row)

    def selected(self):
        selection = self.tree.selection()
        return self.items.get(selection[0]) if selection else None

    def select(self, event=None):
        item = self.selected()
        if item:
            self.detail.set(item['path'] + ('\n'+(item.get('error') or item.get('note'))
                                           if item.get('error') or item.get('note') else ''))

    def copy_path(self):
        item = self.selected()
        if item:
            self.window.clipboard_clear()
            self.window.clipboard_append(item['path'])

    def open_folder(self):
        item = self.selected()
        if item:
            directory = Path(item['path']).parent
            try:
                os.startfile(str(directory))
            except OSError as exc:
                messagebox.showerror('无法打开文件夹', str(exc), parent=self.window)

    def export(self):
        if self.busy or self.result is None:
            return
        path = filedialog.asksaveasfilename(parent=self.window, title='导出查重报告',
                                           initialfile='视频查重报告.txt' if self.is_video else 'Funscript查重报告.txt', defaultextension='.txt',
                                           filetypes=[('文本报告', '*.txt')])
        if path:
            try:
                if Path(path).suffix.casefold() != '.txt':
                    raise ValueError('报告必须保存为 .txt，不能覆盖源文件')
                formatter = self.backend.report_text if self.is_video else report_text
                Path(path).write_text(formatter(self.result), encoding='utf-8-sig')
                self.status.set('报告已导出：'+path)
            except (OSError, ValueError) as exc:
                messagebox.showerror('导出失败', str(exc), parent=self.window)

    def close(self):
        if self.is_moving():
            messagebox.showinfo('整理进行中', '请先停止整理，等待安全结束后再关闭。', parent=self.window)
            return False
        if not self.closed:
            self.save()
            for window in self.organize_windows:
                window.close()
            self.closed = True
            self.cancel.set()
            self.window.after_cancel(self.poll_job)
            self.window.destroy()
        return True


if __name__ == '__main__':
    DuplicateWindow().window.mainloop()
