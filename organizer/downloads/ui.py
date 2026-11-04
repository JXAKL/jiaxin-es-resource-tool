"""Lazy single-window resource queue with a throttled, event-driven presentation."""
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from ..panels import Workbench, section, caption, hint, path_field, switch, frame, table, text_box
from ..ui import button, entry, combo, FIELD, PANEL, MUTED, TEXT, FONT
from ..runtime import STATE, DEFAULT_BROWSER
from .model import Item, parse_items, save_queue, load_queue, PLATFORMS, KEYS, collection_items, merge_sources, normalize_url, link_tokens
import customtkinter as ctk

QUALITY={'best':'最高画质 / 原始文件','2160':'不高于 2160p','1080':'不高于 1080p','720':'不高于 720p','480':'不高于 480p'}
MEGA_MODES={'local':'下载到本地','cloud':'保存到 MEGA 网盘'}


class DownloadPage:
    def __init__(self, surface, state_dir=STATE):
        self.window, self.state_dir = surface, Path(state_dir)
        self.busy = False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.service = None
        self.poll_job = None
        self.items, options = load_queue(self.state_dir / 'download-queue.json')
        self.by_url = {x.url: x for x in self.items}
        self.output = tk.StringVar(value=options.get('output', str(Path.home() / 'Downloads' / 'ES资源')))
        self.browser = tk.StringVar(value=options.get('browser', DEFAULT_BROWSER))
        self.visible = tk.BooleanVar(value=bool(options.get('visible', True)))
        self.iwara_legacy = tk.BooleanVar(value=bool(options.get('iwaraLegacy', True)))
        self.hanime_legacy = tk.BooleanVar(value=bool(options.get('hanimeLegacy', True)))
        self.interval = tk.StringVar(value=str(options.get('interval', 2)))
        self.request_interval = tk.StringVar(value=str(options.get('requestInterval', 8)))
        self.per_minute = tk.StringVar(value=str(options.get('perMinute', 6)))
        self.mega = tk.BooleanVar(value=False)
        self.mega_mode=tk.StringVar(value=MEGA_MODES.get(options.get('megaMode'),MEGA_MODES['local']))
        self.mega_connections=tk.StringVar(value=str(options.get('megaConnections',2)))
        self.quality=tk.StringVar(value=QUALITY.get(options.get('quality'),QUALITY['best']))
        self.rate_limit=tk.StringVar(value=str(options.get('rateLimitMiB',0)))
        self.proxy=tk.StringVar(value=str(options.get('proxy','')))
        self.sessions=options.get('sessions',{}) if isinstance(options.get('sessions'),dict) else {}
        self.session_note=tk.StringVar()
        self.provider = tk.StringVar(value='Iwara')
        self.provider.trace_add('write',self.refresh_session)
        self.refresh_session()
        self.status = tk.StringVar(value='粘贴链接或从采集结果加入队列。点击开始后才连接网站。')
        self.detail = tk.StringVar(value='选择任务查看状态与来源。MEGA 可下载到本地，也可单独选择保存到账号网盘。')
        self.last_save = 0
        self.build()
        self.publish_all()
        surface.protocol('WM_DELETE_WINDOW', self.close)

    def build(self):
        self.layout = Workbench(self.window, '资源队列', self.status, ('队列任务', '已完成 / 跳过', '失败 / 待核对'))
        left = self.layout.settings
        section(left, '01', '链接与目录')
        hint(left, 'Hanime1 · Iwara · Rule34Video\nGoFile · Pixeldrain · MEGA')
        caption(left, '下载链接 · 每行一条，支持批量粘贴')
        self.links = ctk.CTkTextbox(left, height=112, fg_color=FIELD, text_color=TEXT, corner_radius=8,
                                   font=(FONT, 11), wrap='word')
        self.links.pack(fill='x')
        row = frame(left); row.pack(fill='x', pady=6)
        button(row, '加入队列', self.add_input, width=116).pack(side='left')
        button(row, '导入链接/采集', self.import_txt, width=116).pack(side='right')
        path_field(left, '保存目录', self.output, self.choose_output)
        caption(left,'视频画质上限')
        self.quality_control=combo(left,self.quality,list(QUALITY.values()),width=250);self.quality_control.pack(fill='x')
        hint(left,'适用于 Hanime1、Iwara、Rule34Video 的画质菜单。无符合上限的画质时保留失败说明；网盘文件保留原样。')
        section(left, '02', '连接与登录')
        combo(left, self.browser, ['Chromium', 'Chrome', 'Edge'], width=250).pack(fill='x')
        switch(left, '显示浏览器', self.visible)
        switch(left, 'Iwara 使用旧版 Chrome 方式', self.iwara_legacy)
        switch(left, 'Hanime 使用兼容 Chrome 方式', self.hanime_legacy)
        caption(left,'自定义 HTTP 代理 · 留空沿用原连接')
        self.proxy_control=entry(left,self.proxy);self.proxy_control.pack(fill='x')
        hint(left,'例如 http://127.0.0.1:7890；对本模块网页、直链和 MEGA 本地下载统一生效。')
        caption(left, '任务间隔（秒）')
        entry(left, self.interval).pack(fill='x')
        caption(left, '页面 / 请求间隔（秒）')
        entry(left, self.request_interval).pack(fill='x')
        caption(left, '每分钟最多请求（0 表示不限）')
        entry(left, self.per_minute).pack(fill='x')
        caption(left, '单独登录 / 完成验证')
        combo(left, self.provider, list(PLATFORMS.values()), width=250).pack(fill='x')
        self.login_button = button(left, '打开所选网站登录', self.login)
        self.login_button.pack(fill='x', pady=6)
        text_box(left,self.session_note,72).pack(fill='x')
        hint(left, '各网站独立保存登录。临时断线自动重试；验证页面由你在浏览器中完成。')
        section(left,'03','本地下载设置')
        caption(left,'直链与 MEGA 限速 · MiB/s，0 不限')
        self.rate_control=entry(left,self.rate_limit);self.rate_control.pack(fill='x')
        hint(left,'限速不控制浏览器接管的官网保存；请求间隔与每分钟上限继续单独生效。')
        caption(left,'MEGA 处理方式')
        self.mega_control=combo(left,self.mega_mode,list(MEGA_MODES.values()),width=250);self.mega_control.pack(fill='x')
        caption(left,'MEGA 每个文件连接数 · 1–8')
        self.connections_control=entry(left,self.mega_connections);self.connections_control.pack(fill='x')
        switch(left, '允许保存到当前 MEGA 账号', self.mega)
        hint(left, '公开分享下载到本地不需要账号；保留子目录、断点，完成后核对完整文件。网盘保存模式仍需开启上方授权；未确认成功的提交不重复保存。')
        self.start_button = button(self.layout.bottom, '开始 / 继续', self.start, accent=True)
        self.start_button.pack(fill='x')
        row = frame(self.layout.bottom); row.pack(fill='x', pady=7)
        self.retry_button = button(row, '重试失败', lambda: self.start(failed_only=True), width=116)
        self.retry_button.pack(side='left')
        self.stop_button = button(row, '停止', self.stop, width=116, state='disabled')
        self.stop_button.pack(side='right')
        self.login_done = button(self.layout.bottom, '保存登录', self.finish_login, state='disabled')
        self.login_done.pack(fill='x')
        button(self.layout.toolbar, '打开目录', self.open_output, width=98).pack(side='right')
        button(self.layout.toolbar, '导出报告', self.export, width=98).pack(side='right', padx=6)
        self.selected_button=button(self.layout.toolbar,'仅运行选中',lambda:self.start(selected_only=True),width=104)
        self.selected_button.pack(side='right')
        text_box(self.layout.footer, self.detail, 74).pack(fill='x', pady=(0, 6))
        button(self.layout.footer, '全选', self.select_all, width=72).pack(side='left', padx=(0, 6))
        self.remove_button = button(self.layout.footer, '移除选中', self.remove_selected, width=104)
        self.remove_button.pack(side='left')
        self.clear_button = button(self.layout.footer, '清理已完成', self.clear_completed, width=114)
        self.clear_button.pack(side='left', padx=6)
        self.clear_all_button = button(self.layout.footer, '清空队列', self.clear_all, width=104)
        self.clear_all_button.pack(side='left')
        self.tree = table(self.layout.result, [('platform', '来源', 104), ('status', '状态', 108),
            ('title', '名称 / 链接', 310), ('detail', '进度与说明', 340)])
        self.tree.configure(selectmode='extended')
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.tree.bind('<Control-a>', self.select_all)
        self.tree.bind('<Control-A>', self.select_all)
        self.tree.bind('<Delete>', self.remove_selected)
        self.log = ctk.CTkTextbox(self.layout.right, height=78, fg_color=PANEL, text_color=MUTED,
                                 corner_radius=8, font=(FONT, 11), state='disabled', wrap='word')
        self.log.grid(row=2, column=0, sticky='ew', pady=(10, 0))

    def add_urls(self, urls):
        """Add links without visiting them; used by the author link collector."""
        text = urls if isinstance(urls, str) else '\n'.join(urls)
        parsed = parse_items(text)
        unsupported = sum(normalize_url(raw.rstrip('.,，。；;')) is None
                          for _, raw in link_tokens(text))
        return self.add_items(parsed, ignored=unsupported)

    def add_items(self, items, ignored=0):
        """Merge collected resources and provenance, preserving task outcomes."""
        count = 0
        duplicates = 0
        for incoming in items:
            url = normalize_url(incoming.url)
            if not url:
                ignored += 1
                continue
            item = self.by_url.get(url)
            if item is None:
                item = Item(url, incoming.title)
                merge_sources(item.sources, incoming.sources)
                self.items.append(item); self.by_url[url] = item
                count += 1
            else:
                duplicates += 1
                merge_sources(item.sources, incoming.sources)
                if not item.title:
                    item.title = incoming.title
        self.publish_all()
        self.stats()
        self.save()
        self.status.set(f'新增 {count} 个任务；已有 {duplicates} 个链接合并来源并保留任务状态。'
                        + (f'另有 {ignored} 个不支持的链接未加入。' if ignored else '')
                        + '点击开始后运行。')
        return count

    def add_input(self):
        self.add_urls(self.links.get('1.0', 'end'))
        self.links.delete('1.0', 'end')

    def import_txt(self):
        path = filedialog.askopenfilename(parent=self.window, title='导入链接或 ES 采集结果', filetypes=[('链接 / 采集结果', '*.txt *.md *.json'), ('所有文件', '*.*')])
        if not path: return
        try:
            file = Path(path)
            if file.stat().st_size > 32 * 1024 * 1024:
                raise ValueError('导入文件超过 32 MB，请拆分后导入。')
            text = file.read_text(encoding='utf-8-sig')
            if file.suffix.lower() == '.json':
                data = json.loads(text)
                if not isinstance(data, dict) or not isinstance(data.get('records'), (list, dict)):
                    raise ValueError('请选择链接采集导出的 *_Data.json 文件。')
                records = data['records']
                items, ignored = collection_items(records.values() if isinstance(records, dict) else records)
                self.add_items(items, ignored=ignored)
            else:
                self.add_urls(text)
        except (OSError, ValueError) as exc:
            messagebox.showerror('导入失败', str(exc), parent=self.window)

    def choose_output(self):
        folder = filedialog.askdirectory(parent=self.window, title='保存资源')
        if folder: self.output.set(folder)

    def options(self):
        interval = float(self.interval.get())
        if not 0 <= interval <= 86400: raise ValueError('任务间隔需要介于 0 和 86400 秒。')
        if not self.output.get().strip(): raise ValueError('请选择保存目录。')
        from ..pacing import nonnegative_seconds, request_limit
        request_interval = nonnegative_seconds(self.request_interval.get(), '下载请求间隔')
        per_minute = request_limit(self.per_minute.get())
        rate=float(self.rate_limit.get())
        if not math.isfinite(rate) or rate<0 or rate>1024 or 0<rate<.001:raise ValueError('限速请输入 0 或 0.001–1024 MiB/s。')
        connections=int(self.mega_connections.get())
        if not 1<=connections<=8:raise ValueError('MEGA 每个文件连接数需要介于 1 和 8。')
        quality=next(k for k,v in QUALITY.items() if v==self.quality.get())
        mega_mode=next(k for k,v in MEGA_MODES.items() if v==self.mega_mode.get())
        proxy=self.proxy.get().strip()
        if proxy:
            u=urlsplit(proxy)
            if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:
                raise ValueError('代理请输入不含账号密码的 http://地址:端口 或 https://地址:端口。')
            u.port  # Validate malformed and out-of-range ports before launching.
        return dict(output=str(Path(self.output.get()).resolve()), state=str(self.state_dir), browser=self.browser.get(),
                    visible=self.visible.get(), interval=interval, requestInterval=request_interval,
                    perMinute=per_minute, megaSave=self.mega.get(), iwaraLegacy=self.iwara_legacy.get(), hanimeLegacy=self.hanime_legacy.get(),
                    quality=quality,megaMode=mega_mode,megaConnections=connections,rateLimit=int(rate*1048576),proxy=proxy)

    def start(self, failed_only=False, selected_only=False):
        if self.busy: return
        if self.links.get('1.0', 'end').strip(): self.add_input()
        selection=set(self.tree.selection())
        local_mega=self.mega_mode.get()==MEGA_MODES['local']
        items=[x for x in self.items if (not selected_only or x.key in selection) and (not failed_only or x.status=='失败')]
        items=[x for x in items if (local_mega and x.platform=='MEGA') or x.status not in ('网盘已保存','重复已跳过','需核对')]
        if not selected_only:items=[x for x in items if x.status not in ('完成','已校验跳过')]
        if not local_mega and not self.mega.get(): items = [x for x in items if x.platform != 'MEGA']
        if not items:
            self.status.set('没有可运行任务。可选中条目仅运行选中项；MEGA 网盘保存需要授权，未确认的网盘提交不会重复执行。'); return
        self.run(items)

    def login(self):
        if self.busy: return
        provider = self.provider.get()
        item = next((x for x in self.items if x.platform == provider), None)
        if item is None:
            host = next(host for host, name in PLATFORMS.items() if name == provider)
            item = Item('https://' + host + '/')
        self.run([item], login=True)

    def run(self, items, login=False):
        try:
            options = self.options()
            if login: options['visible'] = True
            self.save()
        except (ValueError, OSError) as exc:
            messagebox.showerror('设置无效', str(exc), parent=self.window); return
        self.busy = True; self.cancel.clear(); self.controls(True)
        from .service import DownloadService
        self.service = DownloadService(self.events.put)
        def work():
            try:
                self.service.run(items, options, login)
            except Exception as exc:
                self.events.put({'type': 'error', 'text': str(exc)})
            finally:
                self.events.put({'type': 'finished'})
        threading.Thread(target=work, daemon=True).start()
        self.status.set('正在启动登录窗口…' if login else f'准备处理 {len(items)} 个任务…')
        self.poll_job = self.window.after(80, self.poll)

    def stop(self):
        self.cancel.set()
        if self.service: self.service.stop()
        self.status.set('正在停止；已下载断点会保留。')

    def finish_login(self):
        if self.service: self.service.send('FINISH_LOGIN')
        self.login_done.configure(state='disabled')

    def controls(self, busy):
        for widget in (self.start_button, self.retry_button, self.login_button, self.remove_button, self.clear_button, self.clear_all_button,
                       self.selected_button,self.quality_control,self.rate_control,self.mega_control,self.connections_control,self.proxy_control):
            widget.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        self.layout.progress.configure(mode='indeterminate')
        if busy: self.layout.progress.start(30)
        else:
            self.layout.progress.stop(); self.layout.progress.configure(value=0)
            self.login_done.configure(state='disabled')

    def poll(self):
        self.poll_job = None
        changed = False
        dirty = {}
        progress = None
        started = time.monotonic()
        while time.monotonic() - started < .012:
            try: event = self.events.get_nowait()
            except queue.Empty: break
            kind, text = event['type'], event.get('text', '')
            if kind in ('log', 'retry', 'error', 'login'):
                self.append_log(text); self.status.set(text)
                if kind == 'login': self.login_done.configure(state='normal', text='已完成，继续' if event.get('action') == 'resume' else '保存登录')
            elif kind=='session':
                key=event.get('platform')
                if key in KEYS.values():
                    self.sessions[key]={'checkedAt':str(event.get('checkedAt','')),'cookies':int(event.get('cookies',0))}
                    self.refresh_session();changed=True
            elif kind in ('task', 'progress'):
                item = self.by_url.get(event.get('url'))
                if item:
                    if kind == 'task':
                        progress = None
                        item.status, item.detail = event['status'], event.get('detail', '')
                        changed = True
                    else:
                        done, total = event.get('downloaded', 0), event.get('total', 0)
                        item.status = '下载中'
                        item.detail = f"{event.get('filename', '')} · {done / 1048576:.1f} / {total / 1048576:.1f} MB · {event.get('speed', 0) / 1024:.0f} KB/s"
                        progress = (max(1, total), done, item.detail)
                    dirty[item.key] = item
            elif kind == 'summary':
                progress = None
                self.status.set((f"队列已暂停：{event.get('reason', '')}" if event.get('paused') else
                                f"{'已停止' if event.get('stopped') else '本轮完成'} · 成功 {event['completed']} · 失败 {event['failed']}"))
            elif kind == 'finished':
                progress = None
                self.busy = False; self.controls(False)
                for item in self.items:
                    if item.status in ('处理中', '下载中', '等待网站验证', '等待官网下载'):
                        item.status = '待继续'; dirty[item.key] = item
                changed = True
                self.save()
        # Network progress can arrive in bursts. Apply only the last row/progress
        # state in this slice, preserving event order and terminal task statuses.
        for item in dirty.values():
            self.update_item(item)
        if progress:
            total, done, detail = progress
            self.layout.progress.stop()
            self.layout.progress.configure(mode='determinate', maximum=total, value=done)
            self.status.set(detail)
        if changed:
            self.stats()
            if time.monotonic() - self.last_save > 1:
                self.save(); self.last_save = time.monotonic()
        if self.busy or not self.events.empty():
            self.poll_job = self.window.after(100, self.poll)

    def append_log(self, text):
        from ..display_settings import append_log
        append_log(self.log, time.strftime('%H:%M:%S ') + str(text) + '\n')

    def update_item(self, item):
        values = (item.platform, item.status, item.title or item.url, item.detail)
        cache = getattr(self, '_row_values', None)
        if cache is None:
            self._row_values = cache = {}
        if cache.get(item.key) == values and self.tree.exists(item.key):
            return
        if self.tree.exists(item.key): self.tree.item(item.key, values=values)
        else: self.tree.insert('', 'end', iid=item.key, values=values)
        cache[item.key] = values

    def publish_all(self):
        from ..presentation import paint_items
        paint_items(self, self.window, self.items, self.update_item)
        self.stats()

    def stats(self):
        self.layout.stats(len(self.items), sum(x.status in ('完成', '已校验跳过', '网盘已保存', '重复已跳过') for x in self.items), sum(x.status in ('失败', '需核对') for x in self.items))

    def select(self, *_):
        selected = self.tree.selection()
        if len(selected) > 1:
            self.detail.set(f'已选中 {len(selected)} 项，可仅运行选中或批量移除。Ctrl / Shift 多选，Ctrl+A 全选，Delete 移除；不会删除已下载文件。')
            return
        item = next((x for x in self.items if x.key in selected), None)
        if item:
            sources = '\n'.join(f"来源：{row.get('author', '')} · {row.get('title', '')}\n{row.get('url', '')}" for row in item.sources)
            self.detail.set(item.url + '\n' + item.status + ' · ' + item.detail + ('\n' + sources if sources else ''))

    def select_all(self, *_):
        # Include rows not yet drawn by the incremental initial render.
        self._paint_generation = getattr(self, '_paint_generation', 0) + 1
        for item in self.items:
            if not self.tree.exists(item.key): self.update_item(item)
        self.tree.selection_set(self.tree.get_children())
        self.select()
        return 'break'

    def remove_selected(self, *_):
        self._remove_keys(set(self.tree.selection()))
        return 'break'

    def _remove_keys(self, selected):
        if self.busy:
            self.status.set('请先停止当前下载，待停止完成后再清理队列。')
            return False
        remaining = [x for x in self.items if x.key not in selected]
        count = len(self.items) - len(remaining)
        # Persist first: a failed save must not make tasks appear deleted.
        try:
            save_queue(self.state_dir / 'download-queue.json', remaining, self.saved_options())
        except OSError as exc:
            messagebox.showerror('清理失败', str(exc), parent=self.window)
            return False
        self._paint_generation = getattr(self, '_paint_generation', 0) + 1
        self.items = remaining
        self.by_url = {x.url: x for x in remaining}
        self._row_values = {key: value for key, value in getattr(self, '_row_values', {}).items() if key not in selected}
        removed = [key for key in self.tree.get_children() if key in selected]
        if removed: self.tree.delete(*removed)
        self.publish_all()
        self.detail.set('Ctrl / Shift 多选，Ctrl+A 全选，Delete 移除；清空队列可移除所有状态的任务。')
        self.status.set(f'已移除 {count} 项，剩余 {len(remaining)} 项；已下载文件、断点和登录状态保留。')
        return True

    def clear_all(self):
        if self._remove_keys({x.key for x in self.items}):
            self.links.delete('1.0', 'end')

    def clear_completed(self):
        self._remove_keys({x.key for x in self.items if x.status in ('完成', '已校验跳过', '网盘已保存', '重复已跳过')})

    def saved_options(self):
        options = {key: getattr(self, key).get() for key in ('output', 'browser', 'visible', 'interval')}
        options.update(requestInterval=self.request_interval.get(), perMinute=self.per_minute.get(), iwaraLegacy=self.iwara_legacy.get(), hanimeLegacy=self.hanime_legacy.get())
        options.update(quality=next((k for k,v in QUALITY.items() if v==self.quality.get()),'best'),
                       megaMode=next((k for k,v in MEGA_MODES.items() if v==self.mega_mode.get()),'local'),
                       megaConnections=self.mega_connections.get(),rateLimitMiB=self.rate_limit.get(),proxy=self.proxy.get(),sessions=self.sessions)
        return options

    def refresh_session(self,*_):
        provider=self.provider.get();saved=self.sessions.get(KEYS.get(provider),{})
        if not isinstance(saved,dict):saved={}
        when=str(saved.get('checkedAt','')).replace('T',' ').replace('Z',' UTC')
        if when:
            self.session_note.set(('已保存网站会话' if saved.get('cookies') else '本次未读到站点 Cookie')+'\n'+when+'\n是否仍登录由官网确认，不自动访问检测。')
        else:self.session_note.set('尚未记录登录确认。已有浏览器会话继续复用；公开资源不一定需要登录。')

    def save(self):
        save_queue(self.state_dir / 'download-queue.json', self.items, self.saved_options())

    def export(self):
        path = filedialog.asksaveasfilename(parent=self.window, title='导出下载报告', defaultextension='.json',
                                          initialfile='资源下载报告.json', filetypes=[('JSON 报告', '*.json')])
        if path:
            try: save_queue(path, self.items, {'output': self.output.get()})
            except OSError as exc: messagebox.showerror('导出失败', str(exc), parent=self.window)

    def open_output(self):
        try:
            path = Path(self.output.get()); path.mkdir(parents=True, exist_ok=True); os.startfile(path)
        except OSError as exc: messagebox.showerror('无法打开目录', str(exc), parent=self.window)

    def close(self):
        if self.busy: self.stop()
        else:
            self.save()
            if self.poll_job: self.window.after_cancel(self.poll_job)
            self.window.destroy()
