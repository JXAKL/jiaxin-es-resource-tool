"""Layouts for local tasks using the same rounded workbench as author filing."""
import math
import threading
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from .panels import *


def build_pair(self):
    from .local_tools.ui_theme import apply_theme
    apply_theme(self.root)
    self.layout = Workbench(self.root, '配对结果', self.status, ('候选视频', '已勾选', '已处理'))
    left = self.layout.settings
    section(left, '01', '素材与输出')
    for title, var, action in [('视频目录', self.video_dir, self.choose_video_dir),
                               ('Funscript 目录', self.funscript_dir, self.choose_funscript_dir),
                               ('整理到', self.output_dir, self.choose_output_dir),
                               ('CSV 报告 · 可选', self.output_csv, self.choose_output_csv)]:
        path_field(left, title, var, action)
    section(left, '02', '匹配方式')
    switch(left, '名称相似度', self.use_name)
    switch(left, '总时长相近', self.use_duration)
    row = frame(left)
    row.pack(fill='x', pady=8)
    for text, var in [('最低候选分', self.low_threshold), ('高分阈值', self.high_threshold)]:
        group = frame(row)
        group.pack(side='left', expand=True, fill='x', padx=(0, 6))
        caption(group, text)
        entry(group, var, width=112).pack(fill='x')
    self.auto_select_switch = switch(left, '自动勾选高置信项', self.auto_select)
    hint(left, '新分析结果自动勾选完整名称命中且无冲突的项目；缺少时长不扣名称分。勾选后再整理。')
    section(left, '03', '整理方式')
    self.output_controls = [radio(left, '复制 · 保留原文件', self.operation_mode, 'copy'),
                            radio(left, '移动 · 整理到目标目录', self.operation_mode, 'move')]
    self.include_scripts_switch = switch(left, '连同配对脚本一起整理', self.include_scripts)
    self.separate_folders_switch = switch(left, '每个视频与脚本组独立建文件夹', self.separate_folders)
    self.output_controls.extend([
        switch(left, '覆盖同名文件', self.overwrite),
        switch(left, '分析后直接处理高分项', self.auto_high),
        switch(left, '按脚本运动强度分文件夹', self.classify_intensity)])
    self.output_hint_label = hint(left, '')
    self.output_hint_label.configure(textvariable=self.output_hint)
    self.intensity_button = button(left, '强度分级设置', self.intensity_settings)
    self.intensity_button.pack(fill='x', pady=(8, 0))
    self.intensity_panel = frame(left)
    self.grade_avg = tk.StringVar(value=str(self.avg_reference))
    self.grade_peak = tk.StringVar(value=str(self.max_reference))
    self.grade_gap = tk.StringVar(value=str(self.max_gap))
    self.grade_relative = tk.BooleanVar(value=self.relative_mode)
    for title, var in [('平均速度参考值', self.grade_avg), ('最大速度参考值', self.grade_peak), ('长间隔秒数 · 0 不过滤', self.grade_gap)]:
        caption(self.intensity_panel, title)
        entry(self.intensity_panel, var).pack(fill='x')
    switch(self.intensity_panel, '按本批脚本相对评分', self.grade_relative)
    hint(self.intensity_panel, '有效运动段平均速度占 80%，最大速度占 20%。已处理文件保持原位置。')
    button(self.intensity_panel, '应用并刷新评分', lambda: apply_intensity(self), accent=True).pack(fill='x', pady=7)
    self.start_button = button(self.layout.bottom, '查找候选', self.start_analysis, accent=True)
    self.start_button.pack(fill='x')
    button(self.layout.bottom, '打开输出目录', self.open_output_dir).pack(fill='x', pady=(8, 0))
    toolbar = self.layout.toolbar
    button(toolbar, '播放与同步曲线', self.open_playback, width=136).pack(side='right')
    self.selection_summary = tk.StringVar(value='0 个候选 · 已勾选 0')
    label(self.layout.result, textvariable=self.selection_summary, size=11, color=MUTED).pack(fill='x', padx=18, pady=(0, 8))
    self.details = tk.StringVar(value='单击查看路径与评分；双击视频可播放核对。勾选后再整理。')
    text_box(self.layout.footer, self.details, 82).pack(fill='x', pady=(4, 10))
    actions = frame(self.layout.footer)
    actions.pack(fill='x')
    self.select_high_button = button(actions, '勾选高置信项', self.select_high_confidence, width=112)
    self.select_high_button.pack(side='left', padx=(0, 8))
    button(actions, '全选未处理项', self.select_all, width=120).pack(side='left')
    button(actions, '全不选', self.select_none, width=80).pack(side='left', padx=8)
    self.process_button = button(actions, '处理勾选项', self.process_selected, accent=True, width=120)
    self.process_button.pack(side='right')
    self.tree = table(self.layout.result, [('selected', '勾选', 52), ('funscript', 'Funscript', 240), ('video', '候选视频', 240),
        ('name', '名称分', 70), ('duration', '时长分', 70), ('total', '综合分', 80), ('intensity', '强度 / 分', 145), ('status', '状态', 155)])
    self.tree.bind('<Button-1>', self.on_tree_click)
    self.tree.bind('<<TreeviewSelect>>', self.show_details)
    self.tree.bind('<Double-1>', self.on_tree_double_click)
    self.tree.bind('<space>', self.toggle_focused)
    self.tree.heading('selected', text='全选', command=self.toggle_all)
    for tag, color in [('protected', MUTED), ('review', '#dfc494'), ('confirmed', ACCENT)]:
        self.tree.tag_configure(tag, foreground=color)
    self.progress = self.layout.progress


def apply_intensity(self):
    if self.running:
        return
    try:
        a, p, g = float(self.grade_avg.get()), float(self.grade_peak.get()), float(self.grade_gap.get())
        if not all(math.isfinite(v) and v > 0 for v in (a, p)) or not math.isfinite(g) or g < 0:
            raise ValueError()
    except ValueError:
        messagebox.showwarning('参考值无效', '速度参考值须大于 0；间隔须大于等于 0。', parent=self.root)
        return
    self.avg_reference, self.max_reference, self.max_gap = a, p, g
    self.relative_mode = relative = self.grade_relative.get()
    if any(row.get('status') != '已处理' for row in self.rows):
        from .local_tools.script_intensity import grade_batch
        self.set_buttons(True)
        self.status.set('正在刷新未处理项的强度评分…')
        def run():
            try:
                self.cohort = grade_batch(self.rows, a, p, g, relative)
                self.message_queue.put(('done', self.rows))
            except Exception as exc:
                self.message_queue.put(('error', str(exc)))
        threading.Thread(target=run, daemon=True).start()
    else:
        self.status.set('强度设置已应用，下次分析生效。')


def build_duplicate(self):
    self.layout = Workbench(self.window, '查重结果', self.status,
        ('已扫描文件', '时长 / 名称相近', '完全重复组') if self.is_video else ('已扫描文件', '完全重复组', '问题 / 跳过'))
    left = self.layout.settings
    section(left, '01', '文件与范围')
    if getattr(self.window, 'workspace', None):
        from .duplicate_navigation import create_selector
        self.kind_selector = create_selector(left, self.window.workspace)
    self.scope_buttons = [radio(left, text, self.scan_mode, value, self.switch_mode)
                          for value, text in [('single', '单文件夹'), ('multiple', '多文件夹联合')]]
    holder = frame(left)
    holder.pack(fill='x', pady=8)
    holder.columnconfigure(0, weight=1)
    self.single_panel = frame(holder)
    self.single_panel.grid(row=1, column=0, sticky='ew')
    self.folder_entry, self.choose_button = path_field(self.single_panel, '扫描目录 · 包含子目录', self.folder, self.choose)
    self.multiple_panel = frame(holder)
    caption(self.multiple_panel, '联合比较这些目录')
    box = ctk.CTkFrame(self.multiple_panel, fg_color=FIELD, corner_radius=10)
    box.pack(fill='x', pady=6)
    self.folder_list = tk.Listbox(box, height=5, selectmode='extended', exportselection=False, bg=FIELD, fg=TEXT,
                                 highlightthickness=0, relief='flat', selectbackground='#30483d', font=(FONT, 9))
    self.folder_list.pack(fill='x', padx=8, pady=8)
    scroll = ctk.CTkScrollbar(box, orientation='horizontal', command=self.folder_list.xview, height=12)
    scroll.pack(fill='x', padx=8)
    self.folder_list.configure(xscrollcommand=scroll.set)
    self.add_button = button(self.multiple_panel, '添加文件夹', self.add_folder)
    self.remove_button = button(self.multiple_panel, '移出选中目录', self.remove_folders)
    self.clear_button = button(self.multiple_panel, '清空列表', self.clear_folders)
    for control in (self.add_button, self.remove_button, self.clear_button):
        control.pack(fill='x', pady=4)
    if self.is_video:
        section(left, '02', '视频查重条件')
        for key, text in [('duration_seconds', '时长容差 · 秒（0～60）'),
                          ('duration_percent', '时长容差 · %（0～10）'),
                          ('name_threshold', '名称相似度 · %（50～100）')]:
            row = frame(left)
            row.pack(fill='x', pady=4)
            label(row, text, size=11, color=MUTED).pack(side='left')
            control = entry(row, self.video_options[key], width=64)
            control.pack(side='right')
            self.option_controls.append(control)
        hint(left, '时长容差取秒数与百分比的较大值。先筛时长、再比名称，最后核对编码、画面、帧率与大小。')
    section(left, '03' if self.is_video else '02', '报告与整理')
    self.load_button = button(left, '读取查重报告', self.import_report)
    self.load_button.pack(fill='x', pady=5)
    self.export_button = button(left, '导出全部结果 TXT', self.export, state='disabled')
    self.export_button.pack(fill='x', pady=5)
    self.organize_button = button(left, '按参考方目录整理', self.open_organize, state='disabled')
    self.organize_button.pack(fill='x', pady=5)
    hint(left, '扫描不改动文件。只有完全重复组可以整理；参考方保持原样，重名不覆盖。')
    self.start_button = button(self.layout.bottom, '开始查重', self.start, accent=True)
    self.start_button.pack(fill='x')
    self.stop_button = button(self.layout.bottom, '停止', self.stop, state='disabled')
    self.stop_button.pack(fill='x', pady=(8, 0))
    combo(self.layout.toolbar, self.mode, list(self.MODES), width=235, command=lambda _: self.populate()).pack(side='right')
    text_box(self.layout.footer, self.detail, 60).pack(fill='x', pady=8)
    row = frame(self.layout.footer)
    row.pack(fill='x')
    button(row, '打开所在文件夹', self.open_folder, width=138).pack(side='left')
    button(row, '复制完整路径', self.copy_path, width=120).pack(side='left', padx=8)
    columns = [('#0', '分组 / 文件名', 275), ('size', '字节大小', 110)]
    if self.is_video:
        columns += [('duration', '视频时长', 115), ('actions', '名称 / 内容依据', 130)]
    else:
        columns += [('actions', '动作点数', 110)]
    self.tree = table(self.layout.result, [*columns, ('path', '完整路径 / 提示', 450)], tree=True)
    self.tree.bind('<<TreeviewSelect>>', self.select)
    self.tree.tag_configure('group', foreground=ACCENT)
    self.progress = self.layout.progress


def build_organize(self, roots):
    self.layout = Workbench(self.window, '目录整理预览', self.status, ('待处理项目', '已勾选', '操作状态'))
    left = self.layout.settings
    section(left, '01', '目录结构')
    self.inputs = []
    for title, var in [('移动方根目录', self.source), ('参考方根目录 · 保持原样', self.reference), ('专用输出目录', self.output)]:
        self.inputs.extend(path_field(left, title, var, lambda v=var: self.choose(v)))
    hint(left, '按参考文件所在的相对目录建立文件夹，保留移动方文件名。移动前再次检查内容指纹。')
    section(left, '02', '参考位置')
    hint(left, '一个文件有多个参考位置时，选中右侧条目，再选择参考文件。')
    self.reference_combo = combo(left, self.reference_choice, [], width=254)
    self.reference_combo.pack(fill='x')
    self.apply_button = button(left, '应用参考位置', self.apply_choice)
    self.apply_button.pack(fill='x', pady=8)
    self.preview_button = button(self.layout.bottom, '生成整理预览', self.preview, accent=True)
    self.preview_button.pack(fill='x')
    self.stop_button = button(self.layout.bottom, '停止后续操作', lambda: self.cancel.set(), state='disabled')
    self.stop_button.pack(fill='x', pady=(8, 0))
    self.all_button = button(self.layout.toolbar, '全选可处理项', lambda: self.select_all(True), width=116)
    self.none_button = button(self.layout.toolbar, '全不选', lambda: self.select_all(False), width=80)
    self.none_button.pack(side='right')
    self.all_button.pack(side='right', padx=8)
    self.move_button = button(self.layout.footer, '确认移动勾选项', self.move, accent=True, width=160, state='disabled')
    self.move_button.pack(side='right', pady=8)
    self.tree = table(self.layout.result, [('checked', '勾选', 52), ('source', '移动的源'+self.media, 270),
        ('reference', '参考'+self.media, 250), ('target', '将移动到', 270), ('state', '状态', 220)])
    self.tree.bind('<Button-1>', self.click)
    self.tree.bind('<Double-1>', self.click)
    self.tree.bind('<<TreeviewSelect>>', self.select_row)


def build_playback(self):
    self.layout = Workbench(self.window, '视频与同步曲线', self.status)
    left = self.layout.settings
    section(left, '01', '核对素材')
    for title, var in [('视频', self.video_path), ('Funscript', self.script_path)]:
        path_field(left, title, var, lambda v=var: self.choose(v))
    button(left, '载入素材', self.load).pack(fill='x', pady=8)
    section(left, '02', '播放与同步')
    caption(left, '播放倍速')
    combo(left, self.rate, ['0.25', '0.5', '0.75', '1.0', '1.5', '2.0'], width=230,
          command=lambda _: self.command('set_property', 'speed', float(self.rate.get()))).pack(fill='x')
    caption(left, '脚本偏移秒数 · 正值延后')
    control = entry(left, self.offset)
    control.pack(fill='x')
    control.bind('<Return>', lambda _: self.draw())
    hint(left, '曲线跟随视频实际播放进度。点击曲线或热力图可跳转；偏移仅用于预览。')
    button(self.layout.bottom, '采用当前对应', self.confirm, accent=True).pack(fill='x')
    label(self.layout.toolbar, textvariable=self.clock, size=12, color=ACCENT).pack(side='right')
    body = frame(self.layout.result)
    body.pack(fill='both', expand=True, padx=14, pady=(0, 14))
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    screen = ctk.CTkFrame(body, fg_color=FIELD, corner_radius=14)
    screen.grid(row=0, column=0, sticky='nsew')
    self.video_area = tk.Frame(screen, background='black')
    self.video_area.pack(fill='both', expand=True, padx=7, pady=7)
    self.slider = ctk.CTkSlider(body, from_=0, to=1, progress_color='#72a88b', button_color=ACCENT,
                                button_hover_color='#a3dabc', fg_color=BORDER, command=self.seek_to)
    self.slider.grid(row=1, column=0, sticky='ew', pady=12)
    self.slider.bind('<ButtonPress-1>', lambda _: setattr(self, 'dragging', True))
    self.slider.bind('<ButtonRelease-1>', self.slider_release)
    controls = frame(body)
    controls.grid(row=2, column=0, sticky='ew', pady=(0, 10))
    button(controls, '播放 / 暂停', self.toggle, width=120).pack(side='left')
    button(controls, '← 5 秒', lambda: self.jump(-5), width=80).pack(side='left', padx=8)
    button(controls, '5 秒 →', lambda: self.jump(5), width=80).pack(side='left')
    curve_card = ctk.CTkFrame(body, fg_color=FIELD, corner_radius=12)
    curve_card.grid(row=3, column=0, sticky='ew')
    self.curve = tk.Canvas(curve_card, height=155, background=FIELD, highlightthickness=0)
    self.curve.pack(fill='both', expand=True, padx=8, pady=(8, 0))
    self.heat = tk.Canvas(curve_card, height=42, background=FIELD, highlightthickness=0)
    self.heat.pack(fill='x', padx=8, pady=(0, 8))
    self.curve.bind('<Configure>', lambda _: self.draw())
    self.curve.bind('<Button-1>', self.seek_curve)
    self.heat.bind('<Configure>', lambda _: self.draw_heat())
    self.heat.bind('<Button-1>', self.seek_heat)
    # Keep playback controls beside the video while the plot height is adjustable.
    self.playback_split = SplitPane(body, '播放核对/画面曲线', 'vertical')
    video_group = tk.Frame(self.playback_split, bg=PANEL)
    video_group.columnconfigure(0, weight=1); video_group.rowconfigure(0, weight=1)
    for widget in (screen, self.slider, controls):
        info = widget.grid_info(); info.pop('in', None)
        widget.grid_forget(); widget.grid(in_=video_group, **info); widget.lift()
    self.playback_split.add_panel(video_group, '画面', None, 160)
    self.playback_split.add_panel(curve_card, '曲线', 215, 100)
    self.playback_split.grid(row=0, column=0, rowspan=4, sticky='nsew')
