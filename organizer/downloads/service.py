"""Background process boundary for browser and transfers; no dependency on Qt."""
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
from .model import KEYS


def runtime_paths():
    import playwright
    package = Path(playwright.__file__).resolve().parent / 'driver'
    node = package / ('node.exe' if os.name == 'nt' else 'node')
    module = package / 'package'
    script = Path(__file__).resolve().parent / 'js' / 'runner.js'
    if not node.is_file() or not (module / 'index.js').is_file() or not script.is_file():
        raise RuntimeError('缺少下载运行文件，请使用完整程序目录。')
    return node, module, script


class DownloadService:
    def __init__(self, emit):
        self.emit = emit
        self.process = None
        self.cancel = threading.Event()
        self._lock = threading.Lock()

    def stop(self):
        self.cancel.set()
        self.send('STOP')

    def send(self, message):
        with self._lock:
            if self.process and self.process.poll() is None:
                try:
                    self.process.stdin.write(message + '\n'); self.process.stdin.flush()
                except (OSError, ValueError, BrokenPipeError):
                    pass

    def run(self, items, options, login=False):
        if self.cancel.is_set():
            return
        node, module, script = runtime_paths()
        options = dict(options)
        if options.get('hanimeLegacy', True) and any(x.platform == 'Hanime1' for x in items):
            from .legacy_profile import prepare_site_profile, chrome_installed
            from ..runtime import ROOT
            if chrome_installed():
                candidates = [ROOT.parent / 'hanime_chrome_profile', ROOT.parent / 'hanime1_chrome_profile',
                              ROOT.parent / 'hanime1_gui/hanime_chrome_profile',
                              Path(options['state']) / 'download-browser/hanime']
                source = next((p for p in candidates if (p/'Local State').is_file()), candidates[-1])
                options['hanimeProfile'] = prepare_site_profile(options['state'], source, self.emit, 'hanime', 'Hanime1')
            else:
                self.emit({'type':'log', 'text':'未发现本机 Chrome；Hanime1 暂用所选浏览器，未载入兼容 Chrome 会话。'})
        if options.get('iwaraLegacy', True) and any(x.platform == 'Iwara' for x in items):
            from .legacy_profile import prepare_iwara_profile, chrome_installed
            from ..runtime import ROOT
            if chrome_installed():
                options['iwaraProfile'] = prepare_iwara_profile(options['state'], ROOT.parent / 'hanime1_gui/iwara_chrome_profile', self.emit)
            else:
                self.emit({'type':'log', 'text':'未发现本机 Chrome；Iwara 暂用所选浏览器，未载入旧 Chrome 会话。'})
        payload = {'items': [{'url': x.url, 'title': x.title, 'platform': KEYS[x.platform]} for x in items],
                   'options': options, 'login': login}
        folder = Path(options['state']) / 'download-jobs'
        folder.mkdir(parents=True, exist_ok=True)
        file = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=folder, suffix='.json', delete=False) as stream:
                json.dump(payload, stream, ensure_ascii=False); file = Path(stream.name)
            env = os.environ.copy()
            from ..runtime import configure_bundled_browser
            configure_bundled_browser()
            if os.environ.get('PLAYWRIGHT_BROWSERS_PATH'):
                env['PLAYWRIGHT_BROWSERS_PATH'] = os.environ['PLAYWRIGHT_BROWSERS_PATH']
            env['ES_RESOURCE_PLAYWRIGHT'] = str(module)
            env.pop('NODE_OPTIONS', None)
            self.process = subprocess.Popen([str(node), str(script), str(file)], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                env=env, cwd=folder, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            lines = queue.Queue()
            def read():
                try:
                    for line in self.process.stdout:
                        lines.put(line)
                finally:
                    lines.put(None)
            threading.Thread(target=read, daemon=True).start()
            stopping_at = None
            while True:
                if self.cancel.is_set():
                    if stopping_at is None:
                        self.send('STOP'); stopping_at = time.monotonic()
                    elif time.monotonic() - stopping_at > 8 and self.process.poll() is None:
                        # Only terminate this known child tree if graceful cancellation did not finish.
                        if os.name == 'nt':
                            subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=10)
                        else:
                            self.process.terminate()
                try:
                    line = lines.get(timeout=.1)
                except queue.Empty:
                    if self.process.poll() is not None:
                        break
                    continue
                if line is None:
                    break
                if line.startswith('RESOURCE_EVENT '):
                    try:
                        self.emit(json.loads(line[len('RESOURCE_EVENT '):]))
                    except ValueError:
                        self.emit({'type': 'log', 'text': '忽略一条格式不完整的进度消息。'})
                elif line.strip():
                    self.emit({'type': 'log', 'text': line.strip()[:2000]})
            code = self.process.wait(timeout=12)
            if code and not self.cancel.is_set():
                self.emit({'type': 'error', 'text': f'下载进程退出（{code}）；失败项和断点已保留。'})
        finally:
            if self.process:
                if self.process.poll() is None:
                    self.process.terminate()
                for stream in (self.process.stdin, self.process.stdout):
                    if stream:
                        stream.close()
            self.process = None
            if file:
                file.unlink(missing_ok=True)
