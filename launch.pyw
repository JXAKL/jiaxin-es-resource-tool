"""Windowed entry point with persistent tracebacks when there is no console."""
# Frozen multiprocessing workers must be dispatched before UI/log initialization.
if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()

import json
import sys
import tempfile
import traceback
from pathlib import Path

from organizer.runtime import ROOT, STATE, FROZEN


def start():
    folder = STATE / 'logs'
    folder.mkdir(parents=True, exist_ok=True)
    log_path = folder / 'startup.log'
    original_stdout, original_stderr = sys.stdout, sys.stderr
    with log_path.open('a', encoding='utf-8', buffering=1) as stream:
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
        try:
            import app
            if '--self-test' in sys.argv:
                from organizer.release_check import run
                report_path = Path(sys.argv[sys.argv.index('--self-test') + 1]).resolve()
                run(report_path)
            elif '--smoke-test' in sys.argv:
                import ctypes
                import tkinter as tk
                with tempfile.TemporaryDirectory() as temp:
                    app.STATE = Path(temp)
                    root = tk.Tk()
                    app.App(root)
                    root.update()
                    console = bool(ctypes.windll.kernel32.GetConsoleWindow()) if sys.platform == 'win32' else None
                    report = {'console_attached': console, 'window_created': bool(root.winfo_ismapped()),
                              'executable': sys.executable}
                    (STATE / 'launcher-smoke.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                    root.destroy()
                    assert console is False, 'Windowed launcher unexpectedly has a console'
                    assert report['window_created'], 'App window was not created'
            else:
                app.main()
            return 0
        except Exception:
            traceback.print_exc(file=stream)
            stream.flush()
            if '--smoke-test' not in sys.argv and '--self-test' not in sys.argv:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                advice = '请完整解压发行包，并保留 _internal 文件夹。' if FROZEN else '请检查 Python 与依赖是否安装完整。'
                messagebox.showerror('启动失败', f'启动详情已写入：\n{log_path}\n\n{advice}', parent=root)
                root.destroy()
            return 1
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr


if __name__ == '__main__':
    raise SystemExit(start())
