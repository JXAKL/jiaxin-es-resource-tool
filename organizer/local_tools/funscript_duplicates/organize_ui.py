from ..widgets import ttk
from ..surfaces import create_surface
"""Explicit, preview-first GUI for moving one side into the other's directory structure."""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from .organize import build_plan, execute_plan, set_reference, plan_summary
from ...presentation import paint_items, poll_delay


class OrganizeWindow:
    def __init__(self, parent, result, backend=None):
        self.backend = backend
        self.media = '视频' if backend is not None else '脚本'
        self.window = create_surface(parent)
        self.window.title('按参考方目录结构移动重复'+self.media)
        self.window.geometry('1180x790')
        self.window.minsize(920, 680)
        self.window.transient(parent)
        self.window.grab_set()
        self.result = result
        roots = result['folders']
        self.source = tk.StringVar(value=roots[0] if roots else '')
        self.reference = tk.StringVar(value=roots[1] if len(roots) > 1 else '')
        self.output = tk.StringVar()
        self.reference_choice = tk.StringVar()
        self.status = tk.StringVar(value='选择移动方、参考方和专用输出目录，然后预览。只整理“全部数据相同”组。')
        self.plan, self.items = [], {}
        self.busy = self.closed = False
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.build(roots)
        for variable in (self.source, self.reference, self.output):
            variable.trace_add('write', self.invalidate)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_job = self.window.after(100, self.poll)

    def build(self, roots):
        from ...local_ui import build_organize
        build_organize(self, roots)

    def choose(self, variable):
        if not self.busy:
            path = filedialog.askdirectory(parent=self.window, title='选择目录')
            if path:
                variable.set(path)

    def invalidate(self, *_):
        if not self.busy:
            self.plan = []
            self.populate()
            self.move_button.configure(state='disabled')
            self.status.set('目录已改变，请重新生成整理预览。')

    def set_busy(self, busy):
        self.busy = busy
        for widget in (*self.inputs, self.preview_button, self.all_button, self.none_button, self.apply_button):
            widget.configure(state='disabled' if busy else ('readonly' if isinstance(widget, ttk.Combobox) else 'normal'))
        self.move_button.configure(state='normal' if self.plan and not busy else 'disabled')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        self.reference_combo.configure(state='disabled' if busy else 'readonly')

    def preview(self):
        if self.busy:
            return
        args = (self.source.get().strip(), self.reference.get().strip(), self.output.get().strip())
        self.plan = []
        self.populate()
        self.cancel = threading.Event()
        self.set_busy(True)
        self.status.set('正在根据报告生成预览（不会移动文件）…')
        def run():
            try:
                builder = self.backend.build_plan if self.backend is not None else build_plan
                plan = builder(self.result, *args)
                self.preview_summary = plan_summary(self.result, args[0], args[1])
                self.events.put(('preview', plan))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def populate(self):
        self.layout.stats(len(self.plan), sum(bool(row.get('selected')) for row in self.plan), '预览')
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.reference_combo.configure(values=())
        self.reference_choice.set('')
        def insert_row(row):
            item = self.tree.insert('', 'end', values=('☑' if row.get('selected') else '☐', row['source']['path'],
                                     row['reference']['path'] if row.get('reference') else '需选择', row['target'], row['state']))
            self.items[item] = row
        paint_items(self, self.window, self.plan, insert_row)

    def click(self, event):
        if self.busy or self.tree.identify_region(event.x, event.y) != 'cell' or self.tree.identify_column(event.x) != '#1':
            return
        item = self.tree.identify_row(event.y)
        row = self.items.get(item)
        if row and row.get('reference') and row.get('state') != '已移动':
            row['selected'] = not row.get('selected')
            self.tree.set(item, 'checked', '☑' if row['selected'] else '☐')
            self.tree.selection_set(item)
        return 'break'

    def select_all(self, selected):
        if not self.busy:
            for row in self.plan:
                row['selected'] = selected and row['state'] == '就绪'
            for item, row in self.items.items():
                row['selected'] = selected and row['state'] == '就绪'
                self.tree.set(item, 'checked', '☑' if row['selected'] else '☐')

    def select_row(self, event=None):
        selection = self.tree.selection()
        row = self.items.get(selection[0]) if selection else None
        if row:
            self.reference_combo.configure(values=[ref['path'] for ref in row['references']])
            self.reference_choice.set(row['reference']['path'] if row.get('reference') else '')

    def apply_choice(self):
        if self.busy:
            return
        selection = self.tree.selection()
        row = self.items.get(selection[0]) if selection else None
        if row and row['state'] != '已移动':
            try:
                set_reference(row, self.reference_choice.get())
                self.populate()
            except ValueError as exc:
                messagebox.showwarning('参考路径无效', str(exc), parent=self.window)

    def move(self):
        if self.busy:
            return
        count = sum(bool(row.get('selected')) for row in self.plan)
        if not count:
            messagebox.showinfo('未选择', '请先勾选要移动的项；多参考位置的项需先选定参考文件。', parent=self.window)
            return
        if not messagebox.askyesno('确认移动', f'将核验并移动 {count} 个勾选{self.media}到：\n{self.output.get()}\n\n'
                                  '成功后源位置不再保留该文件，参考方不动。目标重名或数据变化将跳过。是否继续？', parent=self.window):
            return
        self.cancel = threading.Event()
        self.set_busy(True)
        self.status.set('正在逐项核验并移动；请勿同时编辑这些文件。大视频需要完整读取，可能耗时较长。')
        def run():
            try:
                executor = self.backend.execute_plan if self.backend is not None else execute_plan
                summary = executor(self.plan, self.cancel, lambda done, total, state: self.events.put(
                    ('progress', f'{done}/{total} · {state}')))
                self.events.put(('moved', summary))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    self.status.set(value)
                elif kind == 'preview':
                    self.plan = value
                    self.set_busy(False)
                    self.populate()
                    self.status.set(self.preview_summary+f'\n预览 {len(value)} 个源文件；多参考位置的项需手动选择。')
                elif kind == 'moved':
                    self.set_busy(False)
                    self.populate()
                    self.status.set(f"{'已停止' if value['stopped'] else '处理结束'}：成功移动 {value['moved']} 项，未完成 {value['failed']} 项。"
                                    f"\n原路径/新路径记录：{value['log_path']}")
                elif kind == 'error':
                    self.set_busy(False)
                    self.populate()
                    self.status.set('未完成：'+value)
                    messagebox.showerror('整理失败', value, parent=self.window)
        except queue.Empty:
            pass
        self.poll_job = self.window.after(poll_delay(self.window, self.busy), self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('操作进行中', '请先停止操作，等待当前项安全结束后再关闭。', parent=self.window)
            return False
        if not self.closed:
            self.closed = True
            self.window.after_cancel(self.poll_job)
            self.window.destroy()
        return True
