"""Resource completion in the shared dark workspace; no media mutations."""
import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from .panels import *
from .completion import CompletionOptions, complete_resources, inventory, build_rows, export_report
from .collector import atomic_text
from .forum import BrowserClient, Cancelled
from .runtime import LEGACY
from .store import Store
from .resource_info import RESOURCE_LABELS
from .presentation import paint_items, poll_delay


class CompletionPage:
    def __init__(self, surface, author_app, state_dir):
        self.window, self.author_app, self.state_dir = surface, author_app, Path(state_dir)
        self.busy = False
        self.cancel, self.resume, self.events = threading.Event(), threading.Event(), queue.Queue()
        self.folder = tk.StringVar()
        self.author = tk.StringVar(value=author_app.seed.get().split(',')[0].strip())
        self.output = tk.StringVar(value=str(self.state_dir.parent / 'reports' / '资源补齐'))
        self.threshold = tk.StringVar(value='85')
        self.refresh = tk.BooleanVar(value=False)
        self.export_downloads = tk.BooleanVar(value=True)
        self.export_scope = tk.StringVar(value='全部缺失组')
        self.include_review = tk.BooleanVar(value=False)
        self.last_state = None
        self.status = tk.StringVar(value='选择作者素材文件夹，递归检查视频与脚本；再按作者采集补齐线索。')
        self.detail = tk.StringVar(value='报告区分缺视频、缺脚本；不会自动下载、购买或移动素材。')
        self.rows, self.current_output = {}, None
        self._load()
        self.build()
        surface.protocol('WM_DELETE_WINDOW', self.close)
        surface.after(100, self.poll)

    def _load(self):
        try:
            data = json.loads((self.state_dir / 'completion-settings.json').read_text(encoding='utf-8'))
            for name in ('folder', 'author', 'output', 'threshold', 'export_scope'):
                if isinstance(data.get(name), str):
                    getattr(self, name).set(data[name])
            for name in ('export_downloads', 'include_review'):
                if isinstance(data.get(name), bool):
                    getattr(self, name).set(data[name])
        except (OSError, ValueError):
            pass

    def save(self):
        atomic_text(self.state_dir / 'completion-settings.json', json.dumps(
            {name: getattr(self, name).get() for name in ('folder', 'author', 'output', 'threshold',
                'export_downloads', 'export_scope', 'include_review')}, ensure_ascii=False))

    def export_options(self):
        scopes = {'全部缺失组':'all', '只导出缺视频':'video', '只导出缺脚本':'script'}
        if self.export_scope.get() not in scopes:
            raise ValueError('请选择有效的下载链接范围。')
        return dict(enabled=self.export_downloads.get(), scope=scopes[self.export_scope.get()],
                    include_review=self.include_review.get())

    def choose(self, var):
        path = filedialog.askdirectory(parent=self.window)
        if path:
            var.set(path)

    def build(self):
        self.layout = Workbench(self.window, '缺失资源清单', self.status, ('素材总组数', '缺脚本', '缺视频'))
        left = self.layout.settings
        section(left, '01', '素材与作者')
        path_field(left, '作者素材文件夹 · 包含子文件夹', self.folder, lambda: self.choose(self.folder))
        caption(left, 'ES 作者主页或用户名')
        entry(left, self.author).pack(fill='x')
        path_field(left, '报告保存目录', self.output, lambda: self.choose(self.output))
        section(left, '02', '核对方式')
        caption(left, '帖子匹配阈值 · 50–100')
        entry(left, self.threshold).pack(fill='x')
        switch(left, '刷新作者索引与首帖', self.refresh)
        hint(left, '默认复用已采集内容，失败项会重试。多轴脚本算一组；附加标签或元数据名称明确对应时合并本地组。')
        hint(left, '分别核对视频和脚本的付费说明，保留分类、标签及首帖证据；没找到资源或无权访问不会直接标为付费。')
        section(left, '03', '下载链接导出')
        switch(left, '生成按域名下载 TXT', self.export_downloads)
        combo(left, self.export_scope, ['全部缺失组', '只导出缺视频', '只导出缺脚本'], width=250).pack(fill='x', pady=5)
        switch(left, '包含待核对候选链接', self.include_review)
        hint(left, '下载用 TXT 开头含说明，资源链接独占一行。购买入口与暂不支持的链接另存“手动获取链接”。已有结果可直接重新导出。')
        section(left, '04', '共用网站设置')
        app = self.author_app
        combo(left, app.browser_choice, ['Chromium', 'Chrome', 'Edge'], width=250).pack(fill='x')
        switch(left, '显示浏览器', app.visible)
        for title, var in [('请求间隔（秒）', app.request_interval), ('每分钟请求上限 · 0 不限', app.per_minute)]:
            caption(left, title); entry(left, var).pack(fill='x')
        hint(left, '与链接采集共用登录、索引、限速和自动重试。每轮单独保存 HTML、TXT、CSV、JSON 和按域名链接清单。')
        self.start_button = button(self.layout.bottom, '扫描并采集补齐', self.start, accent=True)
        self.start_button.pack(fill='x')
        row = frame(self.layout.bottom); row.pack(fill='x', pady=8)
        self.scan_button = button(row, '只扫描本地', lambda: self.start(local_only=True), width=122)
        self.scan_button.pack(side='left')
        self.stop_button = button(row, '停止', self.cancel.set, width=90, state='disabled')
        self.stop_button.pack(side='right')
        self.resume_button = button(self.layout.bottom, '已完成网站验证，继续', self.resume.set, state='disabled')
        self.resume_button.pack(fill='x')
        self.open_button = button(self.layout.toolbar, '打开报告', self.open_report, width=102)
        self.open_button.pack(side='right')
        self.export_button = button(self.layout.toolbar, '重新导出', self.reexport, width=102)
        self.export_button.pack(side='right', padx=6)
        self.links_button = button(self.layout.toolbar, '链接目录', self.open_links, width=102)
        self.links_button.pack(side='right')
        text_box(self.layout.footer, self.detail, 140).pack(fill='x', pady=8)
        self.tree = table(self.layout.result, [('kind', '缺少类型', 80), ('title', '本地名称', 265),
            ('status', '核对状态', 180), ('paid', '付费信息', 210), ('posts', '候选帖', 65)])
        self.tree.bind('<<TreeviewSelect>>', self.select)

    def check(self):
        if self.cancel.is_set():
            raise Cancelled('已停止')

    def emit(self, kind, value):
        self.events.put((kind, value))

    def controls(self):
        for control in (self.start_button, self.scan_button):
            control.configure(state='disabled' if self.busy else 'normal')
        self.export_button.configure(state='disabled' if self.busy else 'normal')
        self.stop_button.configure(state='normal' if self.busy else 'disabled')
        if self.busy:
            self.layout.progress.start(14)
        else:
            self.layout.progress.stop()
            self.resume_button.configure(state='disabled')

    def start(self, local_only=False):
        if self.busy:
            return
        try:
            folder = Path(self.folder.get().strip())
            if not self.folder.get().strip() or not folder.is_dir():
                raise ValueError('请选择存在的作者素材文件夹。')
            options = CompletionOptions(folder.resolve(), self.author.get(), Path(self.output.get()).resolve(),
                                        float(self.threshold.get()), self.refresh.get())
            exported = self.export_options()
            options.export_downloads = exported['enabled']
            options.export_scope = exported['scope']
            options.include_review = exported['include_review']
            if not local_only:
                if not self.output.get().strip():
                    raise ValueError('请选择报告保存目录。')
                options.validate()
                app = self.author_app; app.network_options(); app._save()
                network = dict(headful=app.visible.get(), browser_channel=app._browser_channel,
                               interval=app._request_interval, search_interval=app._search_interval,
                               per_minute=app._per_minute, cooldown_seconds=app._cooldown_seconds)
            self.save()
        except (ValueError, OSError) as exc:
            messagebox.showerror('设置无效', str(exc), parent=self.window)
            return
        self.busy = True; self.cancel.clear(); self.resume.clear(); self.controls()
        self.status.set('正在扫描作者文件夹及子文件夹…')
        def run():
            try:
                if local_only:
                    groups, missing = inventory(folder, self.check, lambda msg: self.emit('log', msg))
                    self.emit('completion', dict(total=len(groups), rows=build_rows(missing, [], options.threshold),
                        status='本地扫描完成；填写作者后可继续采集', report_folder=None))
                else:
                    store = Store(self.state_dir / 'catalog.sqlite3')
                    try:
                        complete_resources(options, store, lambda: BrowserClient(self.state_dir, self.cancel,
                            self.emit, self.resume, legacy_state=LEGACY, **network), self.state_dir, self.emit, self.check)
                    finally:
                        store.close()
            except Cancelled:
                self.emit('log', '扫描已停止，已有报告保留。')
            except Exception as exc:
                self.emit('log', f'{type(exc).__name__}: {exc}')
            finally:
                self.emit('finished', None)
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        begin = time.monotonic()
        try:
            while time.monotonic() - begin < .03:
                kind, value = self.events.get_nowait()
                if kind in ('log', 'login'):
                    self.status.set(str(value))
                    if kind == 'login':
                        self.resume_button.configure(state='normal')
                elif kind == 'resume_done':
                    self.resume_button.configure(state='disabled')
                elif kind == 'progress':
                    self.status.set(f'读取首帖 {value[0]} / {value[1]} · {value[2]}')
                elif kind == 'completion':
                    self.publish(value)
                elif kind == 'finished':
                    self.busy = False; self.controls()
        except queue.Empty:
            pass
        self.window.after(poll_delay(self.window, self.busy), self.poll)

    def publish(self, state):
        self.last_state = state
        self.current_output = state.get('report_folder')
        self.rows = {str(i): row for i, row in enumerate(state['rows'])}
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        def draw(item):
            key, row = item
            self.tree.insert('', 'end', iid=key, values=(row['missing_label'], row['title'], row['status'], row['payment'], len(row['candidates'])))
        paint_items(self, self.window, self.rows.items(), draw)
        self.layout.stats(state['total'], sum(r['missing'] == 'script' for r in self.rows.values()),
                          sum(r['missing'] == 'video' for r in self.rows.values()))
        self.status.set(state['status'])
        self.detail.set('选择缺失组查看帖子、付费依据与资源链接。' + ('报告已保存，可点击“打开报告”。' if self.current_output else ''))

    def reexport(self):
        if self.busy:
            return
        if not self.last_state or not self.current_output:
            self.status.set('请先完成采集，生成报告后可按新选项重新导出。')
            return
        try:
            state = dict(self.last_state, download_export=self.export_options())
            self.save()
        except (ValueError, OSError) as exc:
            self.status.set(str(exc)); return
        folder = self.current_output
        self.busy = True; self.controls()
        def run():
            try:
                result = export_report(state, folder)
                self.emit('completion', result)
                self.emit('log', f"重新导出完成 · 下载链接 {result['download_text']['download']} 条 · 手动获取 {result['download_text']['manual']} 条")
            except Exception as exc:
                self.emit('log', f'导出失败：{exc}')
            finally:
                self.emit('finished', None)
        threading.Thread(target=run, daemon=True).start()

    def select(self, *_):
        ids = self.tree.selection()
        row = self.rows.get(ids[0]) if ids else None
        if not row:
            return
        from .resource_variants import version_summary
        lines = [row['missing_label'] + ' · ' + row['title'], row['status'],
                 version_summary(row.get('versions', [])), *[f['path'] for f in row.get('files', [])]]
        for c in row['candidates']:
            lines += [f"{c['score']} 分 · {'对应' if c['confirmed'] else '待核对'} · [{c['payment']['tag']}] {c['title']}",
                      c['url'], f"脚本：{c['payment']['script']}；视频：{c['payment']['video']}", c['reason'], *c['payment']['evidence']]
            if c['read_error']:
                lines.append(c['read_error'])
            lines += [RESOURCE_LABELS[r['kind']] + ' · ' + r['url'] for r in c['resources']]
        self.detail.set('\n'.join(lines))

    def open_report(self):
        if self.current_output and (path := Path(self.current_output) / '资源补齐报告.html').is_file():
            os.startfile(path)
        else:
            self.status.set('尚未生成报告，请先扫描并采集补齐。')

    def open_links(self):
        if self.current_output and Path(self.current_output).is_dir():
            folder = Path(self.current_output) / '下载用链接'
            os.startfile(folder if folder.is_dir() else Path(self.current_output))
        else:
            self.status.set('尚未生成报告，请先扫描并采集补齐。')

    def close(self):
        self.cancel.set()
        if not self.busy:
            self.save(); self.window.destroy()
