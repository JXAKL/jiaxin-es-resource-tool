"""Single-window navigation; engines retain their own state and file rules."""
import json
import tkinter as tk
import customtkinter as ctk
from PIL import Image
from .runtime import APP_NAME, APP_VERSION, BUNDLE, STATE
from .ui import BG, PANEL, TEXT, MUTED, ACCENT, button, label, entry, decorate_window
from .presentation import flat_frame


class Surface(tk.Frame):
    """A frame exposing the window protocol expected by existing controllers."""
    embedded = True

    def __init__(self, workspace, parent_surface=None):
        super().__init__(workspace.content, background=BG)
        self.workspace, self.parent_surface = workspace, parent_surface
        self.controller = self.close_handler = None
        self.caption = ''
        self.jobs = set()
        self.destroyed = False
        self.active = False
        workspace.activate_surface(self)

    def title(self, value=None):
        if value is not None:
            self.caption = value
        return self.caption

    def geometry(self, value=None):
        return self.winfo_toplevel().geometry()

    def minsize(self, *args):
        pass

    def transient(self, *args):
        pass

    def grab_set(self):
        pass

    def protocol(self, name, callback):
        if name == 'WM_DELETE_WINDOW':
            self.close_handler = callback
            self.controller = getattr(callback, '__self__', None)
            self.workspace.refresh_manual()

    def after(self, ms, func=None, *args):
        if func is None:
            return super().after(ms)
        job = None
        def invoke():
            self.jobs.discard(job)
            if not self.destroyed:
                func(*args)
        job = super().after(ms, invoke)
        self.jobs.add(job)
        return job

    def after_cancel(self, job):
        self.jobs.discard(job)
        return super().after_cancel(job)

    def destroy(self):
        if self.destroyed:
            return
        self.destroyed = True
        for job in tuple(self.jobs):
            self.after_cancel(job)
        super().destroy()
        self.workspace.surface_closed(self)


class Workspace:
    def __init__(self, root):
        self.root, self.pages, self.overlays = root, {}, []
        root._es_layout_path = STATE / 'pane-layout.json'
        self.current = 'author'
        self.duplicate_selection = tk.StringVar(master=root, value='Funscript')
        self.displayed = None
        self.closing = self.close_waiting = False
        self.manual_panel = None
        from . import display_settings
        root._es_display = display_settings.load(STATE / 'ui-settings.json')
        root.title(APP_NAME)
        root.geometry('1380x940')
        root.minsize(1180, 820)
        root.configure(background=BG)
        ctk.set_appearance_mode('dark')
        ctk.deactivate_automatic_dpi_awareness()
        header = flat_frame(root)
        header.pack(fill='x', padx=22, pady=(14, 12))
        with Image.open(BUNDLE / 'assets' / 'jiaxin.jpg') as picture:
            self.logo = ctk.CTkImage(picture.copy(), size=(38, 38))
        ctk.CTkLabel(header, text='', image=self.logo, width=38).pack(side='left', padx=(0, 10))
        label(header, APP_NAME, size=18, bold=True).pack(side='left')
        label(header, '  ' + APP_VERSION, size=11, color=MUTED).pack(side='left', padx=(0, 6))
        self.nav = {}
        navigation = header
        for key, text in [('author', '作者归类'), ('pair', '本地配对'), ('duplicates', '重复整理'), ('collector', '链接采集'), ('search_scraper', '搜索刮削'), ('completion', '资源补齐'), ('resource_organize', '资源综合整理'), ('downloads', '资源下载')]:
            control = button(navigation, text, lambda k=key: self.show(k), width=102 if key == 'resource_organize' else 78, quiet=True)
            control.pack(side='left', padx=3)
            self.nav[key] = control
        self.back = button(navigation, '返回', self.go_back, width=58, quiet=True)
        self.back.pack(side='right')
        self.status = tk.StringVar(value='')
        footer = flat_frame(root)
        footer.pack(side='bottom', fill='x', padx=26, pady=6)
        self.manual_button = button(footer, '使用说明', self.toggle_manual, width=88, quiet=True)
        self.manual_button.pack(side='right', padx=(6, 0))
        button(footer, '界面设置', self.toggle_display_settings, width=88, quiet=True).pack(side='right')
        button(footer, '重置分区', self.reset_panes, width=88, quiet=True).pack(side='right', padx=6)
        label(footer, textvariable=self.status, color=MUTED, size=11).pack(side='left', fill='x', expand=True)
        self.display_bar = flat_frame(root)
        self.display_values = {key: tk.StringVar(value=str(value)) for key, value in root._es_display.items()}
        self.content = tk.Frame(root, background=BG)
        self.content.pack(fill='both', expand=True, padx=4)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.show('author')
        from .local_tools.ui_theme import apply_theme
        apply_theme(self.pages['author'])
        decorate_window(root)
        display_settings.apply(root, self.active_surfaces())
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<F1>', lambda _: self.open_manual(), add='+')
        root.bind('<Escape>', self.dismiss_manual, add='+')

    def manual_context(self):
        controller = getattr(self.displayed, 'controller', None)
        module = type(controller).__module__
        if module.endswith('.playback_review'):
            return 'playback'
        if module.endswith('.funscript_duplicates.organize_ui'):
            return 'duplicate_organize'
        for key, surface in self.pages.items():
            if surface is self.displayed:
                return key
        return self.current

    def open_manual(self):
        if self.manual_panel is None:
            from .manual_ui import ManualPanel
            self.manual_panel = ManualPanel(self)
        self.manual_panel.show(self.manual_context())

    def toggle_manual(self):
        if self.manual_panel is not None and self.manual_panel.visible:
            self.manual_panel.hide()
        else:
            self.open_manual()

    def dismiss_manual(self, event=None):
        if self.manual_panel is not None and self.manual_panel.visible:
            self.manual_panel.hide()
            return 'break'

    def refresh_manual(self):
        if self.manual_panel is not None and self.manual_panel.visible:
            self.manual_panel.show(self.manual_context())

    def toggle_display_settings(self):
        if not self.display_bar.winfo_children():
            from .display_settings import FIELDS
            for key, (text, _, low, high) in FIELDS.items():
                cell = flat_frame(self.display_bar)
                cell.pack(side='left', padx=(0, 6))
                label(cell, f'{text} ({low}–{high})', size=11, color=MUTED).pack(anchor='w')
                entry(cell, self.display_values[key], width=130).pack(fill='x')
            button(self.display_bar, '应用并保存', self.apply_display_settings, width=108).pack(side='left', padx=4)
            button(self.display_bar, '恢复默认', self.reset_display_settings, width=88).pack(side='left', padx=4)
        if self.display_bar.winfo_manager():
            self.display_bar.pack_forget()
        else:
            self.display_bar.pack(side='bottom', fill='x', padx=26, pady=(0, 10), before=self.content)

    def reset_panes(self):
        from .split_layout import reset_all
        reset_all(self.root)
        self.status.set('已重置各页面分区。拖动分隔条调整大小；双击分隔条可恢复该区域。')

    def apply_display_settings(self):
        from . import display_settings
        try:
            values = display_settings.save(STATE / 'ui-settings.json', {k: v.get() for k, v in self.display_values.items()})
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
            return False
        self.root._es_display = values
        display_settings.apply(self.root, self.active_surfaces(), resize=True)
        self.status.set('界面设置已保存并应用；下次启动自动恢复。网络和匹配参数保持不变。')
        return True

    def reset_display_settings(self):
        from .display_settings import DEFAULTS
        for key, value in DEFAULTS.items():
            self.display_values[key].set(str(value))
        self.apply_display_settings()

    def active_surfaces(self):
        return list(self.pages.values()) + self.overlays

    def activate_surface(self, surface):
        previous = self.displayed
        if previous is not None and previous is not surface and not previous.destroyed:
            previous.active = False
            previous.grid_remove()
        surface.active = True
        surface.grid(row=0, column=0, sticky='nsew')
        self.displayed = surface
        surface.tkraise()
        self.refresh_manual()

    def busy(self):
        for surface in self.active_surfaces():
            controller = surface.controller
            state = getattr(controller, 'busy', False)
            if (state() if callable(state) else state) or getattr(controller, 'running', False):
                return True
        return False

    def sync_duplicate_selection(self):
        from .duplicate_navigation import PAGES
        key = self.current if self.current in PAGES.values() else getattr(self, 'duplicate_kind', 'scripts')
        self.duplicate_selection.set(next(label for label, page in PAGES.items() if page == key))

    def select_duplicate(self, value):
        from .duplicate_navigation import PAGES
        try:
            return self.show(PAGES[value])
        finally:
            # A busy task can reject navigation after the widget selected a tab.
            self.sync_duplicate_selection()

    def show(self, key):
        if self.close_waiting or self.busy():
            self.status.set('当前任务正在运行，请等待完成或先停止，再切换工作区。')
            return False
        while self.overlays:
            self.go_back()
        if key == 'duplicates':
            key = getattr(self, 'duplicate_kind', 'scripts')
        if key not in self.pages:
            surface = Surface(self)
            self.pages[key] = surface
            try:
                if key == 'author':
                    from app import App
                    self.author = App(surface)
                elif key == 'pair':
                    from .local_tools.smart_match import SmartMatcherApp
                    self.pair = SmartMatcherApp(surface)
                    self.load_pair_settings()
                elif key == 'collector':
                    from .collector_ui import CollectorPage
                    self.collector = CollectorPage(surface, self.author, STATE)
                elif key == 'search_scraper':
                    from .search_scraper_ui import SearchScraperPage
                    self.search_scraper = SearchScraperPage(surface, self.author, STATE)
                elif key == 'completion':
                    from .completion_ui import CompletionPage
                    self.completion = CompletionPage(surface, self.author, STATE)
                elif key == 'resource_organize':
                    from .resource_organize_ui import ResourceOrganizePage
                    self.resource_organize = ResourceOrganizePage(surface, self.author, STATE)
                elif key == 'downloads':
                    from .downloads.ui import DownloadPage
                    self.downloads = DownloadPage(surface, STATE)
                elif key == 'folders':
                    from .folder_groups_ui import FolderGroupsPage
                    self.folders = FolderGroupsPage(surface, STATE)
                else:
                    from .local_tools.funscript_duplicates.ui import DuplicateWindow
                    from .local_tools.video_duplicates import backend
                    surface.make_inline = True
                    DuplicateWindow(surface, backend=backend if key == 'videos' else None, state=STATE)
                    if hasattr(self, 'pair'):
                        surface.controller.folder.set(self.pair.video_dir.get() if key == 'videos' else self.pair.funscript_dir.get())
            except Exception:
                surface.destroy()
                self.pages.pop(key, None)
                raise
        self.current = key
        from .presentation import defer_scrollbar_redraws
        defer_scrollbar_redraws(self.pages[key])
        from .display_settings import apply
        apply(self.root, [self.pages[key]])
        if key in ('scripts', 'videos', 'folders'):
            self.duplicate_kind = key
            self.sync_duplicate_selection()
        self.activate_surface(self.pages[key])
        for name, control in self.nav.items():
            selected = name == key or name == 'duplicates' and key in ('scripts', 'videos', 'folders')
            control.configure(fg_color=ACCENT if selected else 'transparent', text_color='#15241c' if selected else MUTED,
                              hover_color='#a3dabc' if selected else '#272a2f', border_width=0)
        self.back.configure(state='disabled')
        self.status.set({'author': '按作者归类 · 使用原有网站索引、匹配规则与移动撤销记录',
                         'pair': '本地配对 · 默认复制；核对与强度分类均在本机完成',
                         'scripts': '重复整理 · 脚本全数据查重，另列仅元数据不同的组',
                         'videos': '重复整理 · 先比时长、名称和文件信息，另列完整文件重复项',
                         'folders': '文件夹组数 · 按一级子文件夹统计，低于阈值的整文件夹移动，可撤销',
                         'collector': '链接采集 · 共用登录与索引，按作者首帖导出，网络异常自动重试',
                         'search_scraper': '搜索刮削 · 多标签搜索与排除、可选包含付费帖子，按 ES 发帖作者汇总',
                         'completion': '资源补齐 · 递归检查缺失组，按作者帖子生成带付费依据的链接报告',
                         'resource_organize': '资源综合整理 · 多目录独立设置，按作者帖子日期预览并复制／移动，保留完整报告',
                         'downloads': '资源下载 · 混合链接队列，独立站点会话，保留断点与失败报告'}[key])
        return True

    def queue_downloads(self, urls):
        if self.show('downloads'):
            return self.downloads.add_urls(urls)
        return 0

    def queue_resources(self, items, ignored=0):
        if self.show('downloads'):
            return self.downloads.add_items(items, ignored=ignored)
        return 0

    def push_surface(self, parent):
        if getattr(parent, 'make_inline', False):
            parent.make_inline = False
            return parent
        surface = Surface(self, parent)
        self.overlays.append(surface)
        self.activate_surface(surface)
        self.back.configure(state='normal')
        self.status.set('核对和设置在当前窗口展开；完成后点击“返回”。')
        return surface

    def surface_closed(self, surface):
        if surface in self.overlays:
            self.overlays.remove(surface)
        if not self.closing and surface.parent_surface and not surface.parent_surface.destroyed:
            self.activate_surface(surface.parent_surface)
            self.back.configure(state='normal' if self.overlays else 'disabled')

    def go_back(self):
        if not self.overlays:
            return
        surface = self.overlays[-1]
        if getattr(surface.controller, 'busy', False):
            self.status.set('请先停止当前分析或整理任务，完成后可返回。')
            return
        if surface.close_handler:
            surface.close_handler()
        else:
            surface.destroy()

    @staticmethod
    def pair_setting_keys():
        # Mode/overwrite/direct processing reset; group options are safe to retain in copy mode.
        return ('video_dir', 'funscript_dir', 'output_dir', 'output_csv', 'high_threshold',
                'low_threshold', 'use_name', 'use_duration', 'classify_intensity', 'auto_select',
                'include_scripts', 'separate_folders')

    def load_pair_settings(self):
        try:
            data = json.loads((STATE / 'local-settings.json').read_text(encoding='utf-8'))
            for key in self.pair_setting_keys():
                if key in data:
                    getattr(self.pair, key).set(data[key])
        except (OSError, ValueError, tk.TclError):
            pass

    def save(self):
        self.author._save()
        if 'videos' in self.pages:
            self.pages['videos'].controller.save()
        if hasattr(self, 'collector'):
            self.collector.save()
        if hasattr(self, 'search_scraper'):
            self.search_scraper.save()
        if hasattr(self, 'completion'):
            self.completion.save()
        if hasattr(self, 'resource_organize'):
            self.resource_organize.save()
        if hasattr(self, 'downloads'):
            self.downloads.save()
        if hasattr(self, 'folders'):
            self.folders.save()
        if hasattr(self, 'pair'):
            data = {name: getattr(self.pair, name).get() for name in self.pair_setting_keys()}
            STATE.mkdir(parents=True, exist_ok=True)
            target = STATE / 'local-settings.json'
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(target)

    def close(self):
        if self.busy():
            self.close_waiting = True
            self.status.set('正在等待文件任务安全结束；支持停止的任务已发送停止请求。')
            for surface in self.active_surfaces():
                controller = surface.controller
                cancel = getattr(controller, 'cancel', None) or getattr(controller, 'stop_event', None)
                if cancel is not None:
                    cancel.set()
                stop = getattr(controller, 'stop', None)
                if callable(stop):
                    stop()
            self.root.after(200, self.close)
            return
        self.save()
        self.closing = True
        while self.overlays:
            self.go_back()
        for surface in self.pages.values():
            cancel = getattr(surface.controller, 'cancel', None)
            if cancel is not None:
                cancel.set()
        self.root.destroy()
