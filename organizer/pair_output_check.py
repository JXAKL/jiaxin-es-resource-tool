"""Drive real pairing controls with disposable, multi-axis file groups."""
import time
from pathlib import Path
from unittest.mock import patch


def exercise(shell, root, folder):
    assert shell.show('pair')
    page = shell.pair
    root.geometry('1180x820'); root.update()
    assert not page.include_scripts.get() and not page.separate_folders.get()
    assert page.separate_folders_switch.cget('state') == 'disabled'
    page.include_scripts_switch.toggle()
    page.separate_folders_switch.toggle()
    assert page.include_scripts.get() and page.separate_folders.get()
    page.include_scripts_switch.toggle()
    assert not page.separate_folders.get() and page.separate_folders_switch.cget('state') == 'disabled'
    page.include_scripts_switch.toggle(); page.separate_folders_switch.toggle()
    shell.save()
    page.include_scripts.set(False)
    shell.load_pair_settings()
    assert page.include_scripts.get() and page.separate_folders.get()
    assert shell.show('author') and shell.show('pair')
    page.set_buttons(True)
    assert page.include_scripts_switch.cget('state') == 'disabled'
    assert page.separate_folders_switch.cget('state') == 'disabled'
    assert not shell.show('author') and shell.current == 'pair'
    page.set_buttons(False)
    assert page.separate_folders_switch.cget('state') == 'normal'
    canvas = page.layout.settings._parent_canvas
    for control in (page.include_scripts_switch, page.separate_folders_switch, page.output_hint_label):
        root.update()
        top = canvas.canvasy(0) + control.winfo_rooty() - canvas.winfo_rooty()
        canvas.yview_moveto(max(0, top-12)/float(canvas.bbox('all')[3])); root.update()
        assert control.winfo_rootx() >= canvas.winfo_rootx()
        assert control.winfo_rootx()+control.winfo_width() <= canvas.winfo_rootx()+canvas.winfo_width(), 'New control clipped horizontally'
        assert control.winfo_rooty() >= canvas.winfo_rooty()
        assert control.winfo_rooty()+control.winfo_height() <= canvas.winfo_rooty()+canvas.winfo_height(), 'New control cannot be reached by scrolling'

    source, target = folder/'pair-output-source', folder/'pair-output-target'
    source.mkdir()
    video = source/'Harbor 1080p.mp4'; video.write_bytes(b'synthetic video')
    scripts = []
    for axis in ('', '.pitch', '.roll', '.twist'):
        p = source/f'Harbor{axis}.funscript'
        p.write_text('{"actions":[{"at":0,"pos":0},{"at":1000,"pos":100}]}')
        scripts.append(p)
    row = dict(video=video.name,funscript=scripts[0].name,video_path=str(video),
               funscript_path=str(scripts[0]),script_paths=[str(p) for p in scripts],
               status='高置信',selected=True,auto_eligible=True,name_score=100,duration_score=100,total=100)
    page.populate_tree([row]); root.update()
    page.output_dir.set(str(target)); page.output_csv.set(''); page.classify_intensity.set(False)
    page.operation_mode.set('move')
    assert page.process_button.cget('text') == '移动勾选组'
    page.process_button.invoke()
    deadline = time.monotonic()+10
    while page.running and time.monotonic() < deadline:
        root.update(); time.sleep(.01)
    assert not page.running and row['status'] == '已处理', row
    assert not video.exists() and all(not p.exists() for p in scripts)
    output = Path(row['output_path'])
    assert output.parent.name == 'Harbor 1080p'
    assert len(row['output_script_paths']) == 4
    assert all(Path(p).parent == output.parent and Path(p).is_file() for p in row['output_script_paths'])
    root.update()
    page.tree.selection_set(next(iter(page.item_rows)))
    page.show_details()
    assert str(output) in page.details.get()
    with patch('organizer.local_tools.playback_review.PlaybackReview') as player:
        page.open_playback()
        assert player.call_args.args[1:3] == (row['output_script_paths'][0],row['output_path'])
    page.playback_windows = []

    # The direct high-confidence branch must receive the same two options.
    from .local_tools import smart_match
    direct_video = source/'Direct.mp4'; direct_video.write_bytes(b'direct fixture')
    direct_script = source/'Direct.funscript'; direct_script.write_text('{}')
    direct = dict(row,video_path=str(direct_video),funscript_path=str(direct_script),
                  script_paths=[str(direct_script)],status='高置信',auto_eligible=True)
    with patch.object(smart_match,'match_files',return_value=[direct]), patch.object(smart_match,'grade_batch',return_value=None):
        page.worker('', '', str(target), '',95,80,True,True,True,False,False,
                    classify=False,include_scripts=True,separate_folders=True)
        page.process_queue(); root.update()
    assert direct['status'] == '已处理' and Path(direct['output_script_paths'][0]).is_file()
    assert direct_video.exists() and direct_script.exists()
    page.include_scripts.set(False); page.operation_mode.set('copy'); page.classify_intensity.set(True)
    page.output_dir.set(''); page.populate_tree([]); canvas.yview_moveto(0); root.update()
    return dict(dependent_switches=True,settings_roundtrip=True,minimum_window=True,
                busy_lock=True,whole_group_move=True,playback_after_move=True,direct_high_copy=True)
