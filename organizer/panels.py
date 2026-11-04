"""Shared rounded desktop layout for every work area."""
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
from .ui import BG, PANEL, FIELD, BORDER, TEXT, MUTED, ACCENT, FONT, button, label, entry, combo, statistics, TaskProgress
from .presentation import flat_frame, bind_wrap, scrollbar
from .split_layout import SplitPane, split_grid


def frame(parent, **kw):
    return flat_frame(parent, **kw)


def card(parent):
    return ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=14)


def section(parent, number, title):
    row = frame(parent)
    row.pack(fill='x', pady=(14, 6))
    label(row, number, size=11, color=ACCENT).pack(side='left', padx=(0, 9))
    label(row, title, size=15, bold=True).pack(side='left')


def caption(parent, title):
    control = label(parent, title, color=MUTED, size=12, justify='left')
    control.pack(fill='x', pady=(10, 5))
    bind_wrap(parent, control, minimum=120)


def hint(parent, text):
    control = label(parent, text, size=11, color=MUTED, wraplength=250, justify='left')
    control.pack(fill='x', pady=5)
    bind_wrap(parent, control, minimum=120)
    return control


def switch(parent, text, variable, command=None):
    widget = ctk.CTkSwitch(parent, text=text, variable=variable, command=command, font=(FONT, 12), height=26,
        switch_width=34, switch_height=18, progress_color='#72a88b', fg_color='#383d45',
        button_color='#e3e7eb', button_hover_color='white', text_color=TEXT, text_color_disabled='#646b74')
    widget.pack(anchor='w', pady=5)
    return widget


def radio(parent, text, variable, value, command=None):
    widget = ctk.CTkRadioButton(parent, text=text, variable=variable, value=value, command=command,
        font=(FONT, 12), text_color=TEXT, text_color_disabled='#646b74', fg_color='#84b99b', hover_color='#a3dabc',
        border_color=BORDER, radiobutton_width=18, radiobutton_height=18)
    widget.pack(anchor='w', pady=4)
    return widget


def path_field(parent, title, variable, command):
    caption(parent, title)
    row = frame(parent)
    row.pack(fill='x', pady=(0, 3))
    row.columnconfigure(0, weight=1)
    control = entry(row, variable, width=100)
    control.grid(row=0, column=0, sticky='ew', padx=(0, 6))
    choose = button(row, '选择…', command, width=64)
    choose.grid(row=0, column=1)
    return control, choose


def text_box(parent, variable, height=64):
    box = ctk.CTkTextbox(parent, height=min(height, 96), fg_color=PANEL, text_color=MUTED,
                         corner_radius=0, font=(FONT, 12), wrap='word', border_width=0)
    def update(*_):
        value = variable.get()
        if getattr(box, '_last_text', None) == value:
            return
        box._last_text = value
        box.configure(state='normal')
        box.delete('1.0', 'end')
        box.insert('1.0', value)
        box.configure(state='disabled')
    trace = variable.trace_add('write', update)
    box.bind('<Destroy>', lambda e: variable.trace_remove('write', trace) if e.widget is box._textbox else None, add='+')
    update()
    return box


class ResultTable(ttk.Treeview):
    """Explain an empty result without a background poll or a fake data row."""
    def __init__(self, parent, empty_text='', **kwargs):
        super().__init__(parent, **kwargs)
        self.empty_hint = None
        if empty_text:
            self.empty_hint = tk.Label(self, text=empty_text, bg=PANEL, fg=MUTED,
                                       font=(FONT, -12), bd=0, justify='center', wraplength=260)
            self.empty_hint.place(relx=.5, rely=.55, anchor='center')
            bind_wrap(self, self.empty_hint, minimum=80)
        self._showing_empty = bool(empty_text)
        self._empty_job = None
        self.bind('<Destroy>', self._cancel_empty, add='+')

    def _cancel_empty(self, event):
        if event.widget is self and self._empty_job is not None:
            self.after_cancel(self._empty_job)
            self._empty_job = None

    def _refresh_empty(self):
        self._empty_job = None
        if not self.get_children():
            self.empty_hint.place(relx=.5, rely=.55, anchor='center')
            self._showing_empty = True

    def insert(self, *args, **kwargs):
        result = super().insert(*args, **kwargs)
        if self._showing_empty:
            self.empty_hint.place_forget()
            self._showing_empty = False
        return result

    def delete(self, *items):
        super().delete(*items)
        if self.empty_hint is not None and self._empty_job is None:
            # Inspect once per batch, rather than copying all rows per deletion.
            self._empty_job = self.after_idle(self._refresh_empty)


def table(parent, columns, *, tree=False, empty_text=None):
    shell = tk.Frame(parent, bg=PANEL)
    shell.pack(fill='both', expand=True, padx=12, pady=(0, 8))
    shell.rowconfigure(0, weight=1)
    shell.columnconfigure(0, weight=1)
    keys = [c[0] for c in columns if c[0] != '#0']
    if empty_text is None:
        empty_text = getattr(parent, '_es_empty_text', '')
    widget = ResultTable(shell, empty_text=empty_text, columns=keys, show='tree headings' if tree else 'headings', selectmode='browse')
    for key, title, width in columns:
        widget.heading(key, text=title, anchor='w')
        widget.column(key, width=width, minwidth=min(100, width), stretch=width >= 180, anchor='w')
    widget.grid(row=0, column=0, sticky='nsew')
    vertical = scrollbar(shell, command=widget.yview, button_color=BORDER)
    vertical.grid(row=0, column=1, sticky='ns')
    horizontal = scrollbar(shell, command=widget.xview, orientation='horizontal', height=12, button_color=BORDER)
    horizontal.grid(row=1, column=0, sticky='ew')
    widget.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    return widget


class Workbench:
    def __init__(self, parent, title, status, stat_titles=()):
        from .display_settings import preferences
        self.key = title + ('/视频' if title == '查重结果' and '时长 / 名称相近' in stat_titles else '')
        parent.configure(bg=BG)
        self.page = frame(parent)
        self.page.pack(fill='both', expand=True, padx=16, pady=14)
        self.page.columnconfigure(1, weight=1)
        self.page.rowconfigure(0, weight=1)
        self.sidebar = ctk.CTkFrame(self.page, fg_color=PANEL, corner_radius=14, width=preferences(parent)['sidebar_width'])
        parent._es_sidebar = self.sidebar
        self.sidebar.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        self.sidebar.rowconfigure(0, weight=1)
        self.settings = ctk.CTkScrollableFrame(self.sidebar, width=264, fg_color=PANEL, corner_radius=0,
                                              scrollbar_button_color=BORDER)
        self.settings.grid(row=0, column=0, sticky='nsew', padx=9, pady=(9, 0))
        self.bottom = frame(self.sidebar)
        self.bottom.grid(row=1, column=0, sticky='ew', padx=16, pady=14)
        self.right = frame(self.page)
        self.right.grid(row=0, column=1, sticky='nsew')
        self.right.columnconfigure(0, weight=1)
        self.right.rowconfigure(1, weight=1)
        stats, self.stat_values = statistics(self.right, stat_titles)
        if stat_titles:
            stats.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        self.result = card(self.right)
        self.result._es_empty_text = {
            '配对结果': '暂无配对结果\n选择视频和脚本目录后，点击“查找候选”。',
            '查重结果': '暂无查重结果\n选择扫描目录后开始扫描，也可读取已有报告。',
            '子文件夹组数': '暂无统计结果\n选择根目录后，点击“扫描子文件夹”。',
            '采集预览': '暂无采集结果\n填写作者及保存目录后开始采集。',
            '缺失资源清单': '暂无扫描结果\n选择作者素材文件夹，可先“只扫描本地”。',
            '综合整理预览': '暂无整理预览\n添加来源目录后，采集或使用本地索引生成预览。',
            '资源队列': '下载队列为空\n在左侧粘贴链接，或从采集结果加入下载。',
        }.get(title, '')
        self.result.grid(row=1, column=0, sticky='nsew')
        self.toolbar = frame(self.result)
        self.toolbar.pack(fill='x', padx=18, pady=(16, 12))
        label(self.toolbar, title, size=17, bold=True).pack(side='left')
        self.footer = frame(self.result)
        self.footer.pack(side='bottom', fill='x', padx=16, pady=(0, 12))
        self.progress = TaskProgress(self.right)
        self.progress.grid(row=3, column=0, sticky='ew', pady=(12, 4))
        status_label = label(self.right, textvariable=status, size=11, color=MUTED, height=34, justify='left')
        status_label.grid(row=4, column=0, sticky='ew')
        bind_wrap(self.right, status_label)
        self.main_split = split_grid(self.page, [self.sidebar, self.right], self.key+'/左右',
                                     [preferences(parent)['sidebar_width'], None], [280, 420], 'horizontal')
        parent._es_sidebar_split = self.main_split
        # CTk may flush idle jobs while constructing controls; run after build.
        parent.after(0, self.finish_layout)

    def finish_layout(self):
        if not self.page.winfo_exists() or getattr(self, '_layout_finished', False):
            return
        self._layout_finished = True
        self.page.update_idletasks()
        # Keep the existing widgets/controllers; only their geometry manager changes.
        items = [w for w in self.result.pack_slaves() if w not in (self.toolbar, self.footer)]
        from .action_layout import wrap_actions
        wrap_actions(self.toolbar, self.refresh_minimums)
        for item in items:
            if isinstance(item, tk.Frame):
                wrap_actions(item, self.refresh_minimums)
        result_min = 200
        children = self.footer.pack_slaves()
        details = next((w for w in children if isinstance(w, ctk.CTkTextbox)), None)
        # A one-line hint/action row is not a resizable content pane.
        if items and details is not None:
            height = self.footer.winfo_reqheight() + 32
            self.footer.pack_forget()
            self.detail_split = SplitPane(self.result, self.key+'/详情', 'vertical')
            content = tk.Frame(self.detail_split, bg=PANEL)
            for widget in items:
                info = widget.pack_info(); info.pop('in', None)
                widget.pack_forget(); widget.pack(in_=content, **info)
                widget.lift()
            footer_min = 85
            if details:
                # Give fixed action rows space first; only the scrollable details shrink.
                before = tk.Frame(self.footer, bg=PANEL)
                after = tk.Frame(self.footer, bg=PANEL)
                after_text = False
                for widget in children:
                    info = widget.pack_info(); info.pop('in', None)
                    widget.pack_forget()
                    if widget is details:
                        after_text = True
                    else:
                        widget.pack(in_=after if after_text else before, **info)
                        widget.lift()
                self.footer.columnconfigure(0, weight=1)
                self.footer.rowconfigure(1, weight=1, minsize=52)
                label(before, '选中项详情', size=12, color=MUTED, bold=True).pack(side='top', fill='x', before=before.pack_slaves()[0] if before.pack_slaves() else None)
                before.grid(row=0, column=0, sticky='ew', padx=10)
                details.grid(row=1, column=0, sticky='nsew', padx=6, pady=(0, 4))
                after.grid(row=2, column=0, sticky='ew', padx=10, pady=(0, 8))
                details.lift()
                self.footer.update_idletasks()
                footer_min = max(85, before.winfo_reqheight()+after.winfo_reqheight()+60)
                self.detail_rows = before, after
                before.bind('<Configure>', lambda _: self.refresh_minimums(), add='+')
                after.bind('<Configure>', lambda _: self.refresh_minimums(), add='+')
                wrap_actions(after, self.refresh_minimums)
                for row in (*before.pack_slaves(), *after.pack_slaves()):
                    if isinstance(row, tk.Frame):
                        wrap_actions(row, self.refresh_minimums)
            self.detail_split.add_panel(content, '列表', None, 100)
            self.detail_split.add_panel(self.footer, '详情', height, footer_min)
            self.detail_split.pack(fill='both', expand=True, padx=8, pady=(0, 8))
            result_min = footer_min + 100 + self.toolbar.winfo_reqheight() + 40
        logs = [w for w in self.right.grid_slaves(row=2) if w is not self.result]
        if logs:
            log = logs[0]
            if isinstance(log, ctk.CTkTextbox):
                log.grid_forget()
                self.log_panel = card(self.right)
                self.log_panel.grid(row=2, column=0, sticky='nsew')
                label(self.log_panel, '运行日志', size=12, bold=True, color=MUTED).pack(fill='x', padx=16, pady=(6, 0))
                log.configure(corner_radius=0, border_width=0)
                log.pack(in_=self.log_panel, fill='both', expand=True, padx=12, pady=(0, 8))
                log.lift()
                log = self.log_panel
                log.update_idletasks()
            self.log_split = split_grid(self.right, [self.result, log], self.key+'/日志',
                                        [None, log.winfo_reqheight()], [result_min, 65])

    def refresh_minimums(self):
        """Wrapping action rows must reserve room before details are shrunk."""
        if getattr(self, '_minimum_job', None) is not None:
            return
        def update():
            self._minimum_job = None
            if not self.page.winfo_exists() or not hasattr(self, 'detail_rows'):
                return
            minimum = max(85, sum(row.winfo_reqheight() for row in self.detail_rows)+60)
            self.detail_split.paneconfigure(self.footer, minsize=minimum)
            self.detail_split.specs = [(w, k, s, minimum if w is self.footer else m)
                                       for w, k, s, m in self.detail_split.specs]
            if hasattr(self, 'log_split'):
                result_min = minimum+100+self.toolbar.winfo_reqheight()+40
                self.log_split.paneconfigure(self.result, minsize=result_min)
                self.log_split.specs = [(w, k, s, result_min if w is self.result else m)
                                        for w, k, s, m in self.log_split.specs]
                if self.result.winfo_height() < result_min:
                    self.log_split.sash_place(0, 0, result_min)
            # Tk records a new minsize without moving an already placed sash.
            # Make room only when necessary; retain the user's larger detail pane.
            if self.footer.winfo_height() < minimum:
                self.detail_split.sash_place(0, 0, max(100, self.detail_split.winfo_height()-8-minimum))
        self._minimum_job = self.page.master.after_idle(update)

    def stats(self, *values):
        for variable, value in zip(self.stat_values, values):
            if variable.get() != str(value):
                variable.set(str(value))
