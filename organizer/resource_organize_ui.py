"""One shared workbench for multi-source author/date organization."""
import copy
import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from .panels import *
from .collector import atomic_text
from .forum import BrowserClient, Cancelled
from .runtime import LEGACY
from .store import Store
from .presentation import paint_items, poll_delay
from .resource_organize import (Source, OrganizeOptions, MODE_LABELS, PAIR_LABELS, DATE_LABELS,
                               prepare, execute, choose, mark_collisions)
from .resource_organize_report import export_report
from .resource_source_links import links_for


class ResourceOrganizePage:
    def __init__(self, surface, author_app, state_dir):
        self.window,self.author_app,self.state_dir=surface,author_app,Path(state_dir)
        self.busy=False
        self.cancel,self.resume,self.events=threading.Event(),threading.Event(),queue.Queue()
        self.sources=[];self.rows={};self.last_state=None;self.current_output=None
        self.author=tk.StringVar(value=author_app.seed.get().split(',')[0].strip())
        self.output=tk.StringVar(value=str(self.state_dir.parent/'资源综合整理输出'))
        self.threshold=tk.StringVar(value='85')
        self.date_policy=tk.StringVar(value=DATE_LABELS['review'])
        self.source_mode=tk.StringVar(value=MODE_LABELS['both'])
        self.source_pairing=tk.StringVar(value=PAIR_LABELS['missing'])
        self.partner_choice=tk.StringVar(value='未选择配套目录')
        self.action=tk.StringVar(value='复制')
        self.refresh=tk.BooleanVar(value=False)
        self.candidate=tk.StringVar(value='先选结果查看候选')
        self.status=tk.StringVar(value='逐个添加来源目录并设置类型；采集后预览作者/年/月，再复制或移动。')
        self.detail=tk.StringVar(value='双击结果或按空格切换勾选；单击查看日期、付费信息、名称证据及资源链接。')
        self._load();self.build()
        surface.protocol('WM_DELETE_WINDOW',self.close)
        surface.after(100,self.poll)

    def _load(self):
        try:
            data=json.loads((self.state_dir/'resource-organize-settings.json').read_text(encoding='utf-8'))
            for key in ('author','output','threshold','date_policy'):
                if isinstance(data.get(key),str):getattr(self,key).set(data[key])
            self.sources=[dict(path=s['path'],mode=s['mode'],pairing=s['pairing'],
                               partner=s.get('partner','') if isinstance(s.get('partner',''),str) else '') for s in data.get('sources',[])
                          if isinstance(s,dict) and isinstance(s.get('path'),str) and s.get('mode') in MODE_LABELS and s.get('pairing') in PAIR_LABELS]
            try:links_for([Source(**s) for s in self.sources])
            except ValueError:
                for s in self.sources:s['partner']=''
                self.status.set('保存的关联设置不完整，已保留目录；请重新关联。')
        except (OSError,ValueError,KeyError,TypeError):pass

    def save(self):
        atomic_text(self.state_dir/'resource-organize-settings.json',json.dumps(dict(
            sources=self.sources,**{k:getattr(self,k).get() for k in ('author','output','threshold','date_policy')}),ensure_ascii=False))

    def build(self):
        self.layout=Workbench(self.window,'综合整理预览',self.status,('本地组数','已勾选','缺失另一半'))
        left=self.layout.settings
        section(left,'01','来源目录 · 包含子目录')
        self.source_tree=table(left,[('path','目录',190),('mode','类型',90),('pair','配套',130)])
        self.source_tree.configure(height=4)
        self.source_tree.bind('<<TreeviewSelect>>',self.select_source)
        line=frame(left);line.pack(fill='x',pady=5)
        self.add_button=button(line,'添加目录',self.add_source,width=90);self.add_button.pack(side='left')
        self.remove_button=button(line,'移除选中',self.remove_source,width=90);self.remove_button.pack(side='right')
        caption(left,'所选目录的文件类型')
        combo(left,self.source_mode,list(MODE_LABELS.values()),width=250).pack(fill='x')
        caption(left,'仅脚本／仅视频目录的配套情况')
        combo(left,self.source_pairing,list(PAIR_LABELS.values()),width=250).pack(fill='x')
        self.apply_button=button(left,'应用到选中目录',self.apply_source);self.apply_button.pack(fill='x',pady=6)
        caption(left,'关联配套 · 以列表选中目录为主')
        self.partner_control=combo(left,self.partner_choice,['未选择配套目录'],width=250)
        self.partner_control.pack(fill='x')
        self.link_button=button(left,'关联并合并显示',self.link_source)
        self.link_button.pack(fill='x',pady=6)
        link_row=frame(left);link_row.pack(fill='x')
        self.swap_button=button(link_row,'交换主从',self.swap_sources,width=90);self.swap_button.pack(side='left')
        self.unlink_button=button(link_row,'解除关联',self.unlink_source,width=90);self.unlink_button.pack(side='right')
        hint(left,'先添加两个单类型目录并设置类型，再选主目录关联另一侧。仅带入可靠配套的文件和全部轴脚本，辅助目录其余文件不纳入。关联后显示一行，可交换主从或解除。')
        section(left,'02','作者与输出')
        caption(left,'ES 作者主页或用户名');entry(left,self.author).pack(fill='x')
        path_field(left,'整理输出目录',self.output,self.choose_output)
        caption(left,'帖子匹配阈值 · 50–100');entry(left,self.threshold).pack(fill='x')
        caption(left,'帖子首次发布日期 · 北京时间')
        combo(left,self.date_policy,list(DATE_LABELS.values()),width=250).pack(fill='x')
        switch(left,'刷新作者索引与首帖',self.refresh)
        hint(left,'默认取综合最高的可靠帖；接近分数指向不同月份时待核对。也可指定最早／最新可靠帖。日期缺失不会猜测归档月份。')
        section(left,'03','共用匹配与网站设置')
        app=self.author_app
        caption(left,'本地计算进程 · 0 自动');entry(left,app.compute_workers).pack(fill='x')
        combo(left,app.browser_choice,['Chromium','Chrome','Edge'],width=250).pack(fill='x',pady=6)
        switch(left,'显示浏览器',app.visible)
        for title,var in [('请求间隔（秒）',app.request_interval),('每分钟请求上限 · 0 不限',app.per_minute)]:
            caption(left,title);entry(left,var).pack(fill='x')
        hint(left,'复用原采集的登录、限速、重试和网页回退；“本地索引预览”无需访问网站。整理不会自动下载或购买资源。')
        self.start_button=button(self.layout.bottom,'采集并生成预览',self.start,accent=True)
        self.start_button.pack(fill='x')
        line=frame(self.layout.bottom);line.pack(fill='x',pady=8)
        self.local_button=button(line,'本地索引预览',lambda:self.start(True),width=125);self.local_button.pack(side='left')
        self.stop_button=button(line,'停止',self.cancel.set,width=80,state='disabled');self.stop_button.pack(side='right')
        self.resume_button=button(self.layout.bottom,'已完成网站验证，继续',self.resume.set,state='disabled')
        self.resume_button.pack(fill='x')
        self.execute_button=button(self.layout.toolbar,'执行勾选项',self.execute_selected,width=100,accent=True)
        self.execute_button.pack(side='right')
        self.action_control=combo(self.layout.toolbar,self.action,['复制','移动'],width=80)
        self.action_control.pack(side='right',padx=6)
        self.report_button=button(self.layout.toolbar,'打开报告',self.open_report,width=90);self.report_button.pack(side='right')
        line=frame(self.layout.result);line.pack(fill='x',padx=16,pady=(0,8))
        self.select_button=button(line,'勾选可靠项',self.select_reliable,width=105);self.select_button.pack(side='left')
        self.clear_button=button(line,'取消全部勾选',self.clear_selection,width=112);self.clear_button.pack(side='left',padx=6)
        self.export_button=button(line,'更新报告',self.reexport,width=90);self.export_button.pack(side='right')
        candidate_line=frame(self.layout.footer);candidate_line.pack(fill='x',pady=(8,4))
        self.use_button=button(candidate_line,'采用此帖',self.use_candidate,width=90);self.use_button.pack(side='right',padx=(8,0))
        self.candidate_control=combo(candidate_line,self.candidate,['先选结果查看候选'],width=250)
        self.candidate_control.pack(side='left',fill='x',expand=True)
        text_box(self.layout.footer,self.detail,145).pack(fill='x',pady=5)
        self.tree=table(self.layout.result,[('selected','选择',45),('title','本地名称',225),('pair','配套情况',125),
            ('status','核对／执行状态',210),('month','归档年月',80),('score','匹配分',65),('payment','付费标签',85)])
        self.tree.bind('<<TreeviewSelect>>',self.select)
        self.tree.bind('<Double-1>',self.toggle);self.tree.bind('<space>',self.toggle)
        self.render_sources();self.controls()

    def render_sources(self):
        selection=self.source_tree.selection()
        self.source_tree.delete(*self.source_tree.get_children())
        links=links_for([Source(**s) for s in self.sources]);auxiliary=set(links.values())
        for i,s in enumerate(self.sources):
            if i in auxiliary:continue
            path=s['path']+(' ↔ '+self.sources[links[i]]['path'] if i in links else '')
            self.source_tree.insert('','end',iid=str(i),values=(path,MODE_LABELS[s['mode']]+('为主' if i in links else ''),
                '已关联配套' if i in links else PAIR_LABELS[s['pairing']] if s['mode']!='both' else '自动检查'))
        if selection and self.source_tree.exists(selection[0]):self.source_tree.selection_set(selection[0])
        self.refresh_partners()

    def add_source(self,path=None):
        if self.busy:return
        path=path or filedialog.askdirectory(parent=self.window)
        if not path:return
        resolved=str(Path(path).resolve())
        if any(os.path.normcase(s['path'])==os.path.normcase(resolved) for s in self.sources):
            self.status.set('目录已在列表中，可直接选中修改设置。');return
        self.sources.append(dict(path=resolved,mode=self.key(MODE_LABELS,self.source_mode.get()),
                                 pairing=self.key(PAIR_LABELS,self.source_pairing.get()),partner=''))
        self.render_sources();self.source_tree.selection_set(str(len(self.sources)-1));self.save()

    def select_source(self,*_):
        ids=self.source_tree.selection()
        if ids:
            s=self.sources[int(ids[0])];self.source_mode.set(MODE_LABELS[s['mode']]);self.source_pairing.set(PAIR_LABELS[s['pairing']])
        self.refresh_partners()

    def refresh_partners(self):
        ids=self.source_tree.selection();self.partner_map={}
        if ids:
            i=int(ids[0]);source=self.sources[i]
            links=links_for([Source(**s) for s in self.sources]);used=set(links)|set(links.values())
            for j,s in enumerate(self.sources):
                if j!=i and (j not in used or links.get(i)==j) and {source['mode'],s['mode']}=={'scripts','videos'}:
                    self.partner_map[f'{j+1} · {s["path"]}']=j
        values=list(self.partner_map) or ['没有可关联的另一类型目录']
        self.partner_control.configure(values=values)
        current=self.sources[int(ids[0])].get('partner','') if ids else ''
        self.partner_choice.set(next((key for key,j in self.partner_map.items() if self.sources[j]['path']==current),values[0]))

    def link_source(self):
        if self.busy:return
        ids=self.source_tree.selection();j=self.partner_map.get(self.partner_choice.get())
        if not ids or j is None:
            self.status.set('先添加并选中单类型主目录，再选择相反类型的配套目录。');return
        i=int(ids[0]);proposed=copy.deepcopy(self.sources)
        proposed[i]['partner']=proposed[j]['path']
        try:links_for([Source(**s) for s in proposed])
        except ValueError as exc:self.status.set(str(exc));return
        self.sources=proposed;self.render_sources();self.save()
        self.status.set('关联已合并显示；以主目录范围查找可靠配套，辅助目录其余文件不纳入。重新预览后生效。')

    def swap_sources(self):
        if self.busy:return
        ids=self.source_tree.selection()
        if not ids:return
        i=int(ids[0]);links=links_for([Source(**s) for s in self.sources])
        if i not in links:self.status.set('请先关联配套目录。');return
        j=links[i];self.sources[i]['partner']='';self.sources[j]['partner']=self.sources[i]['path']
        self.render_sources();self.source_tree.selection_set(str(j));self.select_source();self.save()
        self.status.set('主从已交换；重新预览后按新的主目录范围配套。')

    def unlink_source(self):
        if self.busy:return
        ids=self.source_tree.selection()
        if not ids:return
        self.sources[int(ids[0])]['partner']=''
        self.render_sources();self.save();self.status.set('关联已解除，两个目录恢复独立显示。重新预览后生效。')

    def apply_source(self):
        if self.busy:return
        ids=self.source_tree.selection()
        if not ids:self.status.set('先选中要修改的来源目录。');return
        if self.sources[int(ids[0])].get('partner') and self.key(MODE_LABELS,self.source_mode.get())!=self.sources[int(ids[0])]['mode']:
            self.status.set('关联组请用“交换主从”改变主侧；修改文件类型前请先解除关联。');return
        self.sources[int(ids[0])].update(mode=self.key(MODE_LABELS,self.source_mode.get()),pairing=self.key(PAIR_LABELS,self.source_pairing.get()))
        self.render_sources();self.save();self.status.set('目录设置已保存；重新生成预览后生效。')

    def remove_source(self):
        if self.busy:return
        links=links_for([Source(**s) for s in self.sources])
        remove=set(map(int,self.source_tree.selection()))
        remove.update(links[i] for i in list(remove) if i in links)
        for identity in sorted(remove,reverse=True):self.sources.pop(identity)
        self.render_sources();self.save()

    def choose_output(self):
        if self.busy:return
        path=filedialog.askdirectory(parent=self.window)
        if path:self.output.set(path)

    @staticmethod
    def key(labels,value):
        return next(k for k,v in labels.items() if v==value)

    def check(self):
        if self.cancel.is_set():raise Cancelled('已停止')

    def emit(self,kind,value):self.events.put((kind,value))

    def controls(self):
        for widget in (self.start_button,self.local_button,self.execute_button,self.add_button,
                       self.remove_button,self.apply_button,self.link_button,self.swap_button,self.unlink_button,
                       self.select_button,self.clear_button,self.use_button,self.export_button):
            widget.configure(state='disabled' if self.busy else 'normal')
        self.stop_button.configure(state='normal' if self.busy else 'disabled')
        if self.busy:self.layout.progress.start(14)
        else:self.layout.progress.stop();self.resume_button.configure(state='disabled')

    def background(self,work):
        self.busy=True;self.cancel.clear();self.resume.clear();self.controls()
        def run():
            try:work()
            except Cancelled:self.emit('log','已停止，已完成的预览和文件记录保留。')
            except Exception as exc:self.emit('log',f'{type(exc).__name__}: {exc}')
            finally:self.emit('finished',None)
        threading.Thread(target=run,daemon=True).start()

    def start(self,local_only=False):
        if self.busy:return
        try:
            if not self.output.get().strip():raise ValueError('请选择输出目录。')
            options=OrganizeOptions([Source(**s) for s in self.sources],self.author.get(),Path(self.output.get()).resolve(),
                float(self.threshold.get()),self.key(DATE_LABELS,self.date_policy.get()),self.refresh.get(),int(self.author_app.compute_workers.get()))
            options.validate();self.save()
            app=self.author_app
            if not local_only:app.network_options();app._save()
            network=dict(headful=app.visible.get(),browser_channel=app._browser_channel,
                interval=app._request_interval,search_interval=app._search_interval,per_minute=app._per_minute,cooldown_seconds=app._cooldown_seconds) if not local_only else {}
        except (ValueError,OSError,StopIteration) as exc:
            self.status.set('设置无效：'+str(exc));return
        def work():
            store=Store(self.state_dir/'catalog.sqlite3')
            try:
                prepare(options,store,lambda:BrowserClient(self.state_dir,self.cancel,self.emit,self.resume,
                    legacy_state=LEGACY,**network),self.state_dir,self.emit,self.check,local_only=local_only)
            finally:store.close()
        self.background(work)

    def execute_selected(self):
        if self.busy or not self.last_state:return
        state=copy.deepcopy(self.last_state)
        from .forum import author_from_input
        try:
            changed=(str(Path(self.output.get()).resolve())!=state['output'] or
                     author_from_input(self.author.get()).casefold()!=state['author'].casefold() or self.sources!=state['sources'])
        except ValueError:changed=True
        if changed:
            self.status.set('作者、来源或输出设置已改变，请重新生成预览后执行。');return
        selected=[r for r in state['rows'] if r['selected']]
        if not selected:self.status.set('请先勾选已核对的项目。');return
        mode='move' if self.action.get()=='移动' else 'copy'
        if mode=='move' and not messagebox.askyesno('执行移动',f"将移动 {len(selected)} 组、{sum(len(r['files']) for r in selected)} 个文件到：\n{state['output']}\n按预览中的作者／年／月存放，同名冲突不覆盖。\n确认执行？",parent=self.window):return
        self.background(lambda:execute(state,mode,self.check,self.emit))

    def poll(self):
        begin=time.monotonic()
        try:
            while time.monotonic()-begin<.03:
                kind,value=self.events.get_nowait()
                if kind in ('log','login'):
                    self.status.set(str(value))
                    if kind=='login':self.resume_button.configure(state='normal')
                elif kind=='resume_done':self.resume_button.configure(state='disabled')
                elif kind=='progress':self.status.set(f'{value[0]} / {value[1]} · {value[2]}')
                elif kind=='organized':self.publish(value)
                elif kind=='finished':self.busy=False;self.controls()
        except queue.Empty:pass
        self.window.after(poll_delay(self.window,self.busy),self.poll)

    def publish(self,state):
        self.last_state=state;self.current_output=state.get('report_folder')
        self.rows={str(i):row for i,row in enumerate(state['rows'])}
        self.tree.delete(*self.tree.get_children())
        paint_items(self,self.window,self.rows.items(),lambda item:self.tree.insert('','end',iid=item[0],values=self.values(item[1])))
        self.update_stats();self.status.set(state['status'])

    def values(self,row):
        chosen=next((c for c in row['candidates'] if c['id']==row['chosen']),None)
        return ('☑' if row['selected'] else '☐',row['title'],row['completeness'],row.get('collision') or row['status'],
                row['month'],f"{chosen['score']:.1f}" if chosen else '',chosen['payment']['tag'] if chosen else '未确认')

    def update_stats(self):
        self.layout.stats(len(self.rows),sum(r['selected'] for r in self.rows.values()),sum(bool(r['missing']) for r in self.rows.values()))

    def repaint(self):
        if not self.last_state:return
        mark_collisions(self.last_state)
        for key,row in self.rows.items():
            if self.tree.exists(key):self.tree.item(key,values=self.values(row))
        self.update_stats()

    def selected_row(self):
        ids=self.tree.selection();return self.rows.get(ids[0]) if ids else None

    def toggle(self,*_):
        if self.busy:return
        row=self.selected_row()
        if not row:return
        if not row['chosen'] or not row['target']:
            self.status.set('该项尚未确定帖子与日期，请在下方核对候选。');return
        if row['operations']:
            self.status.set('已有执行记录，请重新扫描确认剩余文件后再执行。');return
        row['selected']=not row['selected'];row['manual_selection']=True;self.repaint()
        return 'break'

    def select_reliable(self):
        if self.busy:return
        for row in self.rows.values():
            if row['status']=='可整理' and not row.get('manual_selection') and not row['operations']:row['selected']=True
        self.repaint()

    def clear_selection(self):
        if self.busy:return
        for row in self.rows.values():row['selected']=False;row['manual_selection']=True
        self.repaint()

    def select(self,*_):
        row=self.selected_row()
        if not row:return
        self.candidate_map={f"{c['id']} · {c['score']:.1f}分 · {c['month'] or '日期未知'} · {c['title'][:55]}":c for c in row['candidates']}
        values=list(self.candidate_map) or ['没有可核对的候选帖子']
        self.candidate_control.configure(values=values)
        self.candidate.set(next((k for k,c in self.candidate_map.items() if c['id']==row['chosen']),values[0]))
        from .resource_variants import version_summary
        lines=[row['title'],row['source'],row['completeness'],row['status'],'目标：'+row['target'],row.get('collision',''),
               '按作品判断缺失；差分版本保留，轴文件仍属于各自版本。', version_summary(row.get('versions', []))]
        if row.get('linked_source'):
            lines += ['配套目录：'+row['linked_source'],row.get('pairing_note','')]
        lines += [f['path'] for f in row['files']]
        for c in row['candidates']:
            lines += [f"{c['score']:.1f}分 · {c['title']} · {c['date_local'] or '发布日期未知'}",c['url'],
                f"脚本：{c['payment']['script']}；视频：{c['payment']['video']}",c['reason'],*c['evidence'],
                *c['payment']['evidence'],'帖子文件名：'+ ' / '.join(c['filenames'])]
            lines += [r.get('label','')+' · '+r['url'] for r in c['resources']]
        self.detail.set('\n'.join(lines))

    def use_candidate(self):
        if self.busy:return
        row=self.selected_row();candidate=getattr(self,'candidate_map',{}).get(self.candidate.get())
        if not row or not candidate:return
        if row['operations']:self.status.set('已有执行记录，请重新生成预览后调整。');return
        try:choose(row,candidate,self.last_state['output'],self.last_state['author'],manual=True)
        except ValueError as exc:self.status.set(str(exc));return
        row['manual_selection']=True;self.repaint();self.select()

    def reexport(self):
        if self.busy or not self.last_state:return
        state=copy.deepcopy(self.last_state)
        self.background(lambda:(self.emit('organized',export_report(state)),self.emit('log','报告已按当前核对选择更新，资源链接已重新过滤。')))

    def open_report(self):
        if self.current_output and (path:=Path(self.current_output)/'资源综合整理报告.html').is_file():os.startfile(path)
        else:self.status.set('尚未生成报告。')

    def close(self):
        self.cancel.set()
        if not self.busy:self.save();self.window.destroy()
