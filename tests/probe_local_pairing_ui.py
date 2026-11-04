"""Real local-pair page over 1,000 videos / 3,000 synthetic script records."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
from organizer import workspace
from organizer.local_tools.name_match import profile
from organizer.local_tools.pairing import match_records, select_confident


def main():
    videos, scripts = [], []
    for n in range(1000):
        name = f'Aurora Harbor Midnight {n:04d}'
        path = f'video/{name} 1080p.mp4'
        videos.append(dict(path=path,name=Path(path).name,name_profile=profile(path),duration=None))
        for axis in ('','.pitch','.roll'):
            path=f'scripts/{name}{axis}.funscript'
            scripts.append(dict(path=path,name=Path(path).name,name_profile=profile(path),
                                duration=60,duration_source='metadata',size=100))
    with tempfile.TemporaryDirectory(dir=ROOT/'build') as tmp:
        app.STATE=workspace.STATE=Path(tmp)
        root=tk.Tk();root.withdraw();root.attributes('-alpha',0)
        shell=workspace.Workspace(root);shell.show('pair')
        root.geometry('1180x820+30000+30000');root.deiconify()
        page=shell.pair;page.set_buttons(True)
        report={};errors=[];pauses=[];last=time.monotonic();started=last
        root.report_callback_exception=lambda *args:errors.append(str(args))
        def work():
            begin=time.perf_counter()
            try:
                rows=match_records(videos,scripts)
                report['selected']=select_confident(rows)
                report['seconds']=round(time.perf_counter()-begin,3)
                report['rows']=len(rows)
                report['all_axes_grouped']=all(len(r['script_paths'])==3 for r in rows)
                page.message_queue.put(('done',rows))
            except Exception as exc:
                report['error']=repr(exc)
            report['done']=True
        def tick():
            nonlocal last
            now=time.monotonic();pauses.append(now-last);last=now
            if report.get('done') and not page.running and len(page.item_rows)==1000:
                report['max_ui_pause']=round(max(pauses),3)
                report['p95_ui_pause']=round(sorted(pauses)[int(len(pauses)*.95)],3)
                root.destroy();return
            if now-started>120 or report.get('error'):
                report['timeout']=True;root.destroy();return
            root.after(50,tick)
        threading.Thread(target=work,daemon=True).start()
        root.after(50,tick);root.mainloop()
        (ROOT/'build/local-pair-ui-2.1.5.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        assert not errors and not report.get('error') and not report.get('timeout'),(errors,report)
        assert report['selected']==report['rows']==1000 and report['all_axes_grouped'],report
        assert report['max_ui_pause']<1,report
        print(json.dumps(report),flush=True)


if __name__=='__main__':main()
