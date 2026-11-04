"""Visible offline responsiveness and cancellation probe with real worker processes."""
import json
import sqlite3
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
from organizer.forum import Cancelled
from organizer.matching import MatchingSession
from organizer.model import FileGroup, Topic


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    report = sorted((ROOT / 'reports').glob('*/归类预览.json'))[-1]
    rows = json.loads(report.read_text(encoding='utf-8'))
    groups = [FileGroup(**{**r['group'], 'files': []}) for r in rows]
    with sqlite3.connect((ROOT / '.state/catalog.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        topics = [Topic(**json.loads(r[0])) for r in db.execute('SELECT data FROM topics ORDER BY id')]
    # Use enough real saved records for progress, then click the actual Stop
    # handler while workers are comparing long bodies. No media/session access.
    outcome = {}
    with tempfile.TemporaryDirectory() as temp:
        app.STATE = Path(temp)
        root = tk.Tk()
        ui = app.App(root)
        started, last_tick = time.monotonic(), time.monotonic()
        pauses = []
        stop_at = None
        def work():
            matcher = MatchingSession(workers=4)
            try:
                matcher.update(topics)
                matcher.refresh(groups, emit=ui.emit, stop=ui.check)
                outcome['unexpected_completion'] = True
            except Cancelled:
                outcome['cancelled'] = True
            except BaseException as exc:
                outcome['error'] = repr(exc)
            finally:
                matcher.close()
                outcome['finished_at'] = time.monotonic()
                ui.emit('finished', None)
        def tick():
            nonlocal last_tick, stop_at
            now = time.monotonic()
            pauses.append(now - last_tick)
            last_tick = now
            if stop_at is None and now - started > 4 and ui.matches:
                stop_at = now
                ui.stop()
            if 'finished_at' in outcome:
                outcome['max_ui_pause_seconds'] = round(max(pauses), 3)
                outcome['stop_latency_seconds'] = round(outcome['finished_at'] - stop_at, 3) if stop_at else None
                outcome['preview_groups'] = len(ui.matches)
                root.destroy()
                return
            if now - started > 60:
                ui.stop()
                outcome['timeout'] = True
            root.after(50, tick)
        ui.set_busy(True)
        ui.worker = threading.Thread(target=work, daemon=True)
        ui.worker.start()
        root.after(50, tick)
        root.mainloop()
    assert outcome.get('cancelled') and 'error' not in outcome and not outcome.get('timeout'), outcome
    assert outcome['preview_groups'] == len(groups), outcome
    assert outcome['max_ui_pause_seconds'] < 1., outcome
    assert outcome['stop_latency_seconds'] < 3., outcome
    print('LOCAL_MATCHING_UI_OK', json.dumps(outcome), flush=True)


if __name__ == '__main__':
    main()
