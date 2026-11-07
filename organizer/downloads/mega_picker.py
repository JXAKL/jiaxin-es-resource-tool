"""Per-share password and download selection. Replies are sent in memory."""
import tkinter as tk
from tkinter import ttk
from ..ui import PANEL, TEXT, label, entry, button


def size_text(n):
    for unit in ('B','KiB','MiB','GiB'):
        if n < 1024 or unit=='GiB':return f'{n:.1f} {unit}' if unit!='B' else f'{n} B'
        n /= 1024


class MegaPrompt(tk.Toplevel):
    def __init__(self,parent,event,title,reply):
        super().__init__(parent)
        self.configure(bg=PANEL)
        self.transient(parent.winfo_toplevel())
        self.reply=reply
        self.protocol('WM_DELETE_WINDOW',lambda:self.finish(None))
        self.bind('<Escape>',lambda _e:self.finish(None))
        files=event['kind']=='files'
        heading='选择要下载的文件' if files else ('输入分享密码' if event['kind']=='password' else '输入分享解密密钥')
        self.title('MEGA · '+heading)
        self.geometry('640x460' if files else '480x245')
        self.minsize(380,240)
        label(self,heading,size=17,bold=True).pack(anchor='w',padx=16,pady=(12,2))
        label(self,title or event['url'],size=11,wraplength=450,justify='left').pack(anchor='w',padx=16)
        bar=tk.Frame(self,bg=PANEL)
        bar.pack(side='bottom',fill='x',padx=16,pady=10)
        button(bar,'跳过此链接',lambda:self.finish(None)).pack(side='right')
        if not files:
            self.value=tk.StringVar(self)
            field=entry(self,self.value)
            field.configure(show='●');field.pack(fill='x',padx=16,pady=8)
            label(self,event.get('message') or '仅用于本次解锁。',size=12).pack(anchor='w',padx=16)
            def submit(*_):
                if self.value.get().strip():self.finish(self.value.get())
            field.bind('<Return>',submit)
            button(bar,'解锁',submit,accent=True).pack(side='right',padx=6)
            field.focus_set()
            return
        self.files={f['id']:f for f in event['files']}
        self.selected=set(event.get('selected',[])) & self.files.keys()
        self.paths={}
        self.members={}
        self.names={}
        self.count=tk.StringVar(self)
        tk.Label(self,textvariable=self.count,bg=PANEL,fg=TEXT,anchor='w').pack(fill='x',padx=16,pady=4)
        body=tk.Frame(self,bg=PANEL);body.pack(fill='both',expand=True,padx=16)
        self.tree=ttk.Treeview(body,columns=('size',),show='tree headings',selectmode='browse')
        self.tree.heading('#0',text='文件夹 / 文件名');self.tree.heading('size',text='大小')
        self.tree.column('size',width=94,stretch=False,anchor='e')
        scroll=ttk.Scrollbar(body,command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.tree.pack(fill='both',expand=True)
        for f in sorted(self.files.values(),key=lambda f:(f['dirs'],f['name'].casefold())):
            parts=();parent_id=''
            for part in f['dirs']:
                parts=(*parts,part)
                if parts not in self.paths:
                    folder=self.tree.insert(parent_id,'end',open=len(parts)<=1)
                    self.paths[parts]=folder;self.names[folder]=part;self.members[folder]=set()
                parent_id=self.paths[parts];self.members[parent_id].add(f['id'])
            self.tree.insert(parent_id,'end',iid=f['id'],values=(size_text(f['size']),))
            self.names[f['id']]=f['name']
        def click(e):
            iid=self.tree.identify_row(e.y)
            if iid and 'indicator' not in self.tree.identify_element(e.x,e.y):self.toggle(iid)
        self.tree.bind('<ButtonRelease-1>',click)
        self.tree.bind('<space>',lambda _:self.toggle(self.tree.focus()))
        button(bar,'全选',lambda:self.set_all(True),width=60).pack(side='left')
        button(bar,'清空',lambda:self.set_all(False),width=60).pack(side='left',padx=4)
        self.confirm=button(bar,'下载勾选项',lambda:self.finish(sorted(self.selected)),accent=True)
        self.confirm.pack(side='right',padx=6)
        self.refresh(self.names)

    def refresh(self,rows):
        for iid in rows:
            group=self.members.get(iid)
            if group:
                marker='☑' if group<=self.selected else '◩' if not group.isdisjoint(self.selected) else '☐'
            else: marker='☑' if iid in self.selected else '☐'
            self.tree.item(iid,text=marker+'  '+self.names[iid])
        self.count.set(f'已选 {len(self.selected)} / {len(self.files)} 个文件 · '+size_text(sum(self.files[i]['size'] for i in self.selected)))
        self.confirm.configure(state='normal' if self.selected else 'disabled')

    def toggle(self,iid):
        if not iid:return
        ids=self.members.get(iid,{iid})
        if ids<=self.selected:self.selected-=ids
        else:self.selected.update(ids)
        self.refresh(ids | {folder for folder,members in self.members.items() if not members.isdisjoint(ids)})

    def set_all(self,on):
        self.selected=set(self.files) if on else set()
        self.refresh(self.names)

    def finish(self,value):
        if hasattr(self,'value'):self.value.set('')
        self.destroy()
        self.reply(value)
