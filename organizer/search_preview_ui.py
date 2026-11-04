"""One silent embedded player, reused for still/animated cached previews."""
import queue
import threading
import time
import tkinter as tk
from PIL import Image, ImageOps, ImageTk
from .panels import frame, label, button, PANEL, MUTED
from .search_thumbnails import cache_path
from .preview_policy import visible_files


class PreviewPane:
    def __init__(self, parent, surface):
        self.surface=surface;self.events=queue.Queue();self.player=None
        self.ready=False;self.pending=None;self.identity=None;self.files=[];self.index=0
        self.closed=False;self.position=0.;self.video_params=None
        self.image_stop=None;self.frames=None;self.next_frame=0.;self.photo=None;self.frame_count=0
        self.resize_job=None
        self.panel=tk.Frame(parent,bg=PANEL,width=220)
        self.split=parent
        parent.add_panel(self.panel, '预览', 220, 140)
        self.panel.grid_propagate(False)
        self.panel.columnconfigure(0,weight=1);self.panel.rowconfigure(1,weight=1)
        self.title=tk.StringVar(value='移到帖子上预览')
        label(self.panel,textvariable=self.title,size=11,color=MUTED,wraplength=200,justify='left').grid(row=0,column=0,sticky='ew',pady=6)
        self.area=tk.Frame(self.panel,bg='#111214');self.area.grid(row=1,column=0,sticky='nsew')
        self.area.bind('<Configure>', self.resize_image, add='+')
        self.image_label=tk.Label(self.area,bg='#111214',bd=0)
        row=frame(self.panel);row.grid(row=2,column=0,sticky='ew',pady=6)
        button(row,'上一张',lambda:self.step(-1),width=78).pack(side='left')
        button(row,'下一张',lambda:self.step(1),width=78).pack(side='right')
        surface.bind('<Destroy>',lambda event:self.close() if event.widget is surface else None,add='+')
        surface.after(100,self.poll)

    def visible(self, value):
        self.split.show_panel(self.panel, value)
        if not value:self.clear()

    def show(self, row):
        if self.closed:return
        preview=row.get('preview') or {}
        allowed=visible_files(preview,row['id'])
        files=[path for item in allowed if (path:=cache_path(preview.get('root',''),item.get('file','')))]
        identity=(row['id'],tuple(str(p) for p in files))
        if identity==self.identity:return
        self.identity=identity;self.files=files;self.index=0
        if not files:
            self.stop()
            self.title.set('已过滤广告，暂无作品预览' if len(allowed)<len(preview.get('files',[])) or preview.get('filtered')
                           else '没有已缓存预览' if not preview.get('errors') else '预览下载失败；帖子结果已保留')
            return
        self.load()

    def step(self, amount):
        if not self.files:return
        self.index=(self.index+amount)%len(self.files);self.load()

    def load(self):
        self.stop()
        self.pending=str(self.files[self.index]);self.position=0.;self.video_params=None
        self.title.set(f'预览 {self.index+1}/{len(self.files)} · 静音循环')
        if self.files[self.index].suffix not in {'.mp4','.webm'}:
            self.load_image(self.files[self.index]);return
        self.image_label.place_forget()
        if self.player is None:
            from .local_tools.playback_review import MpvPlayer
            from .local_tools.surfaces import player_executable
            self.area.update_idletasks()
            try:self.player=MpvPlayer(player_executable(),self.area.winfo_id(),self.events)
            except Exception as exc:self.title.set(f'预览播放器不可用：{exc}');return
        if self.ready:self.play_pending()

    def load_image(self, path):
        self.image_label.place(x=0,y=0,relwidth=1,relheight=1)
        self.area.update_idletasks()
        size=(max(16,min(960,self.area.winfo_width())),max(16,min(720,self.area.winfo_height())))
        stop=threading.Event();frames=queue.Queue(maxsize=2)
        self.image_stop=stop;self.frames=frames;self.next_frame=0.;self.frame_count=0
        def put(item):
            while not stop.is_set():
                try:frames.put(item,timeout=.05);return
                except queue.Full:pass
        def decode():
            try:
                with Image.open(path) as source:
                    index=0
                    while not stop.is_set():
                        source.seek(index)
                        duration=max(40,min(10000,int(source.info.get('duration') or 100)))
                        frame=ImageOps.contain(source.convert('RGBA'),size,Image.Resampling.LANCZOS)
                        put(dict(frame=frame,duration=duration))
                        if not getattr(source,'is_animated',False):break
                        index=(index+1)%source.n_frames
            except Exception as exc:put(dict(error=str(exc)))
        threading.Thread(target=decode,daemon=True).start()

    def resize_image(self, event=None):
        if self.resize_job is not None:self.surface.after_cancel(self.resize_job)
        def refresh():
            self.resize_job=None
            if not self.closed and self.image_stop and self.files:
                self.load()
        self.resize_job=self.surface.after(160, refresh)

    def play_pending(self):
        if not self.pending or self.image_stop:return
        self.player.send('set_property','mute',True)
        self.player.send('set_property','aid','no')
        self.player.send('set_property','loop-file','inf')
        self.player.send('set_property','image-display-duration','inf')
        self.player.send('loadfile',self.pending,'replace')
        self.player.send('set_property','pause',False)

    def stop(self):
        self.pending=None
        if self.image_stop:self.image_stop.set();self.image_stop=None
        self.frames=None
        try:self.image_label.configure(image='')
        except tk.TclError:pass
        self.photo=None
        if self.player:self.player.send('stop')

    def clear(self):
        self.stop()
        self.identity=None;self.files=[];self.pending=None;self.ready=False
        if self.player:self.player.close();self.player=None
        try:self.title.set('移到帖子上预览')
        except tk.TclError:pass
        while not self.events.empty():
            try:self.events.get_nowait()
            except queue.Empty:break

    def poll(self):
        if self.closed:return
        if not getattr(self.surface,'active',True) and (self.player or self.identity):self.clear()
        if self.frames and time.monotonic()>=self.next_frame:
            try:
                item=self.frames.get_nowait()
                if 'error' in item:self.title.set('预览解码失败：'+item['error'])
                else:
                    self.photo=ImageTk.PhotoImage(item['frame'],master=self.image_label)
                    self.image_label.configure(image=self.photo)
                    self.frame_count+=1;self.position+=item['duration']/1000
                    self.video_params=dict(w=item['frame'].width,h=item['frame'].height)
                    self.next_frame=time.monotonic()+item['duration']/1000
            except queue.Empty:pass
        for _ in range(80):
            try:event=self.events.get_nowait()
            except queue.Empty:break
            if event.get('event')=='ready' and self.player:
                self.ready=True;self.play_pending()
            elif event.get('event')=='file-loaded' and self.player:
                self.player.send('get_property','video-out-params')
            elif isinstance(event.get('data'),dict) and 'w' in event['data'] and 'h' in event['data']:
                self.video_params=event['data']
            elif event.get('event')=='property-change':
                if event.get('name')=='time-pos':self.position=event.get('data') or 0.
                if event.get('name')=='video-out-params':self.video_params=event.get('data')
            elif event.get('event')=='player-error':
                self.title.set('预览播放失败：'+event.get('message',''))
            elif event.get('event')=='end-file' and event.get('reason')=='error':
                self.title.set('预览文件无法播放，可切换其他预览')
        self.surface.after(20 if self.frames or self.player else 150,self.poll)

    def close(self):
        if self.closed:return
        if self.resize_job is not None:
            self.surface.after_cancel(self.resize_job);self.resize_job=None
        self.closed=True;self.clear()
