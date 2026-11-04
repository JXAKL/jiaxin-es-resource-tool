from .widgets import ttk
from .surfaces import player_executable
from .surfaces import create_surface
"""Embedded mpv playback with a script curve driven by mpv's actual media clock."""
import json
import math
import os
import queue
import subprocess
import threading
import time
import uuid
import ctypes
import msvcrt
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk

from .script_curve import load_script, script_speed, interpolate, curve_points, percentile


class MpvPlayer:
    def __init__(self, executable, window_id, events):
        self.events = events
        self.pipe = None
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.pipe_name = r'\\.\pipe\funscript-review-' + uuid.uuid4().hex
        self.process = subprocess.Popen([
            str(executable), '--no-config', '--idle=yes', '--keep-open=yes', '--pause=yes',
            '--terminal=no', '--osc=no', '--input-default-bindings=no',
            '--input-vo-keyboard=no', '--input-cursor=no', '--stop-screensaver=no',
            '--hwdec=auto-safe', f'--wid={window_id}', f'--input-ipc-server={self.pipe_name}',
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            deadline = time.monotonic()+10
            while not self.closed.is_set():
                try:
                    self.pipe = open(self.pipe_name, 'r+b', buffering=0)
                    break
                except OSError:
                    if self.process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('MPV 启动失败或连接超时')
                    self.closed.wait(.05)
            if self.closed.is_set():
                return
            for i, prop in enumerate(('time-pos', 'duration', 'pause', 'eof-reached', 'speed', 'video-out-params')):
                self.send('observe_property', i+1, prop)
            self.events.put({'event': 'ready'})
            pending = b''
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            peek = kernel.PeekNamedPipe
            peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                             ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
            peek.restype = ctypes.c_int
            handle = msvcrt.get_osfhandle(self.pipe.fileno())
            while not self.closed.is_set():
                # Never hold a synchronous Windows pipe in a blocking ReadFile:
                # that prevents the UI thread's WriteFile from sending controls.
                available = ctypes.c_ulong()
                with self.lock:
                    if not peek(handle, None, 0, None, ctypes.byref(available), None):
                        break
                    chunk = self.pipe.read(min(65536, available.value)) if available.value else None
                if chunk is None:
                    self.closed.wait(.015)
                    continue
                if not chunk:
                    break
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    self.events.put(json.loads(line.decode('utf-8')))
            if not self.closed.is_set():
                self.events.put({'event': 'player-error', 'message': '播放器连接已断开，请重新打开核对窗口'})
        except Exception as exc:
            if not self.closed.is_set():
                self.events.put({'event': 'player-error', 'message': str(exc)})

    def send(self, *command):
        if self.pipe is None or self.closed.is_set():
            return False
        try:
            with self.lock:
                self.pipe.write((json.dumps({'command': list(command)}, ensure_ascii=False)+'\n').encode('utf-8'))
            return True
        except OSError:
            return False

    def close(self):
        self.closed.set()
        if self.process.poll() is None:
            self.process.terminate()
        # Process shutdown unblocks the reader; closing a pipe while it reads may block.
        def cleanup():
            try:
                self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.kill()
            if self.pipe:
                self.pipe.close()
        threading.Thread(target=cleanup, daemon=True).start()


def position_at(script, video_time, offset=0):
    timestamp = video_time-offset
    if script is None or timestamp < script['times'][0] or timestamp > script['times'][-1]:
        return None
    return interpolate(timestamp, script['times'], script['positions'])


class PlaybackReview:
    def __init__(self, parent, script_path='', video_path='', on_confirm=None):
        self.window = create_surface(parent)
        self.window.title('视频播放 · Funscript 同步曲线')
        self.window.geometry('1120x880')
        self.window.minsize(940, 750)
        self.events = queue.Queue()
        self.script_path = tk.StringVar(value=script_path)
        self.video_path = tk.StringVar(value=video_path)
        self.offset = tk.StringVar(value='0')
        self.rate = tk.StringVar(value='1.0')
        self.status = tk.StringVar(value='载入视频和脚本后，点击播放；曲线跟随实际播放进度。')
        self.clock = tk.StringVar(value='00:00 / 00:00')
        self.script = None
        self.loaded_video = None
        self.duration = 1.
        self.seconds = 0.
        self.paused = True
        self.actual_speed = 1.
        self.video_output = None
        self.dragging = False
        self.ready = False
        self.pending_load = False
        self.on_confirm = on_confirm
        self.player = None
        self.closed = False
        self._draw_job = self._poll_job = None
        self._curve_items = {}
        self._curve_key = self._heat_key = self._heat_image_key = None
        self._render_script = None
        self._heat_source = self._heat_photo = None
        self._heat_item = self._heat_cursor = None
        self._offset_trace = self.offset.trace_add('write', lambda *_: self.draw())
        self.build()
        executable = player_executable()
        if not executable.is_file():
            executable = Path(filedialog.askopenfilename(parent=self.window, title='请选择 mpv.exe', filetypes=[('MPV', 'mpv.exe')]) or '.')
        if not executable.is_file():
            self.status.set('未找到播放器，请选择 mpv.exe，或保留完整的程序文件夹。')
        else:
            self.window.update_idletasks()
            try:
                self.player = MpvPlayer(executable, self.video_area.winfo_id(), self.events)
                if script_path and video_path:
                    self.load()
            except Exception as exc:
                self.status.set(str(exc))
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self._poll_job = self.window.after(33, self.poll)

    def build(self):
        from ..local_ui import build_playback
        build_playback(self)

    def choose(self, variable):
        types = [('Funscript','*.funscript')] if variable is self.script_path else [('视频','*.mp4 *.mkv *.avi *.mov *.webm *.wmv *.m4v *.ts'),('全部','*.*')]
        path = filedialog.askopenfilename(parent=self.window, filetypes=types)
        if path:
            variable.set(path)
            if self.video_path.get() and self.script_path.get():
                self.load()

    def load(self):
        try:
            script = load_script(self.script_path.get().strip())
            path = Path(self.video_path.get().strip()).resolve()
            if not path.is_file():
                raise ValueError('视频不存在')
            self.script = script
            self.loaded_video = str(path)
            self.seconds = 0
            self.duration = script['duration'] or script['last_action']
            self.pending_load = True
            self.draw_heat()
            self.draw()
            if self.ready:
                self.load_player()
        except Exception as exc:
            messagebox.showerror('载入失败', str(exc), parent=self.window)

    def load_player(self):
        self.command('set_property','pause',True)
        self.command('loadfile',self.loaded_video,'replace')
        self.pending_load = False
        self.status.set('已载入。点击播放，同步查看位置曲线和运动指示点；可调倍速与偏移。')

    def command(self, *args):
        return self.player and self.player.send(*args)

    def toggle(self):
        if self.loaded_video and self.ready:
            self.command('cycle','pause')

    def seek_to(self, seconds):
        if self.loaded_video:
            seconds = max(0, min(self.duration, seconds))
            self.command('seek', seconds, 'absolute+exact')

    def jump(self, delta):
        self.seek_to(self.seconds+delta)

    def slider_release(self, event=None):
        self.seek_to(float(self.slider.get()))
        self.dragging = False

    def get_offset(self):
        try:
            offset = float(self.offset.get())
            return offset if math.isfinite(offset) and abs(offset) <= 3600 else 0.
        except ValueError:
            return 0.

    def draw(self):
        """Coalesce player, offset and resize updates into one UI frame."""
        if self.closed or self._draw_job is not None:
            return
        self._draw_job = self.window.after(16, self._paint)

    def draw_heat(self):
        # Existing Configure and load handlers can keep calling this entry point.
        self.draw()

    def _paint(self):
        self._draw_job = None
        if self.closed:
            return
        if self.script is None:
            return
        if self._render_script is not self.script:
            self._render_script = self.script
            self._curve_key = self._heat_key = self._heat_image_key = None
        width = max(200, self.curve.winfo_width())
        heat_width = max(200, self.heat.winfo_width()) - 60
        offset = self.get_offset()
        curve_key = (id(self.script), self.seconds, offset, width)
        if curve_key != self._curve_key:
            self._paint_curve(width, offset)
            self._curve_key = curve_key
        self._paint_heat(heat_width, offset)
        hx = 48 + self.seconds / max(1, self.duration) * heat_width
        self.heat.coords(self._heat_cursor, hx, 0, hx, 42)

    def _paint_curve(self, width, offset):
        if not self._curve_items:
            items = self._curve_items
            for value in (0, 50, 100):
                items[f'grid{value}'] = self.curve.create_line(0, 0, 0, 0, fill='#334756')
                self.curve.create_text(25, 130-value, text=str(value), fill='#a5bac8')
            items['trace'] = self.curve.create_line(0, 0, 0, 0, fill='#37d5c5', width=2)
            items['marker'] = self.curve.create_line(0, 0, 0, 0, fill='#ffffff', width=2)
            items['dot'] = self.curve.create_oval(0, 0, 0, 0, fill='#ffffff', outline='')
            items['bar'] = self.curve.create_rectangle(0, 0, 0, 0, outline='#8aa5b5')
            items['knob'] = self.curve.create_oval(0, 0, 0, 0, fill='#37d5c5', outline='')
            items['position'] = self.curve.create_text(0, 0, fill='white')
            items['start'] = self.curve.create_text(0, 145, fill='#a5bac8', anchor='w')
            items['end'] = self.curve.create_text(0, 145, fill='#a5bac8', anchor='e')
        items = self._curve_items
        left, right = 48, width-90
        start, end = self.seconds-6, self.seconds+6
        if not self._curve_key or self._curve_key[-1] != width:
            for value in (0, 50, 100):
                self.curve.coords(items[f'grid{value}'], left, 130-value, right, 130-value)
            x = (left+right)/2
            self.curve.coords(items['marker'], x, 12, x, 135)
            self.curve.coords(items['bar'], width-54, 30, width-36, 130)
            self.curve.coords(items['position'], width-46, 14)
            self.curve.coords(items['start'], left, 145)
            self.curve.coords(items['end'], right, 145)
        points = curve_points(self.script, start, end, offset)
        if len(points) >= 2:
            coords = []
            for t,p in points:
                coords.extend((left+(t-start)/12*(right-left),130-p))
            self.curve.coords(items['trace'], *coords)
            self.curve.itemconfigure(items['trace'], state='normal')
        else:
            self.curve.itemconfigure(items['trace'], state='hidden')
        x = (left+right)/2
        pos = position_at(self.script,self.seconds,offset)
        for name in ('dot', 'bar', 'knob'):
            self.curve.itemconfigure(items[name], state='normal' if pos is not None else 'hidden')
        if pos is not None:
            y=130-pos
            self.curve.coords(items['dot'], x-4, y-4, x+4, y+4)
            self.curve.coords(items['knob'], width-61, y-5, width-29, y+5)
        self.curve.itemconfigure(items['position'], text='—' if pos is None else f'{pos:.0f}')
        self.curve.itemconfigure(items['start'], text=f'{start:.1f}s')
        self.curve.itemconfigure(items['end'], text=f'{end:.1f}s')

    def _paint_heat(self, width, offset):
        # Temporal sampling stays independent of window width. A resize scales a
        # cached strip, instead of recalculating and creating 1,200 canvas boxes.
        key = (id(self.script), self.duration, offset)
        if key != self._heat_key:
            count = 1024
            step = max(.001, self.duration/count)
            times = [(i+.5)*step-offset for i in range(count)]
            speed = script_speed(self.script, times, step)
            good = [value for value in speed if math.isfinite(value)]
            scale = max(1., percentile(good, .95)) if good else 1.
            colors = []
            for value in speed:
                v = min(1, max(0, value/scale)) if math.isfinite(value) else None
                colors.append((85, 85, 85) if v is None else
                              (int(255*v), int(180*(1-abs(2*v-1))), int(230*(1-v))))
            self._heat_source = Image.new('RGB', (count, 1))
            self._heat_source.putdata(colors)
            self._heat_key = key
            self.heat_offset = offset
        image_key = (key, width)
        if image_key != self._heat_image_key:
            image = self._heat_source.resize((int(width), 26), Image.Resampling.NEAREST)
            self._heat_photo = ImageTk.PhotoImage(image, master=self.heat)
            if self._heat_item is None:
                self.heat.create_text(5, 20, text='热力', fill='white', anchor='w')
                self._heat_item = self.heat.create_image(48, 8, anchor='nw', image=self._heat_photo)
                self._heat_cursor = self.heat.create_line(0, 0, 0, 42, fill='white', width=2, tags='cursor')
            else:
                self.heat.itemconfigure(self._heat_item, image=self._heat_photo)
            self._heat_image_key = image_key

    def seek_curve(self,event):
        width=max(200,self.curve.winfo_width())
        self.seek_to(self.seconds-6+(event.x-48)/(width-138)*12)

    def seek_heat(self,event):
        self.seek_to((event.x-48)/(max(200,self.heat.winfo_width())-60)*self.duration)

    def poll(self):
        self._poll_job = None
        if self.closed:
            return
        changed = clock_changed = False
        started = time.monotonic()
        try:
            while time.monotonic() - started < .008:
                event = self.events.get_nowait()
                if event.get('event') == 'ready':
                    self.ready = True
                    if self.pending_load:
                        self.load_player()
                elif event.get('event') == 'player-error':
                    self.ready = False
                    self.status.set(event['message'])
                elif event.get('event') == 'end-file' and event.get('reason') == 'error':
                    self.status.set('视频载入/解码失败：'+str(event.get('file_error','未知错误')))
                elif event.get('event') == 'property-change':
                    name,value=event.get('name'),event.get('data')
                    if name == 'time-pos' and isinstance(value,(int,float)) and math.isfinite(value):
                        if self.seconds != value:
                            self.seconds=float(value)
                            changed=clock_changed=True
                    elif name == 'duration' and isinstance(value,(int,float)) and math.isfinite(value) and value>0:
                        if self.duration != value:
                            self.duration=float(value)
                            self.slider.configure(to=self.duration)
                            changed=clock_changed=True
                    elif name == 'pause':
                        self.paused=bool(value)
                        clock_changed=True
                    elif name == 'speed' and isinstance(value,(int,float)):
                        self.actual_speed=float(value)
                    elif name == 'video-out-params':
                        self.video_output=value
        except queue.Empty:
            pass
        if changed:
            if not self.dragging:
                self.slider.set(self.seconds)
            self.draw()
        if clock_changed:
            def stamp(seconds):
                return f'{int(seconds)//60:02d}:{int(seconds)%60:02d}'
            self.clock.set(f'{stamp(self.seconds)} / {stamp(self.duration)} · '+('暂停' if self.paused else '播放中'))
        self._poll_job = self.window.after(100 if self.paused else 33, self.poll)

    def confirm(self):
        if self.script is None or self.loaded_video is None:
            return
        if self.on_confirm:
            self.on_confirm({'video_path':self.loaded_video,'script_path':self.script['path'],
                             'duration':self.duration,'score':None})
        self.status.set('已加入主窗口人工确认列表。偏移仅用于预览，不会写入脚本。')

    def close(self):
        if self.closed:
            return
        self.closed=True
        for job in (self._poll_job, self._draw_job):
            if job is not None:
                self.window.after_cancel(job)
        self._poll_job = self._draw_job = None
        self.offset.trace_remove('write', self._offset_trace)
        if self.player:
            self.player.close()
        self.window.destroy()
        self._heat_photo = self._heat_source = None
        self._render_script = self.script = None
