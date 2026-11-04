"""Read local media headers in one paused, invisible MPV process per scan."""
import json
import math
import os
import queue
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from ..surfaces import player_executable
from ..funscript_duplicates.core import check_cancel

PREFIX = 'ES_VIDEO_INFO:'
LUA = r'''
local utils = require('mp.utils')
local input = assert(io.open(os.getenv('ES_VIDEO_PROBE_INPUT'), 'rb'))
local paths = assert(utils.parse_json(input:read('*a')))
input:close()
local index, finished, active, timer, pending = 0, true, nil, nil, nil
local advance
local function finish(info)
    if finished then return end
    finished = true
    if timer then timer:kill() end
    info.index = index
    io.stdout:write('ES_VIDEO_INFO:' .. utils.format_json(info) .. '\n')
    io.stdout:flush()
    mp.add_timeout(0, advance)
end
advance = function()
    index = index + 1
    if index > #paths then mp.commandv('quit'); return end
    finished, active, pending = false, nil, nil
    timer = mp.add_timeout(8, function()
        local info = pending or {}
        info.error = '读取视频头超时（8 秒）；仅保留完整文件查重'
        finish(info)
    end)
    mp.commandv('loadfile', paths[index], 'replace')
end
mp.register_event('start-file', function(e) active = e.playlist_entry_id end)
local function complete_info()
    if finished or not pending then return end
    local params = mp.get_property_native('video-params') or {}
    pending.width = pending.width or params.w
    pending.height = pending.height or params.h
    pending.duration = pending.duration or mp.get_property_number('duration')
    pending.fps = pending.fps or mp.get_property_number('container-fps')
    if pending.duration and pending.duration > 0 and pending.width then finish(pending) end
end
mp.register_event('video-reconfig', complete_info)
mp.register_event('file-loaded', function()
    local info = {duration=mp.get_property_number('duration'),
                  container=mp.get_property('file-format')}
    for _, track in ipairs(mp.get_property_native('track-list') or {}) do
        if track.type == 'video' and not track.image and not info.width then
            info.width, info.height = track['demux-w'], track['demux-h']
            info.fps, info.video_codec = track['demux-fps'], track.codec
        elseif track.type == 'audio' and not info.audio_codec then
            info.audio_codec = track.codec
        end
    end
    pending = info
    complete_info()
    if not finished then
        local serial = index
        mp.add_timeout(.5, function()
            if serial ~= index or finished then return end
            complete_info()
            if not finished then
                pending.error = '缺少有效视频轨或时长；仅保留完整文件查重'
                finish(pending)
            end
        end)
    end
end)
mp.register_event('end-file', function(e)
    if e.reason == 'error' and active == e.playlist_entry_id then
        finish({error='无法读取视频头：' .. (e.error or '格式不支持/文件损坏')})
    end
end)
mp.add_timeout(0, advance)
'''


def probe_videos(paths, cancel=None, progress=None):
    """No playback, network, decode pass, or process launch for every file."""
    paths = list(dict.fromkeys(str(Path(path).absolute()) for path in paths))
    if not paths:
        return {}
    executable = player_executable()
    result = {}
    if not executable.is_file():
        return {path: dict(error='缺少随附 MPV，无法读取时长；仍执行完整文件查重') for path in paths}
    with tempfile.TemporaryDirectory(prefix='es-video-probe-') as directory:
        source = Path(directory)/'probe.lua'
        source.write_text(LUA, encoding='utf-8')
        input_file = Path(directory)/'paths.json'
        input_file.write_text(json.dumps(paths, ensure_ascii=False), encoding='utf-8')
        env = dict(os.environ, ES_VIDEO_PROBE_INPUT=str(input_file))
        process = subprocess.Popen([
            str(executable), '--no-config', '--idle=yes', '--pause=yes', '--vo=null', '--ao=null',
            '--audio=no', '--terminal=yes', '--msg-level=all=no', '--osc=no',
            '--input-default-bindings=no', '--stop-screensaver=no', '--network-timeout=1',
            '--demuxer-max-bytes=1MiB', '--demuxer-readahead-secs=0', '--cache=no',
            '--ytdl=no', '--access-references=no', '--load-scripts=no', f'--script={source}',
        ], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        messages = queue.Queue()
        def read():
            try:
                for line in process.stdout:
                    if line.startswith(PREFIX.encode()):
                        messages.put(line[len(PREFIX):])
            finally:
                messages.put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        last = time.monotonic()
        try:
            while len(result) < len(paths):
                check_cancel(cancel)
                try:
                    message = messages.get(timeout=.1)
                except queue.Empty:
                    if time.monotonic()-last > 12:
                        break
                    continue
                if message is None:
                    break
                info = json.loads(message)
                index = info.pop('index')-1
                if not 0 <= index < len(paths):
                    raise ValueError('视频头读取返回无效序号')
                duration = info.get('duration')
                if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
                    info.pop('duration', None)
                    info.setdefault('error', '无法读取有效时长')
                result[paths[index]] = info
                last = time.monotonic()
                if progress:
                    progress(index+1, len(paths), paths[index])
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            reader.join(timeout=2)
            process.stdout.close()
    check_cancel(cancel)
    for path in paths:
        result.setdefault(path, dict(error='视频头读取进程异常退出/无响应；仅保留完整文件查重'))
    return result
