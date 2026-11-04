"""Local folder-count organizer, using the shared workbench layout."""
import json
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from . import folder_groups as engine
from .panels import Workbench, section, hint, switch, table, caption, frame
from .ui import button, entry
from .presentation import paint_items, poll_delay


class FolderGroupsPage:
    def __init__(self, window, state):
        self.window, self.state = window, Path(state)
        self.busy = False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.rows, self.scan_paths = [], None
        self.latest = ''
        self.settings_file = self.state / 'folder-group-settings.json'
        try:
            saved = json.loads(self.settings_file.read_text(encoding='utf-8'))
            if not isinstance(saved, dict): saved = {}
        except (OSError, ValueError): saved = {}
        self.root = tk.StringVar(value=saved.get('root', ''))
        self.target = tk.StringVar(value=saved.get('target', ''))
        self.threshold = tk.StringVar(value=saved.get('threshold', '5'))
        self.paired = tk.BooleanVar(value=saved.get('paired', False))
        self.zero = tk.BooleanVar(value=saved.get('zero', False))
        self.status = tk.StringVar(value='选择根目录，统计每个一级子文件夹及其下级目录内的素材组数。')
        self.layout = Workbench(window, '子文件夹组数', self.status, ('子文件夹', '低于阈值', '已勾选'))
        left = self.layout.settings
        section(left, '01', '文件与范围')
        from .duplicate_navigation import create_selector
        self.kind_selector = create_selector(left, window.workspace)
        self.inputs = []
        for title, variable in [('扫描根目录', self.root), ('移动到目录', self.target)]:
            caption(left, title)
            row = frame(left); row.pack(fill='x', pady=(0, 4)); row.columnconfigure(0, weight=1)
            field = entry(row, variable, width=150)
            field.grid(row=0, column=0, sticky='ew', padx=(0, 6))
            choose = button(row, '选择', lambda var=variable:self.choose(var), width=56)
            choose.grid(row=0, column=1)
            self.inputs.extend([field, choose])
        section(left, '02', '筛选条件')
        caption(left, '组数低于此值 · 不含等于')
        field = entry(left, self.threshold); field.pack(fill='x'); self.inputs.append(field)
        self.paired_switch = switch(left, '仅统计视频＋脚本配对组', self.paired, self.refilter)
        self.zero_switch = switch(left, '包含 0 组文件夹', self.zero, self.refilter)
        self.inputs.extend([self.paired_switch, self.zero_switch])
        self.count_hint = hint(left, '同名视频与多轴脚本合算一组。\n默认也计入单独视频或脚本。')
        actions = frame(self.layout.bottom); actions.pack(fill='x', pady=(0, 6))
        actions.columnconfigure(0, weight=1)
        self.scan_button = button(actions, '扫描子文件夹', self.scan)
        self.scan_button.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.stop_button = button(actions, '停止', self.stop, width=64)
        self.stop_button.grid(row=0, column=1)
        self.move_button = button(self.layout.bottom, '移动勾选文件夹', self.move, accent=True)
        self.move_button.pack(fill='x', pady=(0, 6))
        self.undo_button = button(self.layout.bottom, '撤销文件夹移动…', self.restore)
        self.undo_button.pack(fill='x', pady=4)
        self.all_button = button(self.layout.toolbar, '勾选低于阈值', self.refilter, width=118)
        self.all_button.pack(side='right', padx=4)
        self.none_button = button(self.layout.toolbar, '取消勾选', self.clear, width=90)
        self.none_button.pack(side='right', padx=4)
        self.tree = table(self.layout.result, [('check','选择',50), ('name','子文件夹',210),
            ('count','统计组数',85), ('paired','配对组',75), ('video','仅视频',75),
            ('script','仅脚本',75), ('status','状态',240)])
        self.tree.bind('<Button-1>', self.click)
        self.tree.bind('<space>', self.toggle_focus)
        self.tree.tag_configure('checked', background='#23392e')
        hint(self.layout.footer, '点击“选择”列勾选。移动整个文件夹，保留内部文件；重名不覆盖，可撤销。').configure(wraplength=0)
        for var in (self.root, self.target): var.trace_add('write', self.invalidate)
        self.threshold.trace_add('write', lambda *_:self.refilter())
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.controls()
        window.after(100, self.poll)

    def choose(self, variable):
        path = filedialog.askdirectory(parent=self.window, initialdir=variable.get() or None)
        if path: variable.set(path)

    def save(self):
        self.state.mkdir(parents=True, exist_ok=True)
        data = {key:getattr(self,key).get() for key in ('root','target','threshold','paired','zero')}
        temp = self.settings_file.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.settings_file)

    def invalidate(self, *_):
        if self.busy: return
        self.rows, self.scan_paths = [], None
        self.render()
        self.status.set('目录已改变，请重新扫描。')

    def qualifies(self, row):
        try: return engine.eligible(row, engine.threshold_value(self.threshold.get()), self.paired.get(), self.zero.get())
        except ValueError: return False

    def refilter(self):
        if self.busy: return
        try: engine.threshold_value(self.threshold.get())
        except ValueError as exc: self.status.set(str(exc))
        for row in self.rows: row.selected = self.qualifies(row)
        self.render()

    def clear(self):
        if self.busy: return
        for row in self.rows: row.selected = False
        self.render()

    def values(self, row):
        count = row.paired if self.paired.get() else row.groups
        return ('✓' if row.selected else '—', Path(row.source).name, count, row.paired,
                row.video_only, row.script_only, row.error or ('低于阈值' if self.qualifies(row) else '保留'))

    def render(self):
        children = self.tree.get_children()
        if children: self.tree.delete(*children)
        paint_items(self, self.window, list(enumerate(self.rows)),
                    lambda item:self.tree.insert('', 'end', iid=str(item[0]), values=self.values(item[1]),
                        tags=('checked',) if item[1].selected else ()))
        self.controls()

    def controls(self):
        selected = sum(r.selected and self.qualifies(r) for r in self.rows)
        self.layout.stats(len(self.rows), sum(self.qualifies(r) for r in self.rows), selected)
        for widget in self.inputs + [self.scan_button, self.undo_button, self.all_button, self.none_button]:
            widget.configure(state='disabled' if self.busy else 'normal')
        self.move_button.configure(state='normal' if not self.busy and selected else 'disabled')
        self.stop_button.configure(state='normal' if self.busy else 'disabled')

    def toggle(self, item):
        if self.busy or not item: return
        row = self.rows[int(item)]
        if self.qualifies(row):
            row.selected = not row.selected
            self.tree.item(item, values=self.values(row), tags=('checked',) if row.selected else ()); self.controls()

    def click(self, event):
        if self.tree.identify_column(event.x) == '#1': self.toggle(self.tree.identify_row(event.y))

    def toggle_focus(self, _):
        self.toggle(self.tree.focus()); return 'break'

    def checkpoint(self):
        if self.cancel.is_set(): raise engine.Cancelled()

    def progress(self, *text):
        # Only the latest progress is shown, avoiding an unbounded GUI backlog.
        self.latest = ' · '.join(map(str,text))

    def start(self, kind, job):
        if self.busy: return
        try: self.save()
        except OSError as exc: self.status.set(f'无法保存设置：{exc}'); return
        self.task_kind = kind
        self.busy = True; self.cancel.clear(); self.started = time.monotonic()
        self.latest = '正在准备…'; self.controls(); self.layout.progress.start(25)
        def run():
            try: self.events.put((kind, job()))
            except engine.Cancelled: self.events.put(('stopped', None))
            except Exception as exc: self.events.put(('error', str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def scan(self):
        source, target = self.root.get().strip(), self.target.get().strip()
        if not source: self.status.set('请先选择扫描根目录。'); return
        try: engine.threshold_value(self.threshold.get())
        except ValueError as exc: self.status.set(str(exc)); return
        self.scan_paths = (source,target)
        self.start('scan', lambda:engine.scan_folders(source, target, self.checkpoint, self.progress))

    def move(self):
        source, target = self.root.get().strip(), self.target.get().strip()
        if not target: self.status.set('请先选择移动到目录。'); return
        if self.scan_paths != (source,target): self.status.set('目录已改变，请重新扫描。'); return
        threshold, paired, zero = self.threshold.get(), self.paired.get(), self.zero.get()
        self.start('move', lambda:engine.execute(self.rows, source, target, threshold, self.state/'folder-moves',
            paired_only=paired, include_zero=zero, stop=self.checkpoint, progress=self.progress))

    def restore(self):
        path = filedialog.askopenfilename(parent=self.window, title='选择文件夹移动记录',
            initialdir=str(self.state/'folder-moves'), filetypes=[('文件夹移动记录','folders-*.jsonl')])
        if path: self.start('undo', lambda:engine.undo_folders(path, self.checkpoint, self.progress))

    def stop(self):
        self.cancel.set(); self.latest = '正在安全停止，保留已完成的移动及撤销记录…'

    def poll(self):
        try: kind, result = self.events.get_nowait()
        except queue.Empty:
            if self.busy: self.status.set(f'{self.latest} · 已用 {time.monotonic()-self.started:.0f} 秒')
        else:
            self.busy = False; self.layout.progress.stop()
            if kind == 'scan':
                self.rows = result; self.refilter()
                self.status.set(f'扫描完成：{len(result)} 个子文件夹。勾选项将整个移动到目标目录。')
            elif kind in ('move','undo'):
                self.rows, self.scan_paths = [], None; self.render()
                errors = result.get('errors', [])
                count = result.get('moved', result.get('folders',0))
                summary = f'{"移动" if kind=="move" else "撤销"}{"已停止" if result.get("stopped") else "完成"}：{count} 个文件夹，{len(errors)} 个问题。请重新扫描查看现状。'
                if errors:
                    report = self.state/'folder-moves'/'last-errors.txt'
                    report.parent.mkdir(parents=True, exist_ok=True)
                    report.write_text('\n'.join(errors), encoding='utf-8')
                    summary += '\n' + errors[0] + f'（完整记录：{report}）'
                self.status.set(summary)
            elif kind == 'stopped':
                if self.task_kind != 'scan':
                    self.rows, self.scan_paths = [], None; self.render()
                self.status.set('已停止；已完成的移动保留在撤销记录中。请重新扫描查看现状。')
            else: self.status.set(result)
            self.controls()
        self.window.after(poll_delay(self.window, self.busy), self.poll)

    def close(self):
        self.cancel.set()
        self.save()
