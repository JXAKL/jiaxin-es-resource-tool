"""Real event-loop probe for recursive inventory, post matching and report export."""
import json
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app
from organizer import workspace
from organizer.completion import inventory, build_rows, export_report
from organizer.model import Topic


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'build') as tmp:
        folder=Path(tmp); media=folder/'media';media.mkdir()
        topics=[]
        for n in range(500):
            title=f'Aurora Harbor Missing Work {n:04d}'
            nested=media/str(n//100);nested.mkdir(exist_ok=True)
            (nested/(title+('.mp4' if n%2 else '.pitch.funscript'))).write_text('{}')
            topics.append(Topic(n+1,title,'Fixture',f'https://discuss.eroscripts.com/t/{n+1}',
                body=title+'\nPaid script, free video.',details_loaded=True,links_loaded=True,
                resource_links=[dict(url=f'https://gofile.io/d/Fixture{n}',label='video and script',context='')],
                resource_details_loaded=True))
        app.STATE=workspace.STATE=folder/'state'
        root=tk.Tk();root.withdraw();root.attributes('-alpha',0)
        shell=workspace.Workspace(root);shell.show('completion');page=shell.completion
        root.geometry('1180x820+30000+30000');root.deiconify()
        page.busy=True;page.controls()
        result={};errors=[];gaps=[];last=time.monotonic();started=last
        root.report_callback_exception=lambda *args:errors.append(str(args))
        def work():
            try:
                groups,missing=inventory(media)
                rows=build_rows(missing,topics,85)
                state=dict(author='Fixture',source=str(media),total=len(groups),rows=rows,status='测试完成',failed={},report_folder=str(folder/'report'))
                export_report(state,folder/'report')
                page.emit('completion',state);page.emit('finished',None)
                result.update(seconds=round(time.monotonic()-started,3),rows=len(rows),
                              confirmed=sum(any(c['confirmed'] for c in r['candidates']) for r in rows))
            except Exception as exc:
                result['error']=repr(exc)
            result['done']=True
        def tick():
            nonlocal last
            now=time.monotonic();gaps.append(now-last);last=now
            if (result.get('done') and not page.busy and len(page.tree.get_children())==500) or result.get('error') or now-started>120:
                result['max_ui_pause']=round(max(gaps),3)
                result['p95_ui_pause']=round(sorted(gaps)[int(len(gaps)*.95)],3)
                root.destroy();return
            root.after(50,tick)
        threading.Thread(target=work,daemon=True).start()
        root.after(50,tick);root.mainloop()
        (ROOT/'build/completion-ui-2.1.6.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        assert not errors and not result.get('error'),(errors,result)
        assert result['rows']==result['confirmed']==500,result
        assert result['max_ui_pause']<1,result
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
