"""Real Tk event-loop timing over the user's isolated metadata snapshot."""
import json
import argparse
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app
from organizer import workspace
from organizer.matching import MatchingSession
from organizer.model import FileGroup, Topic
from organizer.store import Store

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--folder',type=Path,default=ROOT/'build/performance-2.1.3')
    parser.add_argument('--cache',default='local-scores.sqlite3')
    args=parser.parse_args();folder=args.folder
    if (folder/'baseline.json').exists():
        groups=[FileGroup(**{**r['group'],'files':[]}) for r in json.loads((folder/'baseline.json').read_text(encoding='utf-8'))]
    else:
        groups=[FileGroup(**r) for r in json.loads((folder/'groups.json').read_text(encoding='utf-8'))]
    with tempfile.TemporaryDirectory() as temp:
        app.STATE=workspace.STATE=Path(temp)
        root=tk.Tk();root.withdraw();root.attributes('-alpha',0)
        shell=workspace.Workspace(root)
        root.geometry('1180x820+30000+30000');root.deiconify()
        ui=shell.author; result={'rounds':[], 'slow_ticks':[], 'stalls':[]}; errors=[]
        root.report_callback_exception=lambda *exc:errors.append(str(exc))
        started=time.monotonic(); last=started; pauses=[]
        def work():
            matcher=MatchingSession(workers=8,cache_path=folder/args.cache)
            store=None
            try:
                if (folder/'topics.json').exists():
                    result['phase']='read_snapshot'
                    loaded=[Topic(**t) for t in json.loads((folder/'topics.json').read_text(encoding='utf-8'))['topics']]
                    result['phase']='initial_index';matcher.update(loaded)
                else:
                    store=Store(folder/'catalog.sqlite3');matcher.sync(store)
                result['phase']='initial_refresh'
                matches=matcher.refresh(groups,emit=ui.emit,stop=ui.check)
                topics=list(matcher.index.topics.values())
                ids=list(dict.fromkeys(c.topic_id for m in matches for c in m.candidates if c.verified))[:8]
                for identity in ids:
                    changed=[replace(t,body=t.body+'\nOffline UI fixture appendix.') if t.id==identity else t for t in topics]
                    result['phase']='update_'+str(len(result['rounds']))
                    tick=time.monotonic(); matcher.update(changed)
                    result['phase']='refresh_'+str(len(result['rounds']))
                    matcher.refresh(groups,emit=ui.emit,stop=ui.check)
                    result['rounds'].append(round(time.monotonic()-tick,3))
                    topics=changed
            except BaseException as exc: result['error']=repr(exc)
            finally:
                result['phase']='dispose'
                matcher.dispose()
                if store:store.close()
                result['done']=True; ui.emit('finished',None)
        def tick():
            nonlocal last
            now=time.monotonic(); pauses.append(now-last)
            if now-last>.3:result['slow_ticks'].append({'seconds':round(now-last,3),'phase':result.get('phase')})
            last=now
            if result.get('done') and ui.events.empty():
                result['groups']=len(ui.matches)
                result['max_ui_pause_seconds']=round(max(pauses),3)
                result['p95_ui_pause_seconds']=round(sorted(pauses)[int(len(pauses)*.95)],3)
                root.destroy(); return
            if now-started>180: ui.stop(); result['timeout']=True
            root.after(50,tick)
        ui.worker=threading.Thread(target=work,daemon=True); ui.set_busy(True); ui.worker.start()
        def watch():
            while not result.get('done'):
                time.sleep(.2)
                if time.monotonic()-last>.5 and len(result['stalls'])<15:
                    frames=sys._current_frames()
                    result['stalls'].append({'phase':result.get('phase'),
                        'main':traceback.format_stack(frames[threading.main_thread().ident])[-7:],
                        'worker':traceback.format_stack(frames[ui.worker.ident])[-7:] if ui.worker.ident in frames else []})
        threading.Thread(target=watch,daemon=True).start()
        root.after(50,tick); root.mainloop()
        (folder/'ui-after.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        assert not errors and not result.get('error') and not result.get('timeout'),(errors,result)
        assert result['groups']==len(groups) and len(result['rounds'])==8,result
        assert result['max_ui_pause_seconds']<1,result
        print(json.dumps(result),flush=True)

if __name__=='__main__': main()
