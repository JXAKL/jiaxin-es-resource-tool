"""Link collection controls sharing author networking settings and catalog."""
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from .panels import *
from .collector import CollectionOptions, collect, sorted_records, display_date, title_filter_keyword
from .links import RULES, DOWNLOAD_TYPES, compile_custom
from .forum import BrowserClient, Cancelled, author_from_input
from .runtime import STATE, REPORTS, LEGACY
from .store import Store
from .presentation import poll_delay, paint_items


class CollectorPage:
    def __init__(self, surface, author_app, state_dir=STATE):
        self.window, self.author_app = surface, author_app
        self.state_dir = Path(state_dir)
        self.busy = False
        self.cancel, self.resume = threading.Event(), threading.Event()
        self.events = queue.Queue()
        self.author = tk.StringVar(value=author_app.seed.get().split(',')[0].strip())
        self.output = tk.StringVar(value=str(self.state_dir.parent / 'reports' / '链接采集'))
        self.order = tk.StringVar(value='新到旧')
        self.limit = tk.StringVar(value='0')
        self.refresh = tk.BooleanVar(value=False)
        self.title_filter_enabled = tk.BooleanVar(value=False)
        self.title_keyword = tk.StringVar()
        self.custom = tk.StringVar()
        self.type_vars = {key: tk.BooleanVar(value=key in DOWNLOAD_TYPES) for key, _ in RULES}
        self.status = tk.StringVar(value='输入作者主页或用户名，选择链接类型后开始采集。')
        self.detail = tk.StringVar(value='选择一条记录，查看首帖中提取的链接。')
        self.records, self.current_output = {}, None
        self._load()
        self.build()
        surface.protocol('WM_DELETE_WINDOW', self.close)
        surface.after(100, self.poll)

    def _load(self):
        try:
            data = json.loads((self.state_dir / 'collector-settings.json').read_text(encoding='utf-8'))
            for name in ('author', 'output', 'order', 'limit', 'custom', 'title_keyword'):
                if isinstance(data.get(name), str):
                    getattr(self, name).set(data[name])
            if isinstance(data.get('title_filter_enabled'), bool):
                self.title_filter_enabled.set(data['title_filter_enabled'])
            for key, var in self.type_vars.items():
                var.set(key in data.get('types', ['gofile']))
        except (ValueError, OSError):
            pass

    def save(self):
        from .collector import atomic_text
        data = {name: getattr(self, name).get() for name in ('author', 'output', 'order', 'limit', 'custom', 'title_keyword', 'title_filter_enabled')}
        data['types'] = [key for key, var in self.type_vars.items() if var.get()]
        atomic_text(self.state_dir / 'collector-settings.json', json.dumps(data, ensure_ascii=False, indent=2))

    def build(self):
        self.layout = Workbench(self.window, '采集预览', self.status, ('已索引帖子', '已读取首帖', '唯一链接'))
        left = self.layout.settings
        section(left, '01', '作者与输出')
        caption(left, '作者用户名或主页')
        entry(left, self.author).pack(fill='x')
        path_field(left, '保存目录', self.output, self.choose_output)
        caption(left, '按发布时间排序')
        combo(left, self.order, ['新到旧', '旧到新'], width=250).pack(fill='x')
        caption(left, '最多读取首帖数 · 0 为全部')
        entry(left, self.limit).pack(fill='x')
        self.title_filter_switch = switch(left, '仅采集标题包含关键词的帖子', self.title_filter_enabled, self.update_title_controls)
        self.title_keyword_entry = entry(left, self.title_keyword)
        self.title_keyword_entry.pack(fill='x', pady=(0, 4))
        hint(left, '下次开始采集时生效。按完整文字包含匹配，忽略大小写；先筛标题，再按数量上限读取。')
        self.update_title_controls()
        switch(left, '刷新已采集内容', self.refresh)
        section(left, '02', '提取链接')
        button(left, '选择下载支持的类型', self.select_download_types, width=250).pack(fill='x', pady=(0, 5))
        for key, title in RULES:
            switch(left, title, self.type_vars[key])
        caption(left, '自定义链接正则 · 可选')
        entry(left, self.custom).pack(fill='x')
        hint(left, '采集 ES 作者首帖中的视频和网盘链接，保留参数与密钥。结果可多选或全部加入下载队列，点击下载开始后获取资源。')
        section(left, '03', '共用网站设置')
        author = self.author_app
        combo(left, author.browser_choice, ['Chromium', 'Chrome', 'Edge'], width=250).pack(fill='x', pady=5)
        switch(left, '显示浏览器', author.visible)
        for title, var in [('请求间隔（秒）', author.request_interval), ('搜索间隔（秒）', author.search_interval),
                           ('每分钟请求上限 · 0 不限', author.per_minute), ('异常冷却（秒）', author.cooldown_seconds)]:
            caption(left, title)
            entry(left, var).pack(fill='x')
        hint(left, '与作者归类共用登录状态、索引和限速设置。网络超时自动重试。')
        self.start_button = button(self.layout.bottom, '开始 / 继续采集', self.start, accent=True)
        self.start_button.pack(fill='x')
        row = frame(self.layout.bottom)
        row.pack(fill='x', pady=8)
        self.login_button = button(row, '登录网站', lambda: self.start(login_only=True), width=116)
        self.login_button.pack(side='left')
        self.stop_button = button(row, '停止', self.cancel.set, width=116, state='disabled')
        self.stop_button.pack(side='right')
        self.resume_button = button(self.layout.bottom, '已完成验证，继续', self.resume.set, state='disabled')
        self.resume_button.pack(fill='x')
        button(self.layout.toolbar, '打开输出目录', self.open_output, width=126).pack(side='right')
        text_box(self.layout.footer, self.detail, 104).pack(fill='x', pady=8)
        button(self.layout.footer, '复制选中链接', self.copy_links, width=126).pack(side='right')
        button(self.layout.footer, '选中加入下载', self.queue_downloads, width=126).pack(side='left')
        button(self.layout.footer, '全部加入下载', lambda: self.queue_downloads(all_records=True), width=126).pack(side='left', padx=6)
        self.tree = table(self.layout.result, [('date', '发布时间 · UTC+8', 220), ('title', '帖子标题', 340), ('links', '链接数', 70), ('url', '帖子地址', 220)])
        self.tree.configure(selectmode='extended')
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.log = ctk.CTkTextbox(self.layout.right, height=88, corner_radius=14, fg_color=PANEL, text_color=MUTED,
                                 font=(FONT, 11), state='disabled', wrap='word')
        self.log.grid(row=2, column=0, sticky='ew', pady=(12, 0))

    def choose_output(self):
        path = filedialog.askdirectory(parent=self.window, title='保存采集结果')
        if path:
            self.output.set(path)

    def update_title_controls(self):
        self.title_filter_switch.configure(state='disabled' if self.busy else 'normal')
        self.title_keyword_entry.configure(state='normal' if not self.busy and self.title_filter_enabled.get() else 'disabled')

    def select_download_types(self):
        if self.busy:
            return
        for key, variable in self.type_vars.items():
            variable.set(key in DOWNLOAD_TYPES)

    def start(self, login_only=False):
        if self.busy:
            return
        try:
            app = self.author_app
            app.network_options()
            options = CollectionOptions(author_from_input(self.author.get()) if not login_only else '', Path(self.output.get()).resolve(),
                [key for key, var in self.type_vars.items() if var.get()], self.custom.get(),
                'desc' if self.order.get() == '新到旧' else 'asc', int(self.limit.get()), self.refresh.get(),
                self.title_filter_enabled.get(), self.title_keyword.get())
            if not login_only:
                title_filter_keyword(options.title_filter_enabled, options.title_keyword)
            compile_custom(options.custom)
            if not login_only and (not self.output.get().strip() or options.limit < 0 or not (options.types or options.custom.strip())):
                raise ValueError('请选择输出目录和链接类型；帖子数量不能小于 0')
            network = dict(headful=app.visible.get() or login_only, browser_channel=app._browser_channel,
                           interval=app._request_interval, search_interval=app._search_interval,
                           per_minute=app._per_minute, cooldown_seconds=app._cooldown_seconds)
            app._save()
            self.save()
        except (ValueError, OSError) as exc:
            messagebox.showerror('设置无效', str(exc), parent=self.window)
            return
        self.busy = True
        self.cancel.clear()
        self.resume.clear()
        self.controls(True)
        self.status.set('正在连接网站…')
        def run():
            try:
                with BrowserClient(self.state_dir, self.cancel, self.emit, self.resume, legacy_state=LEGACY, **network) as client:
                    if login_only:
                        client.login()
                        self.emit('log', '网站会话已保存。')
                    else:
                        store = Store(self.state_dir / 'catalog.sqlite3')
                        try:
                            collect(options, store, client, self.state_dir, self.emit)
                        finally:
                            store.close()
            except Cancelled:
                self.emit('log', '已停止，索引和已读取结果已保留。')
            except Exception as exc:
                self.emit('log', f'{type(exc).__name__}: {exc}')
            finally:
                self.emit('finished', None)
        threading.Thread(target=run, daemon=True).start()

    def controls(self, busy):
        self.update_title_controls()
        self.start_button.configure(state='disabled' if busy else 'normal')
        self.login_button.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        if busy:
            self.layout.progress.start(14)
        else:
            self.layout.progress.stop()
            self.resume_button.configure(state='disabled')

    def emit(self, kind, value):
        self.events.put((kind, value))

    def poll(self):
        started = time.monotonic()
        try:
            while time.monotonic()-started < .03:
                kind, value = self.events.get_nowait()
                if kind in ('log', 'login'):
                    from .display_settings import append_log
                    append_log(self.log, time.strftime('%H:%M:%S ') + str(value) + '\n')
                    self.status.set(str(value))
                    if kind == 'login':
                        self.resume_button.configure(state='normal')
                elif kind == 'resume_done':
                    self.resume_button.configure(state='disabled')
                elif kind == 'collection':
                    self.publish(value)
                elif kind == 'exported':
                    self.current_output = value['folder']
                    self.status.set(value['status'] + f" · {value['posts']} 帖 / {value['unique_links']} 链接 · 已导出 TXT、JSON 和 {value.get('domains', 0)} 个域名清单")
                elif kind == 'finished':
                    self.busy = False
                    self.controls(False)
        except queue.Empty:
            pass
        self.window.after(poll_delay(self.window, self.busy), self.poll)

    def publish(self, value):
        records = sorted_records(value['records'], 'desc' if self.order.get() == '新到旧' else 'asc')
        current = {str(r['id']): r for r in records}
        obsolete = [key for key in set(self.records) - set(current) if self.tree.exists(key)]
        if obsolete:
            self.tree.delete(*obsolete)
        previous = self.records
        def draw(item):
            key, row = item
            values = (display_date(row['date']), row['title'], len({url for urls in row['links'].values() for url in urls}), row['url'])
            if not self.tree.exists(key):
                self.tree.insert('', 'end', iid=key, values=values)
            elif previous.get(key) != row:
                self.tree.item(key, values=values)
        def finish():
            if tuple(previous) != tuple(current):
                self.tree.set_children('', *current)
        paint_items(self, self.window, current.items(), draw, finish)
        self.records = current
        unique = {url for row in records for links in row['links'].values() for url in links}
        self.layout.stats(value['discovered'], len(records), len(unique))
        self.status.set(value['status'])

    def select(self, *_):
        selected = self.tree.selection()
        row = self.records.get(selected[0]) if selected else None
        if row:
            links = list(dict.fromkeys(url for values in row['links'].values() for url in values))
            self.detail.set(row['title'] + '\n' + row['url'] + '\n\n' + ('\n'.join(links) or '首帖中没有所选类型的链接。'))

    def copy_links(self):
        selected = self.tree.selection()
        rows = [self.records[key] for key in selected if key in self.records]
        if rows:
            links = list(dict.fromkeys(url for row in rows for values in row['links'].values() for url in values))
            self.window.clipboard_clear()
            self.window.clipboard_append('\n'.join(links))

    def queue_downloads(self, all_records=False):
        if self.busy:
            self.status.set('请先停止采集或等待完成，再加入下载队列。')
            return
        selected = tuple(self.records) if all_records else self.tree.selection()
        rows = [self.records[key] for key in selected if key in self.records]
        if not rows:
            self.status.set('没有可加入的结果。请先采集，或选择一个/多个帖子。')
            return
        from .downloads.model import collection_items
        items, ignored = collection_items(rows)
        if not items:
            self.status.set(f'所选结果没有下载支持的资源链接；{ignored} 个其他链接仍保留在采集结果。')
            return
        workspace = getattr(self.window, 'workspace', None)
        if workspace:
            workspace.queue_resources(items, ignored=ignored)

    def open_output(self):
        path = Path(self.current_output or self.output.get())
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def close(self):
        self.cancel.set()
        if not self.busy:
            self.save()
            self.window.destroy()
