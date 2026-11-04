"""Contextual offline reading panel; does not activate or interrupt a task surface."""
import tkinter as tk
import customtkinter as ctk

from .manual_content import manual
from .presentation import flat_frame
from .runtime import APP_VERSION
from .ui import PANEL, FIELD, BORDER, TEXT, MUTED, ACCENT, FONT, button, label, entry, combo


class ManualPanel:
    def __init__(self, workspace):
        self.workspace = workspace
        self.visible = False
        self.key = None
        self.positions = {}
        self.frame = ctk.CTkFrame(workspace.content, fg_color=PANEL, corner_radius=18,
                                 border_width=1, border_color=BORDER, width=780)
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_rowconfigure(3, weight=1)
        head = flat_frame(self.frame)
        head.grid(row=0, column=0, sticky='ew', padx=20, pady=(18, 6))
        head.columnconfigure(0, weight=1)
        self.title = label(head, size=18, bold=True)
        self.title.grid(row=0, column=0, sticky='w')
        self.close_button = button(head, '关闭说明', self.hide, width=88)
        self.close_button.grid(row=0, column=1, padx=(10, 0))
        self.section = tk.StringVar(master=workspace.root)
        self.query = tk.StringVar(master=workspace.root)
        self.note = tk.StringVar(master=workspace.root)
        tools = flat_frame(self.frame)
        tools.grid(row=1, column=0, sticky='ew', padx=20, pady=7)
        tools.columnconfigure(0, weight=1)
        self.toc = combo(tools, self.section, [], command=self.jump)
        self.toc.grid(row=0, column=0, sticky='ew')
        search = flat_frame(self.frame)
        search.grid(row=2, column=0, sticky='ew', padx=20, pady=(0, 10))
        search.columnconfigure(1, weight=1)
        label(search, '本页查找', size=12, color=MUTED).grid(row=0, column=0, padx=(0, 8))
        self.search = entry(search, self.query, width=120)
        self.search.grid(row=0, column=1, sticky='ew')
        self.find_button = button(search, '查找下一处', self.find_next, width=102)
        self.find_button.grid(row=0, column=2, padx=(8, 0))
        self.search.bind('<Return>', lambda _: self.find_next())
        self.query.trace_add('write', self.clear_search)
        self.text = ctk.CTkTextbox(self.frame, fg_color=FIELD, text_color=TEXT, corner_radius=12,
                                   font=(FONT, 13), wrap='word', border_width=0)
        self.text.grid(row=3, column=0, sticky='nsew', padx=16, pady=(0, 8))
        self.document = self.text._textbox
        self.document.configure(padx=16, pady=12, spacing1=3, spacing3=8)
        self.document.tag_configure('heading', foreground=ACCENT, font=(FONT, 14, 'bold'),
                                    spacing1=18, spacing3=10)
        self.document.tag_configure('found', background='#435e4e', foreground='#ffffff')
        self.document.tag_configure('current', background=ACCENT, foreground='#15241c')
        self.document.configure(state='disabled')
        bottom = flat_frame(self.frame)
        bottom.grid(row=4, column=0, sticky='ew', padx=20, pady=(0, 14))
        label(bottom, textvariable=self.note, size=11, color=MUTED).pack(side='left')
        label(bottom, f'内置说明 · {APP_VERSION}', size=11, color=MUTED).pack(side='right')
        self.matches = []
        self.match_index = -1
        self.search_term = ''
        workspace.content.bind('<Configure>', self.resize, add='+')

    def resize(self, event=None):
        if event is not None and event.widget is not self.workspace.content:
            return
        if self.visible:
            width = min(820, max(460, self.workspace.content.winfo_width()-48))
            if self.frame.cget('width') != width:
                self.frame.configure(width=width)

    def show(self, key):
        if self.key != key:
            if self.key is not None:
                self.positions[self.key] = self.document.yview()[0]
            title, sections = manual(key)
            self.key = key
            self.title.configure(text=title + ' · 使用说明')
            self.query.set('')
            self.document.configure(state='normal')
            self.document.delete('1.0', 'end')
            self.anchors = {}
            for n, (heading, body) in enumerate(sections, 1):
                index = self.document.index('end-1c')
                self.anchors[f'{n:02d}  {heading}'] = index
                self.document.insert('end', heading+'\n', 'heading')
                self.document.insert('end', body.strip()+'\n\n')
            self.document.configure(state='disabled')
            self.toc.configure(values=list(self.anchors))
            self.section.set(next(iter(self.anchors)))
            self.document.yview_moveto(self.positions.get(key, 0))
            self.note.set('仅显示当前模块 · 可选中文字复制')
        self.visible = True
        self.resize()
        self.frame.place(relx=1, rely=.02, relheight=.96, x=-16, anchor='ne')
        self.frame.lift()

    def hide(self):
        self.visible = False
        self.frame.place_forget()

    def jump(self, title):
        if title in self.anchors:
            self.document.yview(self.anchors[title])

    def clear_search(self, *_):
        self.matches = []
        self.match_index = -1
        self.search_term = ''
        self.document.tag_remove('found', '1.0', 'end')
        self.document.tag_remove('current', '1.0', 'end')
        self.note.set('输入文字后按回车或点击查找')

    def find_next(self):
        term = self.query.get().strip()
        if not term:
            self.note.set('请输入要在本模块查找的文字')
            return
        if term != self.search_term:
            self.clear_search()
            self.search_term = term
            start = '1.0'
            count = tk.IntVar(master=self.frame)
            while True:
                found = self.document.search(term, start, stopindex='end', nocase=True, count=count)
                if not found:
                    break
                end = self.document.index(f'{found}+{count.get()}c')
                self.matches.append((found, end))
                self.document.tag_add('found', found, end)
                start = end
        if not self.matches:
            self.note.set('当前模块未找到该文字')
            return
        self.match_index = (self.match_index+1) % len(self.matches)
        self.document.tag_remove('current', '1.0', 'end')
        first, last = self.matches[self.match_index]
        self.document.tag_add('current', first, last)
        self.document.tag_raise('current')
        self.document.see(first)
        self.note.set(f'第 {self.match_index+1} / {len(self.matches)} 处')
