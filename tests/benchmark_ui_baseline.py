"""Invisible, isolated desktop layout benchmark; never reads the user's state.

Run with the project's Python. The window is fully transparent and undecorated.
It exists only to let Tk perform the same geometry/drawing work as a real window.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time


def run(steps=40):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    state = tempfile.TemporaryDirectory(prefix='es-ui-benchmark-')
    os.environ['ES_ORGANIZER_DATA_DIR'] = state.name
    started = time.perf_counter()
    import tkinter as tk
    from customtkinter.windows.widgets.core_widget_classes import CTkBaseClass
    from organizer.workspace import Workspace
    import_seconds = time.perf_counter() - started
    draws = collections.Counter()
    original = CTkBaseClass._draw

    def counted(self, *args, **kwargs):
        draws[str(self)] += 1
        return original(self, *args, **kwargs)

    CTkBaseClass._draw = counted
    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes('-alpha', 0.0)
    started = time.perf_counter()
    workspace = Workspace(root)
    root.geometry('1380x940+30000+30000')
    root.deiconify()
    root.update()
    construction_seconds = time.perf_counter() - started

    def measure(label):
        root.update()
        draws.clear()
        samples = []
        cpu_started = time.process_time()
        for i in range(steps):
            width = 1280 + (i % 20) * 6
            height = 840 + (i % 20) * 4
            started = time.perf_counter()
            root.geometry(f'{width}x{height}+30000+30000')
            root.update()
            samples.append(time.perf_counter() - started)
        result = {
            'label': label,
            'steps': steps,
            'total_seconds': round(sum(samples), 4),
            'mean_ms': round(statistics.mean(samples) * 1000, 2),
            'p95_ms': round(sorted(samples)[int(len(samples) * .95)-1] * 1000, 2),
            'cpu_seconds': round(time.process_time()-cpu_started, 4),
            'draw_calls': sum(draws.values()),
            'draw_calls_by_page': {
                key: sum(n for widget, n in draws.items() if widget.startswith(str(page)+'.'))
                for key, page in workspace.pages.items()
            },
            'managed_pages': {key: page.winfo_manager() for key, page in workspace.pages.items()},
            'mapped_pages': {key: bool(page.winfo_ismapped()) for key, page in workspace.pages.items()},
        }
        return result

    results = [measure('author_only')]
    page_creation = {}
    for key in ('pair', 'scripts', 'videos', 'collector'):
        started = time.perf_counter()
        workspace.show(key)
        root.update()
        page_creation[key] = round(time.perf_counter() - started, 4)
    workspace.show('author')
    root.update()
    results.append(measure('all_pages_visited_author_visible'))
    # Do not save settings: this is a test state, and no controller worker ran.
    root.destroy()
    CTkBaseClass._draw = original
    state.cleanup()
    return {'import_seconds': round(import_seconds, 4),
            'construction_seconds': round(construction_seconds, 4),
            'page_creation_seconds': page_creation, 'resize': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    parser.add_argument('--steps', type=int, default=40)
    args = parser.parse_args()
    result = run(args.steps)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding='utf-8')
