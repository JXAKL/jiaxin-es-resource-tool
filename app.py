from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from organizer.engine import analyse, export_matches
from organizer.matching import MatchingSession, configured_workers, worker_limit
from organizer.forum import BrowserClient, Cancelled, Forum, author_from_input
from organizer.model import scan
from organizer.moves import execute, undo
from organizer.store import Store
from organizer.diagnostics import append_log, NetworkDiagnostics, ZONE
from organizer.pacing import nonnegative_seconds, request_limit
from organizer.runtime import ROOT, STATE, REPORTS, LEGACY, DEFAULT_BROWSER

from organizer.ui import DesktopUI, APP_NAME, decorate_window, button, PANEL, TEXT, MUTED
from organizer.presentation import poll_delay
from organizer.ui_events import UIEventQueue, LogText
from organizer.review import can_choose_highest, choose_highest_authors, matches_in_view

class App(DesktopUI):
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry('1380x900')
        self.root.minsize(1120, 740)
        self.events = UIEventQueue()
        self.stop_event, self.resume_event = threading.Event(), threading.Event()
        self.worker = None
        self.matches = []
        self._row_cache = {}
        self._render_generation = 0
        self.analysis_target = ''
        self.analysis_threshold = .85
        self.analysis_sync_search = True
        self.latest_report = None
        self.close_pending = False
        self.filter_var = tk.StringVar(value='全部')
        self.target = tk.StringVar()
        self.seed = tk.StringVar()
        self.recursive = tk.BooleanVar(value=True)
        self.sync_search = tk.BooleanVar(value=True)
        self.offline = tk.BooleanVar(value=False)
        self.visible = tk.BooleanVar(value=True)
        self.browser_choice = tk.StringVar(value=DEFAULT_BROWSER)
        self.request_interval = tk.StringVar(value='8')
        self.search_interval = tk.StringVar(value='15')
        self.per_minute = tk.StringVar(value='6')
        self.cooldown_seconds = tk.StringVar(value='120')
        self._browser_channel, self._request_interval = DEFAULT_BROWSER.lower(), 8.
        self._search_interval, self._per_minute, self._cooldown_seconds = 15., 6, 120.
        self.refresh = tk.BooleanVar(value=False)
        self.limit = tk.StringVar(value='0')
        self.threshold = tk.StringVar(value='85')
        self.compute_workers = tk.StringVar(value='0')
        self.status = tk.StringVar(value='添加素材目录，设置目标目录，然后开始分析。')
        self.summary = tk.StringVar(value='0 组文件 · 0 可归类 · 0 待核对')
        self._style()
        self._build()
        self._load()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(poll_delay(self.root, self.busy()), self.poll)

    def _load(self):
        try:
            data = json.loads((STATE / 'settings.json').read_text(encoding='utf-8'))
            for path in data.get('sources', []):
                self.sources.insert('end', path)
            self.target.set(data.get('target', ''))
            self.seed.set(data.get('seed', ''))
            self.threshold.set(str(data.get('threshold', 85)))
            self.browser_choice.set(data.get('browser', DEFAULT_BROWSER))
            self.request_interval.set(str(data.get('request_interval', 8)))
            self.search_interval.set(str(data.get('search_interval', 15)))
            self.per_minute.set(str(data.get('per_minute', 6)))
            self.cooldown_seconds.set(str(data.get('cooldown_seconds', 120)))
            self.compute_workers.set(str(data.get('compute_workers', 0)))
            self.sync_search.set(bool(data.get('sync_search', True)))
        except (OSError, ValueError):
            pass

    def _save(self):
        STATE.mkdir(exist_ok=True)
        (STATE / 'settings.json').write_text(json.dumps(dict(sources=list(self.sources.get(0, 'end')),
            target=self.target.get(), seed=self.seed.get(), threshold=self.threshold.get(),
            browser=self.browser_choice.get(), request_interval=self.request_interval.get(),
            search_interval=self.search_interval.get(), per_minute=self.per_minute.get(),
            cooldown_seconds=self.cooldown_seconds.get(),
            sync_search=self.sync_search.get(),
            compute_workers=self.compute_workers.get()), ensure_ascii=False, indent=2), encoding='utf-8')

    def add_source(self):
        folder = filedialog.askdirectory(title='选择视频或脚本目录')
        if folder and folder not in self.sources.get(0, 'end'):
            self.sources.insert('end', folder)
            if not self.target.get():
                self.target.set(str(Path(folder) / '按作者归类'))

    def remove_source(self):
        for item in reversed(self.sources.curselection()):
            self.sources.delete(item)

    def choose_target(self):
        folder = filedialog.askdirectory(title='选择归类目标目录')
        if folder:
            self.target.set(folder)

    def emit(self, kind, value):
        if kind in {'log', 'login', 'error'}:
            value = LogText(value)
        self.events.put((kind, value))

    def check(self):
        if self.stop_event.is_set():
            raise Cancelled('已停止。已采集索引和已完成操作均已记录。')

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def run(self, work):
        if self.busy():
            return
        self.stop_event.clear()
        self.resume_event.clear()
        self.set_busy(True)
        def wrapped():
            try:
                work()
            except Cancelled as exc:
                self.emit('log', str(exc))
            except Exception as exc:
                self.emit('error', f'{type(exc).__name__}: {exc}')
            finally:
                self.emit('finished', None)
        self.worker = threading.Thread(target=wrapped, daemon=True)
        self.worker.start()

    def set_busy(self, busy):
        for button in [self.start_btn, self.login_btn, self.undo_btn, self.import_btn] + self.edit_buttons:
            button.configure(state='disabled' if busy else 'normal')
        self.target_entry.configure(state='disabled' if busy else 'normal')
        self.threshold_entry.configure(state='disabled' if busy else 'normal')
        self.browser_entry.configure(state='disabled' if busy else 'readonly')
        self.interval_entry.configure(state='disabled' if busy else 'normal')
        self.search_interval_entry.configure(state='disabled' if busy else 'normal')
        self.per_minute_entry.configure(state='disabled' if busy else 'normal')
        self.cooldown_entry.configure(state='disabled' if busy else 'normal')
        self.workers_entry.configure(state='disabled' if busy else 'normal')
        self.sync_search_switch.configure(state='disabled' if busy else 'normal')
        self.best_author_btn.configure(state='normal' if not busy and any(
            can_choose_highest(m) for _, m in matches_in_view(self.matches, self.filter_var.get())) else 'disabled')
        self.stop_btn.configure(state='normal' if busy else 'disabled')
        self.move_btn.configure(state='normal' if not busy and self.sync_search.get() == self.analysis_sync_search
                                and any(m.selected and m.author for m in self.matches) else 'disabled')
        if not busy:
            self.resume_btn.configure(state='disabled')

    def client(self, visible):
        return BrowserClient(STATE, self.stop_event, self.emit, self.resume_event,
                             headful=visible, legacy_state=LEGACY,
                             browser_channel=self._browser_channel, interval=self._request_interval,
                             search_interval=self._search_interval, per_minute=self._per_minute,
                             cooldown_seconds=self._cooldown_seconds)

    def grouping_changed(self):
        if self.matches and self.sync_search.get() != self.analysis_sync_search:
            self.status.set('同步检索设置已改变，请重新分析以更新分组。')
        self.set_busy(self.busy())

    def network_options(self):
        choices = {'Chrome': 'chrome', 'Edge': 'msedge', 'Chromium': 'chromium'}
        if self.browser_choice.get() not in choices:
            raise ValueError('请选择 Chrome、Edge 或 Chromium。')
        interval = nonnegative_seconds(self.request_interval.get(), '请求间隔')
        search = nonnegative_seconds(self.search_interval.get(), '搜索间隔')
        per_minute = request_limit(self.per_minute.get())
        cooldown = nonnegative_seconds(self.cooldown_seconds.get(), '异常冷却')
        self._browser_channel, self._request_interval = choices[self.browser_choice.get()], interval
        self._search_interval, self._per_minute, self._cooldown_seconds = search, per_minute, cooldown

    def login(self):
        try:
            self.network_options()
        except ValueError as exc:
            messagebox.showerror('检查设置', str(exc))
            return
        self._save()
        def work():
            with self.client(True) as client:
                client.login()
                session = client.get('/session/current.json')
                if not (session.get('current_user') or {}).get('username'):
                    raise ValueError('当前浏览器尚未登录，未标记为登录成功。')
                self.emit('log', '网站会话验证成功，登录状态已保存在本机应用数据目录。')
        self.run(work)

    def start(self):
        roots = list(self.sources.get(0, 'end'))
        target = self.target.get().strip()
        try:
            self.network_options()
            if not roots or not target:
                raise ValueError('请先选择素材目录和目标目录。')
            if not Path(target).is_absolute():
                raise ValueError('目标目录请使用完整路径。')
            limit = int(self.limit.get())
            if limit < 0:
                raise ValueError('搜索上限不能小于 0。')
            threshold = float(self.threshold.get()) / 100
            if not .70 <= threshold <= 1.:
                raise ValueError('作者确认阈值须为 70–100 分。')
            workers = configured_workers(self.compute_workers.get())
            seeds = [author_from_input(s) for s in self.seed.get().replace('，', ',').split(',') if s.strip()]
        except ValueError as exc:
            messagebox.showerror('检查设置', str(exc))
            return
        self._save()
        offline, visible, recursive, refresh = self.offline.get(), self.visible.get(), self.recursive.get(), self.refresh.get()
        self.analysis_target = target
        self.analysis_threshold = threshold
        sync_search = self.sync_search.get()
        self.analysis_sync_search = sync_search
        self.matches = []
        self.render()
        report = REPORTS / time.strftime('%Y%m%d-%H%M%S')
        def work():
            results = []
            store = Store(STATE / 'catalog.sqlite3')
            matcher = MatchingSession(workers=workers, cache_path=STATE / 'local-scores.sqlite3')
            def output(kind, value):
                nonlocal results
                if kind == 'matches':
                    results = value
                self.emit(kind, value)
            try:
                self.emit('log', '扫描本地视频和脚本……')
                groups = scan(roots, target, recursive, self.check, lambda s: self.emit('log', s), sync_search=sync_search)
                self.emit('log', f'找到 {len(groups)} 组、{sum(len(g.files) for g in groups)} 个文件。')
                if not groups:
                    return
                # Publish the scan and cached matches before any browser startup.
                results = analyse(groups, store, emit=output, stop=self.check, threshold=threshold, matcher=matcher)
                if not offline:
                    self.emit('log', '本地匹配结果已保留；正在连接网站继续搜索。')
                    with self.client(visible) as client:
                        results = analyse(groups, store, client, output, seeds, refresh, limit, self.check, threshold=threshold, matcher=matcher)
            finally:
                matcher.dispose()
                store.export(report)
                export_matches(results, report)
                store.close()
                self.emit('report', str(report))
        self.run(work)

    def render(self):
        self._render_generation += 1
        generation = self._render_generation
        ready = sum(m.status in {'可归类', '人工指定'} for m in self.matches)
        review = sum('核对' in m.status for m in self.matches)
        failed = sum(m.status == '搜索失败' for m in self.matches)
        multi = sum(m.status == '多作者待核对' for m in self.matches)
        visible = list(matches_in_view(self.matches, self.filter_var.get()))
        self.update_summary_cards(ready, review)
        self.summary.set(f'当前显示 {len(visible)}/{len(self.matches)} 组 · 全部：{ready} 可归类 · {review} 待核对（多作者 {multi}） · {failed} 搜索失败 · {sum(m.selected for m in self.matches)} 已勾选')
        rows = []
        for i, match in visible:
            best = next((c for c in match.candidates if c.author == match.author), None)
            best = best or (match.candidates[0] if match.candidates else None)
            values = ('☑' if match.selected else '☐', match.status, match.group.display_name, match.group.kind_label, len(match.group.files), match.author,
                      f'{best.score * 100:.1f}' if best else '—', match.error or match.selection_note or ('人工指定' if match.manual else best.reason if best else '暂无可用证据'))
            rows.append((str(i), values, ('ready' if match.author else 'review',)))
        if rows:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.configure(text='当前筛选下没有结果。' if self.matches else '还没有匹配结果\n\n添加素材目录，开始分析后在这里核对作者。')
            self.empty_hint.place(relx=.5, rely=.5, anchor='center')
        desired = {identity for identity, _, _ in rows}
        obsolete = self._row_cache.keys() - desired
        if obsolete:
            self.tree.delete(*obsolete)
            for identity in obsolete:
                del self._row_cache[identity]
        changed = [(identity, values, tags) for identity, values, tags in rows
                   if self._row_cache.get(identity) != (values, tags)]
        # Keep existing rows/selection/scroll position. Large first paints yield
        # between chunks so the Stop button and window messages remain usable.
        def draw(position=0):
            if generation != self._render_generation:
                return
            started = time.monotonic()
            while position < len(changed):
                identity, values, tags = changed[position]
                if identity in self._row_cache:
                    self.tree.item(identity, values=values, tags=tags)
                else:
                    self.tree.insert('', 'end', iid=identity, values=values, tags=tags)
                self._row_cache[identity] = values, tags
                position += 1
                if time.monotonic() - started >= .012:
                    self.root.after(10, draw, position)
                    return
            order = [identity for identity, _, _ in rows]
            if list(self.tree.get_children()) != order:
                for index, identity in enumerate(order):
                    self.tree.move(identity, '', index)
        draw()
        if not self.busy():
            self.set_busy(False)

    def toggle(self, event):
        if self.busy() or self.tree.identify_column(event.x) != '#1':
            return
        row = self.tree.identify_row(event.y)
        if row:
            match = self.matches[int(row)]
            if match.author:
                match.selected = not match.selected
                self.render()

    def uncheck(self):
        if not self.busy():
            for match in self.matches:
                match.selected = False
            self.render()

    def choose_best_authors(self):
        if self.busy():
            return
        scope = [m for _, m in matches_in_view(self.matches, self.filter_var.get())]
        pending = sum(can_choose_highest(m) for m in scope)
        counts = choose_highest_authors(scope, self.analysis_threshold)
        self.render()
        remaining = sum('核对' in m.status for m in scope)
        self.write_log(f"一键取最高分（当前筛选）：检查 {pending} 组未确认多作者项，新增勾选 {counts['selected']} 组；并列 {counts['tied']} 组、低于分析阈值 {counts['below_threshold']} 组、证据不足或冲突 {counts['insufficient']} 组保留核对。保留已有选择/归属 {counts['preserved']} 组；当前筛选剩余待核对 {remaining} 组（含普通待核对）。尚未移动文件。")

    def details(self, event=None):
        if self.busy() or not self.tree.selection():
            return
        match = self.matches[int(self.tree.selection()[0])]
        from organizer.local_tools.surfaces import create_surface
        dialog = create_surface(self.root)
        dialog.title('核对归属作者 · ' + APP_NAME)
        if not getattr(dialog, 'embedded', False):
            decorate_window(dialog)
        dialog.geometry('1040x720')
        dialog.transient(self.root)
        dialog.grab_set()
        import customtkinter as ctk
        from organizer.panels import table
        from organizer.ui import label as heading_label, FIELD, BORDER, FONT
        frame = ctk.CTkFrame(dialog, fg_color=PANEL, corner_radius=18)
        frame.pack(fill='both', expand=True, padx=18, pady=16)
        heading_label(frame, match.group.display_name, size=17, bold=True, wraplength=980).pack(anchor='w', padx=20, pady=(18, 6))
        heading_label(frame, '以下是主题首帖作者；转载或多人协作时，请结合帖子内容确认。', size=12, color=MUTED).pack(anchor='w', padx=20, pady=(0, 12))
        row = ctk.CTkFrame(frame, fg_color='transparent')
        row.pack(side='bottom', fill='x', padx=18, pady=(8, 18))
        notebook = ctk.CTkTabview(frame, height=250, fg_color=FIELD, corner_radius=12,
            segmented_button_fg_color=FIELD, segmented_button_selected_color='#42614f',
            segmented_button_selected_hover_color='#52765f', segmented_button_unselected_color=FIELD,
            segmented_button_unselected_hover_color=BORDER)
        notebook.pack(side='bottom', fill='x', padx=18, pady=8)
        evidence = ctk.CTkTextbox(notebook.add('匹配证据'), fg_color=FIELD, text_color=TEXT, wrap='word', font=(FONT, 12))
        files = ctk.CTkTextbox(notebook.add('本地文件'), fg_color=FIELD, text_color=TEXT, wrap='word', font=(FONT, 12))
        evidence.pack(fill='both', expand=True)
        files.pack(fill='both', expand=True)
        tree = table(frame, [('author', '作者', 130), ('score', '综合分', 65), ('title', '帖子标题', 350), ('reason', '依据', 370)])
        candidates = sorted(match.candidates, key=lambda c: (-c.score, c.author.casefold(), c.topic_id))
        for i, candidate in enumerate(candidates):
            tree.insert('', 'end', iid=str(i), values=(candidate.author, f'{candidate.score * 100:.1f}', candidate.title, candidate.reason))
        if match.candidates:
            tree.selection_set('0')
        files.insert('1.0', '\n'.join(f.path for f in match.group.files))
        files.configure(state='disabled')
        def chosen():
            return candidates[int(tree.selection()[0])] if tree.selection() else None
        def show_evidence(event=None):
            candidate = chosen()
            lines = [f'当前分析阈值：{self.analysis_threshold * 100:.0f} 分']
            if candidate:
                lines.extend([f'综合分：{candidate.score * 100:.1f} 分', candidate.reason,
                              f'已读取首帖：{"是" if candidate.verified else "否"}', *candidate.evidence])
                hits = match.group.search_hits.get(str(candidate.topic_id), [])
                if hits:
                    lines.append('命中检索片段（不计入确认分）：' + ' / '.join(hits))
            if match.error:
                lines.append(match.error)
            if match.selection_note:
                lines.append(match.selection_note)
            evidence.configure(state='normal')
            evidence.delete('1.0', 'end')
            evidence.insert('1.0', '\n\n'.join(lines))
            evidence.configure(state='disabled')
        tree.bind('<<TreeviewSelect>>', show_evidence)
        show_evidence()
        def assign(author):
            if not author:
                return
            try:
                match.author = author_from_input(author)
            except ValueError as exc:
                messagebox.showerror('作者格式', str(exc), parent=dialog)
                return
            match.status, match.selected, match.manual = '人工指定', True, True
            match.error = ''
            match.selection_note = '手动核对指定：' + match.author
            dialog.destroy()
            self.render()
        button(row, text='打开候选帖子', command=lambda: webbrowser.open(chosen().url) if chosen() else None).pack(side='left')
        button(row, text='手动填写作者', command=lambda: assign(simpledialog.askstring('作者', '作者用户名：', parent=dialog))).pack(side='left', padx=8)
        button(row, text='采用选中作者', accent=True, command=lambda: assign(chosen().author) if chosen() else None).pack(side='right')

    def move(self):
        if self.busy():
            return
        if self.sync_search.get() != self.analysis_sync_search:
            messagebox.showerror('分组方式已改变', '同步检索设置已改变，请重新分析后再执行归类。')
            return
        if self.target.get().strip() != self.analysis_target:
            messagebox.showerror('目录已改变', '请重新分析后再执行归类。')
            return
        try:
            if float(self.threshold.get()) / 100 != self.analysis_threshold:
                raise ValueError()
        except ValueError:
            messagebox.showerror('阈值已改变', '请重新分析后再执行归类。')
            return
        selected = [m for m in self.matches if m.selected and m.author]
        count = sum(len(m.group.files) for m in selected)
        if not selected:
            return
        if not messagebox.askyesno('执行归类', f'将 {len(selected)} 组、{count} 个文件移动到：\n{self.analysis_target}\n\n同名冲突会跳过，文件名保留。操作会记录，可按日志撤销。'):
            return
        target = Path(self.analysis_target)
        report = self.latest_report
        def work():
            if report:
                export_matches(self.matches, Path(report))
            result = execute(selected, target, STATE / 'journals', lambda s: self.emit('log', s), self.check)
            self.emit('moved', result)
        self.run(work)

    def undo_move(self):
        file = filedialog.askopenfilename(title='选择要撤销的归类日志', initialdir=str(STATE / 'journals'), filetypes=[('归类日志', '*.jsonl')])
        if not file:
            return
        if not messagebox.askyesno('撤销归类', '按此日志将文件移回原目录？原路径被占用或文件内容已改变的条目会跳过。'):
            return
        def work():
            result = undo(Path(file), lambda s: self.emit('log', s), self.check)
            self.emit('undone', result)
        self.run(work)

    def open_report(self):
        folder = Path(self.latest_report) if self.latest_report else REPORTS
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def import_index(self):
        filename = filedialog.askopenfilename(title='导入已保存的作者帖子 JSON', filetypes=[('帖子索引', '*.json')])
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
            rows = data.get('topics', []) if isinstance(data, dict) else data
            if not isinstance(rows, list):
                raise ValueError('JSON 中没有主题数组')
            author = ''
            if any(isinstance(row, dict) and not row.get('author') for row in rows):
                answer = simpledialog.askstring('补充作者', '此索引没有作者字段。请输入这份索引所属的作者用户名：', parent=self.root)
                if not answer:
                    return
                author = author_from_input(answer)
            store = Store(STATE / 'catalog.sqlite3')
            try:
                count = store.import_file(Path(filename), author)
            finally:
                store.close()
            self.write_log(f'已导入 {count} 条主题。可勾选“仅用本地索引”进行分析。')
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror('导入失败', str(exc))

    def stop(self):
        self.stop_event.set()
        self.status.set('正在停止；等待当前请求或文件操作收尾……')

    def write_log(self, text):
        self.write_logs([text])

    def write_logs(self, messages):
        if not messages:
            return
        records = [(getattr(text, 'created', None) or datetime.now(ZONE), str(text)) for text in messages]
        try:
            append_log(STATE / 'logs' / 'runtime.log', '\n'.join(
                stamp.isoformat(timespec='seconds') + ' ' + text for stamp, text in records))
        except OSError:
            pass
        from organizer.display_settings import append_log as append_visible_log
        # Dense cached searches may produce thousands of lines per second.
        # Persist every line, but never ask Tk to lay out that entire burst.
        visible = records[-100:]
        append_visible_log(self.log, ''.join(stamp.strftime('%H:%M:%S ') + text + '\n' for stamp, text in visible))
        self.status.set(records[-1][1].replace('\n', ' ')[:150])

    def open_runtime_log(self):
        path = STATE / 'logs' / 'runtime.log'
        if path.exists():
            os.startfile(str(path))
        else:
            self.status.set('尚未产生运行日志。')

    def open_network_stats(self):
        folder = STATE / 'diagnostics'
        folder.mkdir(parents=True, exist_ok=True)
        NetworkDiagnostics(folder).write_summary()
        os.startfile(str(folder / '网络规律汇总.txt'))

    def poll(self):
        render_pending = False
        logs = []
        latest_progress = None
        finished = False
        started = time.monotonic()
        processed = 0
        try:
            while processed < 1000 and time.monotonic() - started < .012:
                kind, value = self.events.get_nowait()
                processed += 1
                if kind == 'matches':
                    self.matches = value
                    render_pending = True
                elif kind == 'log':
                    logs.append(value)
                elif kind == 'login':
                    self.write_logs(logs); logs.clear()
                    self.resume_btn.configure(state='normal')
                    self.write_log(value)
                elif kind == 'resume_done':
                    self.resume_btn.configure(state='disabled')
                elif kind == 'progress':
                    latest_progress = value
                elif kind == 'report':
                    self.latest_report = value
                elif kind == 'error':
                    self.write_logs(logs); logs.clear()
                    self.write_log(value)
                    messagebox.showerror('操作未完成', value)
                elif kind in {'moved', 'undone'}:
                    self.write_logs(logs); logs.clear()
                    text = (f"移动 {value['moved']} 个文件，跳过 {value['skipped']} 个，问题 {len(value['errors'])} 项。"
                            if kind == 'moved' else f"恢复 {value['restored']} 个文件，问题 {len(value['errors'])} 项。")
                    self.write_log(text)
                    if value.get('journal'):
                        self.write_log('撤销日志：' + value['journal'])
                    for match in self.matches:
                        match.selected = False
                    self.render()
                    messagebox.showinfo('操作结果', text + '\n详情见运行日志；继续处理前请重新分析。')
                elif kind == 'finished':
                    finished = True
                    self.worker = None
                    self.set_busy(False)
                    if self.close_pending:
                        self.write_logs(logs)
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        self.write_logs(logs)
        if latest_progress:
            current, total, name = latest_progress
            self.progress.configure(maximum=max(1, total), value=current)
            if not finished:
                self.status.set(f'{current} / {total}：{name[:110]}')
        if finished:
            self.status.set(('已停止' if self.stop_event.is_set() else '本轮任务已结束') + '；已完成匹配保留，可核对或归类勾选项。')
        if render_pending:
            self.render()
        self.root.after(15 if not self.events.empty() else poll_delay(self.root, self.busy()), self.poll)

    def close(self):
        if self.busy():
            self.close_pending = True
            self.stop()
        else:
            self._save()
            self.root.destroy()


def main():
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Jiaxin.ESOrganizer')
    root = tk.Tk()
    from organizer.workspace import Workspace
    Workspace(root)
    root.mainloop()


if __name__ == '__main__':
    main()
