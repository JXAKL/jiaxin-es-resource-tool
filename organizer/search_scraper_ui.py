"""Search discovery in the shared workspace, with ranked ES authors."""
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, filedialog

from .collector_ui import CollectorPage
from .collector import atomic_text, display_date
from .forum import BrowserClient, Cancelled
from .panels import *
from .presentation import paint_items
from .runtime import STATE, LEGACY
from .search_scraper import ScrapeOptions, scrape, author_counts, remove_posts, export_report
from .store import Store


class SearchScraperPage(CollectorPage):
    def __init__(self, surface, author_app, state_dir=STATE):
        self.window, self.author_app, self.state_dir = surface, author_app, Path(state_dir)
        self.busy = False
        self.cancel, self.resume = threading.Event(), threading.Event()
        self.events = queue.Queue()
        self.tag, self.creator = tk.StringVar(), tk.StringVar()
        self.exclude_tags = tk.StringVar()
        self.include_paid = tk.BooleanVar(value=False)
        self.thumbnails = tk.BooleanVar(value=False)
        self.thumbnail_dir = tk.StringVar(value=str(self.state_dir.parent / 'cache' / '搜索刮削预览'))
        self.last_state=None;self._hover_job=None;self._hover_key=None
        self.output = tk.StringVar(value=str(self.state_dir.parent / 'reports' / '搜索刮削'))
        self.limit = tk.StringVar(value='0')
        self.refresh = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='填写标签或视频作者；两项同时填写时要求同时满足。')
        self.detail = tk.StringVar(value='左侧按 ES 作者的纳入帖子数排序；选择作者查看帖子及付费依据。')
        self.records, self.current_output, self.author_filter = {}, None, ''
        self._load(); self.build()
        surface.protocol('WM_DELETE_WINDOW', self.close)
        surface.after(100, self.poll)

    def _load(self):
        try:
            data = json.loads((self.state_dir / 'search-scraper-settings.json').read_text(encoding='utf-8'))
            for key in ('tag', 'creator', 'output', 'limit', 'exclude_tags', 'thumbnail_dir'):
                if isinstance(data.get(key), str):
                    getattr(self, key).set(data[key])
            if isinstance(data.get('include_paid'), bool):
                self.include_paid.set(data['include_paid'])
            if isinstance(data.get('thumbnails'), bool):self.thumbnails.set(data['thumbnails'])
        except (ValueError, OSError):
            pass

    def save(self):
        atomic_text(self.state_dir / 'search-scraper-settings.json', json.dumps(
            {key: getattr(self, key).get() for key in ('tag', 'creator', 'output', 'limit', 'exclude_tags', 'include_paid', 'thumbnails', 'thumbnail_dir')}, ensure_ascii=False))

    def build(self):
        self.layout = Workbench(self.window, '采集帖子', self.status, ('搜索发现', '纳入帖子', 'ES 作者数'))
        left = self.layout.settings
        section(left, '01', '搜索条件')
        caption(left, '指定标签 · 多个用 , 分隔，必须全部满足')
        self.inputs = [entry(left, self.tag)]
        self.inputs[-1].pack(fill='x')
        caption(left, '排除标签 · 多个用 , 分隔')
        self.exclude_entry = entry(left, self.exclude_tags)
        self.exclude_entry.pack(fill='x'); self.inputs.append(self.exclude_entry)
        self.paid_switch = switch(left, '包含付费帖子', self.include_paid)
        self.inputs.append(self.paid_switch)
        hint(left, '读取首帖后，命中任意排除标签即移出结果和计数。开启开关后不按付费状态排除，仍保留付费/未知标记。')
        caption(left, '视频原作者 · 不是 ES 发帖作者')
        self.inputs.append(entry(left, self.creator)); self.inputs[-1].pack(fill='x')
        hint(left, '例如 Kaotaro12，核对标题开头括号中的作者。与指定标签同时填写时取交集；关闭付费开关仅纳入明确免费帖子。')
        self.thumbnail_switch=switch(left,'下载缩略图 / 动态预览',self.thumbnails,self.preview_controls)
        self.inputs.append(self.thumbnail_switch)
        self.cache_controls=path_field(left,'缩略图缓存目录',self.thumbnail_dir,self.choose_cache)
        hint(left,'关闭后不下载；已有缓存仍可预览。抓取结束后移到帖子上静音播放。每帖最多 8 个预览，单个上限 32 MB。')
        path_controls = path_field(left, '报告保存目录', self.output, self.choose_output)
        self.inputs.extend(path_controls)
        caption(left, '每轮核对首帖数 · 0 不限')
        self.inputs.append(entry(left, self.limit)); self.inputs[-1].pack(fill='x')
        self.inputs.append(switch(left, '重新搜索并刷新首帖', self.refresh))
        hint(left, '停止或异常会保存断点；继续时复用已完成读取。报告附失败/排除原因、作者排名和按域名 TXT。')
        section(left, '02', '网站设置')
        app = self.author_app
        combo(left, app.browser_choice, ['Chromium', 'Chrome', 'Edge'], width=250).pack(fill='x')
        switch(left, '显示浏览器', app.visible)
        for title, var in [('请求间隔（秒）', app.request_interval), ('搜索间隔（秒）', app.search_interval),
                           ('每分钟请求上限 · 0 不限', app.per_minute), ('异常冷却（秒）', app.cooldown_seconds)]:
            caption(left, title); entry(left, var).pack(fill='x')
        hint(left, '沿用共用登录、请求限速和网页回退。搜索可见范围受网站及登录权限限制。')
        self.start_button = button(self.layout.bottom, '开始 / 继续搜索', self.start, accent=True)
        self.start_button.pack(fill='x')
        row = frame(self.layout.bottom); row.pack(fill='x', pady=8)
        self.login_button = button(row, '登录网站', lambda: self.start(True), width=116); self.login_button.pack(side='left')
        self.stop_button = button(row, '停止', self.cancel.set, width=116, state='disabled'); self.stop_button.pack(side='right')
        self.resume_button = button(self.layout.bottom, '已完成验证，继续', self.resume.set, state='disabled'); self.resume_button.pack(fill='x')
        button(self.layout.toolbar, '全部作者', self.clear_filter, width=90).pack(side='right', padx=4)
        button(self.layout.toolbar, '打开报告目录', self.open_output, width=118).pack(side='right')
        listings = SplitPane(self.layout.result, '搜索刮削/作者帖子预览')
        listings.pack(fill='both', expand=True)
        rank = tk.Frame(listings, bg=PANEL, width=185)
        rank.pack_propagate(False)
        listings.add_panel(rank, '作者', 185, 100)
        posts = tk.Frame(listings, bg=PANEL,width=1)
        posts.pack_propagate(False)
        listings.add_panel(posts, '帖子', None, 200)
        self.ranking = table(rank, [('author', 'ES 作者', 130), ('count', '帖数', 48)], empty_text='采集后显示\nES 作者排名')
        self.ranking.column('author', stretch=True)
        self.ranking.configure(height=5)
        self.ranking.bind('<<TreeviewSelect>>', self.select_author)
        self.tree = table(posts, [('author', 'ES 作者', 90), ('payment', '脚本/帖子', 100), ('title', '帖子标题', 260), ('date', '发布时间', 130)], empty_text='暂无帖子\n填写标签或视频作者后开始搜索。')
        self.tree.configure(selectmode='extended', height=5); self.tree.bind('<<TreeviewSelect>>', self.select)
        self.tree.bind('<Motion>',self.hover)
        self.tree.bind('<Leave>',self.cancel_hover)
        from .search_preview_ui import PreviewPane
        self.preview=PreviewPane(listings,self.window)
        text_box(self.layout.footer, self.detail, 64).pack(fill='x', pady=8)
        button(self.layout.footer, '选中加入下载', self.queue_downloads, width=122).pack(side='left')
        button(self.layout.footer, '全部加入下载', lambda: self.queue_downloads(True), width=122).pack(side='left', padx=6)
        button(self.layout.footer, '复制选中链接', self.copy_links, width=122).pack(side='right')
        self.delete_button=button(self.layout.footer,'删除选中帖',self.delete_selected,width=100)
        self.delete_button.pack(side='left',padx=4)
        self.log = ctk.CTkTextbox(self.layout.right, height=70, corner_radius=14, fg_color=PANEL, text_color=MUTED,
                                 font=(FONT, 11), state='disabled', wrap='word')
        self.log.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        self.preview_controls()

    def choose_cache(self):
        folder=filedialog.askdirectory(parent=self.window,title='选择缩略图缓存目录')
        if folder:self.thumbnail_dir.set(folder)

    def preview_controls(self):
        for control in self.cache_controls:
            control.configure(state='normal' if self.thumbnails.get() and not self.busy else 'disabled')
        self.preview.visible(self.thumbnails.get() or any(r.get('preview',{}).get('files') for r in self.records.values()))

    def cancel_hover(self, *_):
        if self._hover_job:
            self.window.after_cancel(self._hover_job);self._hover_job=None
        self._hover_key=None

    def hover(self, event):
        if self.busy:return
        key=self.tree.identify_row(event.y)
        if key==self._hover_key:return
        self.cancel_hover();self._hover_key=key
        if key in self.records:
            def show():
                self._hover_job=None
                if not self.busy and key==self._hover_key and key in self.records:self.display_row(self.records[key])
            self._hover_job=self.window.after(120,show)

    def delete_selected(self):
        keys=self.tree.selection()
        if self.busy or not keys or not self.last_state:return
        state=json.loads(json.dumps(self.last_state))
        output=self.current_output or state.get('report_folder')
        if not output:return
        self.busy=True;self.controls(True)
        def run():
            try:
                updated=remove_posts(state,keys,self.state_dir)
                self.emit('collection',updated)
                self.emit('exported',export_report(updated,output))
            except Exception as exc:self.emit('log',f'删除结果/更新报告失败：{exc}')
            finally:self.emit('finished',None)
        threading.Thread(target=run,daemon=True).start()

    def controls(self, busy):
        self.start_button.configure(state='disabled' if busy else 'normal')
        self.login_button.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if busy else 'disabled')
        for control in self.inputs:
            control.configure(state='disabled' if busy else 'normal')
        self.delete_button.configure(state='disabled' if busy else 'normal')
        self.preview_controls()
        if busy:
            self.cancel_hover();self.preview.clear()
            self.layout.progress.start(14)
        else:
            self.layout.progress.stop(); self.resume_button.configure(state='disabled')

    def start(self, login_only=False):
        if self.busy:
            return
        try:
            app = self.author_app; app.network_options()
            options = ScrapeOptions(Path(self.output.get()).resolve(), self.tag.get(), self.creator.get(), int(self.limit.get()),
                                    self.refresh.get(), self.exclude_tags.get(), self.include_paid.get(),
                                    self.thumbnails.get(),Path(self.thumbnail_dir.get()).resolve() if self.thumbnail_dir.get().strip() else None)
            if not login_only:
                options.filters()
                if not self.output.get().strip():
                    raise ValueError('请选择报告保存目录。')
            network = dict(headful=app.visible.get() or login_only, browser_channel=app._browser_channel,
                           interval=app._request_interval, search_interval=app._search_interval,
                           per_minute=app._per_minute, cooldown_seconds=app._cooldown_seconds)
            self.save(); app._save()
        except (ValueError, OSError) as exc:
            messagebox.showerror('设置无效', str(exc), parent=self.window); return
        self.busy = True; self.cancel.clear(); self.resume.clear(); self.controls(True)
        self.status.set('正在连接网站…')
        def run():
            try:
                with BrowserClient(self.state_dir, self.cancel, self.emit, self.resume, legacy_state=LEGACY, **network) as client:
                    if login_only:
                        client.login(); self.emit('log', '登录状态已保存。')
                    else:
                        store = Store(self.state_dir / 'catalog.sqlite3')
                        try:
                            scrape(options, store, client, self.state_dir, self.emit)
                        finally:
                            store.close()
            except Cancelled:
                self.emit('log', '已停止；本轮结果和断点已保留。')
            except Exception as exc:
                self.emit('log', f'{type(exc).__name__}: {exc}')
            finally:
                self.emit('finished', None)
        threading.Thread(target=run, daemon=True).start()

    def publish(self, value):
        self.last_state=value
        self.records = value['records']
        self.preview_controls()
        if self.preview.identity and str(self.preview.identity[0]) not in self.records:self.preview.clear()
        ranks = author_counts(self.records)
        identity = (value.get('tag'), value.get('creator'), value.get('exclude_tags'), value.get('include_paid'))
        if (identity != getattr(self, '_filter_identity', None)
                or self.author_filter not in {r['author'].casefold() for r in ranks}):
            self.author_filter = ''
        self._filter_identity = identity
        self.rank_rows = {str(i): row for i, row in enumerate(ranks)}
        current = self.ranking.get_children()
        if current:
            self.ranking.delete(*current)
        for key, row in self.rank_rows.items():
            self.ranking.insert('', 'end', iid=key, values=(row['author'], row['count']))
        self.layout.stats(value['discovered'], len(self.records), len(ranks))
        self.status.set(value['status']); self.draw_posts()

    def draw_posts(self):
        rows = sorted((r for r in self.records.values() if not self.author_filter or r['author'].casefold() == self.author_filter),
                      key=lambda r: (r['date'], r['id']), reverse=True)
        keys = {str(r['id']) for r in rows}
        obsolete = [k for k in self.tree.get_children() if k not in keys]
        if obsolete:
            self.tree.delete(*obsolete)
        def draw(row):
            payment = row['payment']
            labels = {payment['script'], payment['post']}
            fee = ('混合/需核实' if '混合/需核实' in labels or {'免费','付费'} <= labels
                   else '付费' if '付费' in labels else '免费' if '免费' in labels else '未知')
            key = str(row['id']); values = (row['author'], fee, row['title'], display_date(row['date']))
            if self.tree.exists(key): self.tree.item(key, values=values)
            else: self.tree.insert('', 'end', iid=key, values=values)
        paint_items(self, self.window, rows, draw, lambda: self.tree.set_children('', *(str(r['id']) for r in rows)))

    def select_author(self, *_):
        selected = self.ranking.selection()
        if selected and selected[0] in self.rank_rows:
            self.author_filter = self.rank_rows[selected[0]]['author'].casefold(); self.draw_posts()

    def clear_filter(self):
        self.author_filter = ''; self.ranking.selection_remove(self.ranking.selection()); self.draw_posts()

    def select(self, *_):
        selected = self.tree.selection()
        row = self.records.get(selected[0]) if selected else None
        self.display_row(row)

    def display_row(self, row):
        if row:
            if not self.busy:self.preview.show(row)
            self.detail.set(f'ES 作者：{row["author"]} · 视频作者：{", ".join(row["video_creators"])}\n{row["title"]}\n{row["url"]}\n'
                            f'帖子：{row["payment"]["post"]} · 脚本：{row["payment"]["script"]} · 视频：{row["payment"]["video"]} · 资源链接：{len(row["resources"])}\n'
                            + '；'.join(row['payment']['evidence']))
        else:self.detail.set('选择或移到帖子上，查看对应预览与信息。')
