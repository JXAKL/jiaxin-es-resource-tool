"""Dark desktop presentation. Matching, persistence and file operations live elsewhere."""
import ctypes
import sys
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk
from PIL import Image

from .runtime import BUNDLE, APP_NAME
from .matching import configured_workers, worker_limit
from .presentation import flat_frame, bind_wrap, background, scrollbar

BG = '#101113'
PANEL = '#1b1d20'
FIELD = '#141619'
BORDER = '#303338'
TEXT = '#eef0f3'
MUTED = '#9298a3'
ACCENT = '#c3efd6'
FONT = 'Microsoft YaHei UI'


def label(parent, text='', size=13, color=TEXT, bold=False, **kwargs):
    # Text needs no rounded canvas. Native labels avoid two extra widgets and
    # a Python redraw callback for every label on every resize.
    height = kwargs.pop('height', 28)
    return tk.Label(parent, text=text, fg=color, bg=background(parent), anchor='w',
                    font=(FONT, -size, 'bold' if bold else 'normal'), borderwidth=0,
                    highlightthickness=0, padx=0, pady=max(1, int((height-size*1.4)/2)), **kwargs)


def button(parent, text, command, accent=False, width=88, quiet=False, **kwargs):
    return ctk.CTkButton(parent, text=text, command=command, width=width, height=34,
                         corner_radius=8, font=(FONT, 13), border_width=0,
                         border_color=BORDER, fg_color=ACCENT if accent else 'transparent' if quiet else '#292c31',
                         hover_color='#a3dabc' if accent else '#363b42',
                         text_color='#15241c' if accent else TEXT,
                         text_color_disabled='#646b74', **kwargs)


def statistics(parent, titles, colors=()):
    """One quiet summary strip, not a separate card for every number."""
    strip = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=14)
    values = []
    for i, title in enumerate(titles):
        strip.columnconfigure(i, weight=1, uniform='statistics')
        cell = flat_frame(strip)
        cell.grid(row=0, column=i, sticky='ew', padx=18, pady=10)
        label(cell, title, size=12, color=MUTED, height=22).pack(anchor='w')
        value = tk.StringVar(master=parent, value='0')
        label(cell, textvariable=value, size=23, height=30, bold=True,
              color=colors[i] if i < len(colors) else ACCENT if i == 1 else TEXT).pack(anchor='w')
        values.append(value)
    return strip, values


class TaskProgress(ttk.Progressbar):
    """An idle indeterminate ttk bar paints a misleading green block."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, mode='determinate', **kwargs)

    def start(self, interval=None):
        self.configure(mode='indeterminate')
        super().start(interval)

    def stop(self):
        super().stop()
        self.configure(mode='determinate', value=0)


def entry(parent, variable, width=140):
    return ctk.CTkEntry(parent, textvariable=variable, width=width, height=34,
                        corner_radius=9, border_width=1, border_color=BORDER,
                        fg_color=FIELD, text_color=TEXT, font=(FONT, 13))


def combo(parent, variable, values, width=125, command=None):
    return ctk.CTkComboBox(parent, variable=variable, values=values, width=width, height=34,
                         corner_radius=9, border_width=1, border_color=BORDER, state='readonly',
                         fg_color=FIELD, button_color=BORDER, button_hover_color='#454a52',
                         text_color=TEXT, dropdown_fg_color=PANEL, dropdown_text_color=TEXT,
                         dropdown_hover_color=BORDER, font=(FONT, 12), dropdown_font=(FONT, 12), command=command)


def decorate_window(window):
    if getattr(window, '_jiaxin_decorated', False):
        return
    window._jiaxin_decorated = True
    window.configure(bg=BG)
    icon = BUNDLE / 'assets' / 'jiaxin.ico'
    if icon.exists():
        window.iconbitmap(str(icon))
        window.iconbitmap(default=str(icon))
    if sys.platform == 'win32':
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        # DWM dark title bar and rounded corners where supported by Windows.
        for attribute, value in ((20, 1), (33, 2)):
            setting = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(setting), ctypes.sizeof(setting))


class DesktopUI:
    def _style(self):
        ctk.set_appearance_mode('dark')
        # Both native ttk and custom controls use the same window coordinate scale.
        ctk.deactivate_automatic_dpi_awareness()
        self.root.option_add('*Font', (FONT, 10))
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', background=PANEL, foreground=TEXT, font=(FONT, 10), bordercolor=BORDER)
        style.configure('TFrame', background=PANEL)
        style.configure('TLabel', background=PANEL, foreground=TEXT)
        style.configure('Muted.TLabel', foreground=MUTED)
        style.configure('TButton', background='#272a2f', foreground=TEXT, padding=(10, 7), borderwidth=0)
        style.map('TButton', background=[('active', '#363b42')], foreground=[('disabled', '#646b74')])
        style.configure('Accent.TButton', background=ACCENT, foreground='#15241c')
        style.map('Accent.TButton', background=[('active', '#a3dabc')])
        style.configure('Treeview', rowheight=37, background=PANEL, fieldbackground=PANEL,
                        foreground=TEXT, borderwidth=0, lightcolor=PANEL, darkcolor=PANEL)
        style.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
        style.configure('Treeview.Heading', background='#25282d', foreground=MUTED,
                        font=(FONT, 9, 'bold'), padding=(6, 10), relief='flat')
        style.map('Treeview.Heading', background=[('active', '#30353c')])
        style.map('Treeview', background=[('selected', '#30483d')], foreground=[('selected', '#effff5')])
        for direction, axis in (('Vertical', 'ns'), ('Horizontal', 'ew')):
            name = f'Slim.{direction}.TScrollbar'
            style.configure(name, background=BORDER, troughcolor=PANEL, borderwidth=0,
                            lightcolor=BORDER, darkcolor=BORDER, arrowsize=10, width=10, gripcount=0)
            style.map(name, background=[('active', '#515965')])
            style.layout(name, [(f'{direction}.Scrollbar.trough', {'sticky': axis, 'children': [
                (f'{direction}.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        style.configure('Horizontal.TProgressbar', troughcolor=BG, background=ACCENT,
                        borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG, thickness=4)
        style.layout('Horizontal.TProgressbar', [('Horizontal.Progressbar.trough',
            {'sticky': 'nswe', 'children': [('Horizontal.Progressbar.pbar', {'side': 'left', 'sticky': 'ns'})]})])
        style.configure('TNotebook', background=PANEL, borderwidth=0)
        style.configure('TNotebook.Tab', background=FIELD, foreground=MUTED, padding=(16, 8))
        style.map('TNotebook.Tab', background=[('selected', '#30353c')], foreground=[('selected', TEXT)])
        decorate_window(self.root.winfo_toplevel())

    def _build(self):
        self.root.configure(background=BG)
        main = flat_frame(self.root)
        main.pack(fill='both', expand=True, padx=16, pady=14)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(1, weight=1)
        if not getattr(self.root, 'embedded', False):
            header = flat_frame(main)
            header.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 20))
            with Image.open(BUNDLE / 'assets' / 'jiaxin.jpg') as original:
                self.brand_image = ctk.CTkImage(original.copy(), size=(48, 48))
            ctk.CTkLabel(header, image=self.brand_image, text='', width=48).pack(side='left', padx=(0, 14))
            label(header, APP_NAME, size=24, bold=True).pack(side='left')

        sidebar = ctk.CTkFrame(main, fg_color=PANEL, corner_radius=14, width=324)
        self.root._es_sidebar = sidebar
        sidebar.grid(row=1, column=0, sticky='nsew', padx=(0, 18))
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(0, weight=1)
        settings = ctk.CTkScrollableFrame(sidebar, fg_color=PANEL, corner_radius=0, width=284,
                                         scrollbar_button_color=BORDER, scrollbar_button_hover_color='#515965')
        settings.grid(row=0, column=0, sticky='nsew', padx=10, pady=(10, 0))
        self.settings_panel = settings
        self.edit_buttons = []
        def section(number, title):
            row = flat_frame(settings)
            row.pack(fill='x', pady=(12, 10))
            label(row, number, size=11, color=ACCENT).pack(side='left', padx=(0, 9))
            label(row, title, size=15, bold=True).pack(side='left')
        def caption(text):
            control = label(settings, text, color=MUTED, size=12, justify='left')
            control.pack(fill='x', pady=(10, 5))
            bind_wrap(settings, control, minimum=120)
        def switch(text, var, command=None):
            control = ctk.CTkSwitch(settings, text=text, variable=var, command=command, font=(FONT, 12), height=26,
                          switch_width=34, switch_height=18, progress_color='#72a88b',
                          fg_color='#383d45', button_color='#e3e7eb', button_hover_color='white',
                          text_color=TEXT)
            control.pack(anchor='w', pady=4)
            return control
        section('01', '素材与输出')
        sourcebox = ctk.CTkFrame(settings, fg_color=FIELD, corner_radius=10, border_width=1, border_color=BORDER)
        sourcebox.pack(fill='x')
        self.sources = tk.Listbox(sourcebox, height=3, bg=FIELD, fg=TEXT, relief='flat', borderwidth=0,
                                   highlightthickness=0, selectbackground='#30483d', selectforeground=TEXT,
                                   exportselection=False, font=(FONT, 9))
        self.sources.pack(fill='x', padx=10, pady=(9, 0))
        scroll = scrollbar(sourcebox, orientation='horizontal', height=10, command=self.sources.xview)
        scroll.pack(fill='x', padx=10, pady=(0, 5))
        self.sources.configure(xscrollcommand=scroll.set)
        row = flat_frame(settings)
        row.pack(fill='x', pady=(8, 0))
        for i, (title, command) in enumerate((('+ 添加目录', self.add_source), ('移除', self.remove_source))):
            row.columnconfigure(i, weight=1, uniform='sources')
            control = button(row, title, command, width=80)
            control.grid(row=0, column=i, sticky='ew', padx=(0, 6 if i == 0 else 0))
            self.edit_buttons.append(control)
        caption('归类目标目录')
        row = flat_frame(settings)
        row.pack(fill='x', pady=(0, 8))
        row.columnconfigure(0, weight=1)
        self.target_entry = entry(row, self.target, width=100)
        self.target_entry.grid(row=0, column=0, sticky='ew', padx=(0, 6))
        choose = button(row, '选择…', self.choose_target, width=64)
        choose.grid(row=0, column=1)
        self.edit_buttons.append(choose)
        switch('包含子目录', self.recursive)
        self.sync_search_switch = switch('同步检索', self.sync_search, self.grouping_changed)
        label(settings, '开启：视频与对应脚本合为一组\n关闭：视频与脚本分开匹配',
              size=11, color=MUTED, justify='left').pack(anchor='w', pady=(2, 6))
        section('02', '网站与索引')
        label(settings, '已知作者 · 可选', size=12, color=MUTED).pack(anchor='w', pady=(0, 5))
        entry(settings, self.seed).pack(fill='x')
        label(settings, '用户名或主页，多个用逗号隔开', size=11, color=MUTED).pack(anchor='w', pady=(4, 8))
        switch('显示浏览器', self.visible)
        switch('仅用本地索引', self.offline)
        switch('刷新缓存', self.refresh)
        row = flat_frame(settings)
        row.pack(fill='x', pady=(10, 0))
        self.browser_entry = combo(row, self.browser_choice, ['Chrome', 'Edge', 'Chromium'], width=142)
        self.browser_entry.pack(side='left')
        self.login_btn = button(row, '登录网站', self.login, width=104)
        self.login_btn.pack(side='right')
        section('03', '匹配设置')
        def setting(text, variable, help_text):
            row = flat_frame(settings)
            row.pack(fill='x', pady=(8, 0))
            label(row, text, size=12).pack(side='left')
            control = entry(row, variable, width=66)
            control.pack(side='right')
            label(settings, help_text, size=11, color=MUTED, wraplength=254, justify='left').pack(anchor='w', pady=(4, 2))
            return control
        self.threshold_entry = setting('作者确认阈值', self.threshold, '70–100 分 · 首帖证据或作者累计证据，无歧义')
        setting('本次搜索上限', self.limit, '按文件组计数，0 表示不限')
        self.interval_entry = setting('请求间隔 / 秒', self.request_interval, '响应结束后的最短等待 · 可填小数，0 为不等待')
        self.search_interval_entry = setting('搜索间隔 / 秒', self.search_interval, '两次搜索的最短间隔 · 0 关闭此项限制')
        self.per_minute_entry = setting('每分钟请求上限', self.per_minute, '滚动 60 秒内最多请求次数 · 0 为不限')
        self.cooldown_entry = setting('异常冷却 / 秒', self.cooldown_seconds, '异常后最短等待 · 仍遵守网站 Retry-After')
        self.workers_entry = setting('本地计算进程', self.compute_workers,
                                     f'0 为自动（当前 {configured_workers(0)} 个） · 可选 1–{worker_limit()}')
        footer = flat_frame(sidebar)
        footer.grid(row=1, column=0, sticky='ew', padx=18, pady=16)
        self.start_btn = button(footer, '开始分析', self.start, accent=True)
        self.start_btn.pack(fill='x')
        row = flat_frame(footer)
        row.pack(fill='x', pady=(8, 0))
        self.stop_btn = button(row, '停止', self.stop, width=76, state='disabled')
        self.stop_btn.pack(side='left')
        self.resume_btn = button(row, '已完成，继续', self.resume_event.set, width=198, state='disabled')
        self.resume_btn.pack(side='right')

        workspace = flat_frame(main)
        workspace.grid(row=1, column=1, sticky='nsew')
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(1, weight=1)
        stats, self.stat_values = statistics(workspace, ('素材组数', '可以归类', '等待核对'),
                                              (TEXT, ACCENT, '#e7cba3'))
        stats.grid(row=0, column=0, sticky='ew', pady=(0, 10))

        panel = ctk.CTkFrame(workspace, fg_color=PANEL, corner_radius=14)
        panel.grid(row=1, column=0, sticky='nsew')
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(2, weight=1)
        tools = flat_frame(panel)
        tools.grid(row=0, column=0, sticky='ew', padx=18, pady=(16, 2))
        label(tools, '归类预览', size=17, bold=True).pack(side='left')
        self.filter_entry = combo(tools, self.filter_var, ['全部', '可归类', '待核对', '未匹配', '搜索失败'],
                                  width=122, command=lambda _: self.render())
        self.filter_entry.pack(side='right')
        self.best_author_btn = button(tools, '一键取最高分', self.choose_best_authors, width=118, state='disabled')
        self.best_author_btn.pack(side='right', padx=10)
        label(panel, textvariable=self.summary, color=MUTED, size=11).grid(row=1, column=0, sticky='w', padx=18, pady=(3, 12))
        frame = tk.Frame(panel, bg=PANEL)
        frame.grid(row=2, column=0, sticky='nsew', padx=12)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        columns = ('pick', 'status', 'title', 'type', 'files', 'author', 'score', 'reason')
        self.tree = ttk.Treeview(frame, columns=columns, show='headings', selectmode='extended')
        for key, title, width in [('pick', '选择', 44), ('status', '状态', 95), ('title', '文件名称', 270),
                                  ('type', '文件类型', 128),
                                  ('files', '文件', 52), ('author', '归属作者', 118), ('score', '评分', 55), ('reason', '匹配依据', 230)]:
            self.tree.heading(key, text=title, anchor='w')
            self.tree.column(key, width=width, minwidth=width if key not in {'title', 'reason'} else 180,
                             stretch=key in {'title', 'reason'}, anchor='center' if key in {'pick', 'files', 'score'} else 'w')
        sy = scrollbar(frame, orientation='vertical', command=self.tree.yview, button_color=BORDER)
        sx = scrollbar(frame, orientation='horizontal', command=self.tree.xview, height=12, button_color=BORDER)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        sy.grid(row=0, column=1, sticky='ns')
        sx.grid(row=1, column=0, sticky='ew')
        self.tree.bind('<Double-1>', self.details)
        self.tree.bind('<Button-1>', self.toggle)
        self.tree.tag_configure('review', foreground='#dfc494')
        self.tree.tag_configure('ready', foreground=ACCENT)
        self.empty_hint = tk.Label(self.tree, text='还没有匹配结果\n\n添加素材目录，开始分析后在这里核对作者。',
                                   bg=PANEL, fg=MUTED, font=(FONT, 11), justify='center')
        self.empty_hint.place(relx=.5, rely=.5, anchor='center')
        evidence_hint = label(panel, '双击查看证据或指定作者 · 一键取最高分仅处理当前筛选中的未确认多作者项 · 评分不代表概率', color=MUTED, size=11, justify='left')
        evidence_hint.grid(row=3, column=0, sticky='ew', padx=18, pady=(8, 12))
        bind_wrap(frame, evidence_hint, minimum=120)
        actions = flat_frame(panel)
        actions.grid(row=4, column=0, sticky='ew', padx=16, pady=(0, 16))
        self.import_btn = button(actions, '导入索引', self.import_index, width=80)
        self.import_btn.pack(side='left')
        self.report_btn = button(actions, '打开报告', self.open_report, width=80)
        self.report_btn.pack(side='left', padx=6)
        button(actions, '网络统计', self.open_network_stats, width=80).pack(side='left', padx=(0, 6))
        button(actions, '取消全选', self.uncheck, width=80).pack(side='left')
        self.move_btn = button(actions, '归类勾选项', self.move, accent=True, width=112, state='disabled')
        self.move_btn.pack(side='right')
        self.undo_btn = button(actions, '撤销归类', self.undo_move, width=84)
        self.undo_btn.pack(side='right', padx=6)
        from .action_layout import wrap_actions
        wrap_actions(tools)
        wrap_actions(actions)
        logs = ctk.CTkFrame(workspace, fg_color=PANEL, corner_radius=14)
        logs.grid(row=2, column=0, sticky='ew', pady=(14, 0))
        log_header = flat_frame(logs)
        log_header.pack(fill='x', padx=16, pady=(8, 5))
        label(log_header, '运行日志', size=13, bold=True).pack(side='left')
        button(log_header, '完整日志', self.open_runtime_log, width=82).pack(side='right')
        logbody = tk.Frame(logs, bg=PANEL)
        logbody.pack(fill='x', padx=16, pady=(0, 12))
        self.log = tk.Text(logbody, height=4, state='disabled', wrap='word', relief='flat', bg=PANEL,
                           fg=MUTED, insertbackground=TEXT, selectbackground='#30483d', highlightthickness=0,
                           font=(FONT, 9), spacing1=3)
        logscroll = scrollbar(logbody, height=60, command=self.log.yview, button_color=BORDER)
        logscroll.pack(side='right', fill='y')
        self.log.configure(yscrollcommand=logscroll.set)
        self.log.pack(fill='x')
        self.progress = ttk.Progressbar(workspace, mode='determinate', length=0)
        self.progress.grid(row=3, column=0, sticky='ew', pady=(12, 6))
        self.status_label = label(workspace, textvariable=self.status, size=11, color=MUTED, justify='left', height=32)
        self.status_label.grid(row=4, column=0, sticky='ew')
        bind_wrap(workspace, self.status_label, 300)
        from .split_layout import split_grid
        from .display_settings import preferences
        self.main_split = split_grid(main, [sidebar, workspace], '作者归类/左右',
                                     [preferences(self.root)['sidebar_width'], None], [280, 420], 'horizontal')
        self.root._es_sidebar_split = self.main_split
        self.log_split = split_grid(workspace, [panel, logs], '作者归类/日志', [None, 142], [230, 70])
        logbody.pack_configure(fill='both', expand=True)
        self.log.pack_configure(fill='both', expand=True)

    def update_summary_cards(self, ready, review):
        for variable, value in zip(self.stat_values, (len(self.matches), ready, review)):
            variable.set(str(value))
