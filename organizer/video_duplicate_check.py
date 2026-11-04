"""Drive the real video duplicate controls using disposable local AVI files."""
import json
import shutil
import threading
import time
from pathlib import Path
from unittest.mock import patch

from .video_fixture import make_video
from .local_tools.video_duplicates.similarity import similar_groups


def exercise(shell, root, folder):
    assert shell.show('videos')
    page = shell.pages['videos'].controller
    root.geometry('1180x820');root.update()
    source = Path(folder)/'video-duration-fixture'
    for name in ('A', 'B', 'C', 'D'):
        (source/name).mkdir(parents=True)
    first = source/'A'/'Aurora Moonlight Dance 01 1080p.avi'
    second = source/'B'/'Aurora Moonlight Dance 1 [Collection 159] 720p.avi'
    make_video(first,seconds=3,width=160)
    make_video(second,seconds=3,width=320)
    shutil.copy2(first,source/'C'/'Renamed original.avi')
    make_video(source/'D'/'Unrelated Ocean Sunrise.avi',seconds=5)
    page.folder.set(str(source))
    for key,value in [('duration_seconds','2'),('duration_percent','0.5'),('name_threshold','90')]:
        page.video_options[key].set(value)
    page.start_button.invoke()
    assert page.busy
    assert all(control.cget('state')=='disabled' for control in page.option_controls)
    assert not shell.show('scripts') and shell.current=='videos'
    assert page.kind_selector.get()=='视频'
    def wait(predicate,timeout=20):
        end = time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:
            root.update();time.sleep(.01)
        assert predicate(),page.status.get()
        root.update()
    wait(lambda:not page.busy)
    assert page.result is not None,page.status.get()
    assert len(page.result['similar_groups'])==len(page.result['exact_groups'])==1,page.result
    wait(lambda:len(page.tree.get_children())==1 and len(page.items)==2)
    assert [var.get() for var in page.layout.stat_values]==['4','1','1']
    child=page.tree.get_children(page.tree.get_children()[0])[1]
    page.tree.selection_set(child);page.select()
    assert '时长差' in page.detail.get() and '编码' in page.detail.get() and '画面' in page.detail.get()
    assert '3.000 秒' in page.tree.item(child,'values')
    report=Path(folder)/'duration-report.txt'
    with patch('organizer.local_tools.funscript_duplicates.ui.filedialog.asksaveasfilename',return_value=str(report)):
        page.export_button.invoke()
    assert '时长与名称相近' in report.read_text(encoding='utf-8-sig')
    page.video_options['name_threshold'].set('100')
    with patch('organizer.local_tools.funscript_duplicates.ui.filedialog.askopenfilename',return_value=str(report)):
        page.load_button.invoke()
    wait(lambda:not page.busy)
    assert page.video_options['name_threshold'].get()=='90'
    for mode, expected in [(page.MODES[1],1),(page.MODES[2],0),(page.MODES[0],1)]*3:
        page.mode.set(mode);page.populate()
        wait(lambda:len(page.tree.get_children())==expected)
    viewport=page.layout.settings._parent_canvas
    viewport.yview_moveto(0);root.update()
    for control in page.option_controls:
        assert control.winfo_ismapped()
        assert control.winfo_rootx()>=viewport.winfo_rootx()
        assert control.winfo_rootx()+control.winfo_width()<=viewport.winfo_rootx()+viewport.winfo_width()
        assert control.winfo_rooty()>=viewport.winfo_rooty()
        assert control.winfo_rooty()+control.winfo_height()<=viewport.winfo_rooty()+viewport.winfo_height(), 'Video parameters clipped at minimum size'
    assert page.start_button.winfo_rooty()+page.start_button.winfo_height()<=root.winfo_rooty()+root.winfo_height()
    page.video_options['name_threshold'].set('100')
    page.start_button.invoke();wait(lambda:not page.busy)
    assert not page.result['similar_groups'] and len(page.result['exact_groups'])==1
    page.video_options['name_threshold'].set('90');page.save()
    assert json.loads(page.settings_file.read_text(encoding='utf-8'))['name_threshold']==90
    # Check Stop using a waiting probe so cancellation is deterministic; normal
    # metadata and file scans above used the actual bundled MPV, not mocks.
    from .local_tools.video_duplicates import core
    def blocked(paths,cancel,progress):
        cancel.wait(10);core.check_cancel(cancel)
    with patch.object(core,'probe_videos',side_effect=blocked):
        page.start_button.invoke();assert page.busy
        page.stop_button.invoke();wait(lambda:not page.busy)
        assert page.result is None and '已停止' in page.status.get()
    assert first.is_file() and second.is_file()
    for key,label in [('scripts','Funscript'),('videos','视频'),('folders','文件夹组数'),('videos','视频')]*3:
        shell.pages[shell.current].controller.kind_selector._buttons_dict[label].invoke()
        root.update()
        assert shell.current==key and shell.pages[key].controller.kind_selector.get()==label
    settle=time.monotonic()+.4
    while time.monotonic()<settle:
        root.update();time.sleep(.01)
    # Actual event-loop responsiveness while indexing 10,000 same-length names.
    rows=[dict(path=f'E:/fixture/Aurora Moonlight Work {n//2:05d} '+('720p.mp4' if n%2 else '1080p.mp4'),
               duration=100,size=1000+n,width=160,height=120,fps=20) for n in range(10000)]
    result={};finished=threading.Event()
    def work():
        try:
            begin=time.perf_counter();groups,pairs=similar_groups(rows)
            result.update(groups=len(groups),pairs=pairs,seconds=round(time.perf_counter()-begin,3))
        except Exception as exc:
            result['error']=repr(exc)
        finally:finished.set()
    gaps=[];last=time.monotonic()
    def tick():
        nonlocal last
        now=time.monotonic();gaps.append(now-last);last=now
        if not finished.is_set():root.after(20,tick)
    root.after(20,tick);threading.Thread(target=work,daemon=True).start()
    wait(finished.is_set,timeout=30)
    assert 'error' not in result and result['groups']==result['pairs']==5000,result
    result['max_ui_pause']=round(max(gaps),3)
    result['ui_slow_pauses']=[round(gap,3) for gap in gaps if gap>.1]
    assert max(gaps)<1,result
    return dict(actual_video_headers=True,duration_and_name_controls=True,report_reload=True,
                exact_files_preserved=True,stop=True,minimum_size=True,navigation=True,benchmark=result)
