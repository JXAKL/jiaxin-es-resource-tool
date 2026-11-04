"""Exercise actual merged views with isolated state and synthetic media."""
import json
from copy import deepcopy
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import patch

import app
import organizer.workspace as workspace


def exercise_pair_selection(shell, root, folder):
    assert shell.show('pair')
    page = shell.pair
    assert page.auto_select.get() is True
    rows = [dict(video='Synthetic work.mp4',funscript='Synthetic work.funscript',
                 video_path='fixture.mp4',funscript_path='fixture.funscript',name_score=98,
                 duration_score=0,total=98,status='高置信',auto_eligible=True),
            dict(video='Review.mp4',funscript='Review.funscript',video_path='review.mp4',
                 funscript_path='review.funscript',name_score=90,duration_score=100,total=96,
                 status='多脚本候选待核对',auto_eligible=False)]
    from .local_tools import smart_match
    with patch.object(smart_match,'match_files',return_value=rows), \
            patch.object(smart_match,'grade_batch',return_value=None), \
            patch.object(smart_match,'process_match',side_effect=AssertionError('Preview must not copy/move files')):
        page.worker('', '', '', '',95,80,True,True,False,False,False,auto_select=True)
        page.process_queue()
        root.update()
    assert rows[0]['selected'] and not rows[1].get('selected')
    first = next(i for i,r in page.item_rows.items() if r is rows[0])
    page.toggle_item(first)
    page.select_high_button.invoke();root.update()
    assert not rows[0]['selected'], 'Manual deselection was overwritten'
    assert '已勾选 0' in page.selection_summary.get()
    page.populate_tree([{**rows[0], 'selected':False, 'selection_touched':False}]);root.update()
    page.select_high_button.invoke();root.update()
    assert page.rows[0]['selected']
    assert shell.show('author') and shell.show('pair')
    assert page.rows[0]['selected']
    page.auto_select.set(False)
    shell.save();page.auto_select.set(True);shell.load_pair_settings()
    assert page.auto_select.get() is False
    page.auto_select.set(True)
    root.geometry('1180x820');root.update()
    for control in (page.select_high_button,page.process_button):
        assert control.winfo_ismapped()
        assert control.winfo_rootx()+control.winfo_width() <= root.winfo_rootx()+root.winfo_width()
    page.populate_tree([]);root.update()


def exercise_folder_groups(shell, root, folder):
    """Drive actual controls and worker completion using disposable files only."""
    assert shell.show('folders')
    root.update()
    page = shell.folders
    fixture = folder / 'folder-fixture'
    source, target = fixture/'source', fixture/'target'
    for name in ('Few/work.mp4','Few/scripts/work.funscript','Few/scripts/work.pitch.funscript',
                 'Many/one.mp4','Many/two.mp4','Notes/readme.txt'):
        file = source/name; file.parent.mkdir(parents=True, exist_ok=True); file.write_bytes(b'test')
    page.root.set(str(source)); page.target.set(str(target)); page.threshold.set('2')
    page.scan_button.invoke()
    def wait():
        deadline = time.monotonic()+15
        while page.busy and time.monotonic()<deadline:
            root.update(); time.sleep(.01)
        assert not page.busy, page.status.get()
        root.update()
    wait()
    assert len(page.rows)==3, page.status.get()
    few = next(r for r in page.rows if Path(r.source).name=='Few')
    assert few.groups==few.paired==1 and few.selected
    assert sum(r.selected for r in page.rows)==1
    page.paired.set(True); page.refilter()
    assert sum(r.selected for r in page.rows)==1
    page.zero.set(True); page.refilter()
    assert sum(r.selected for r in page.rows)==3
    page.zero.set(False); page.paired.set(False); page.refilter()
    page.clear(); assert page.move_button.cget('state')=='disabled'
    page.refilter()
    root.geometry('1180x820'); root.update()
    for widget in (page.scan_button,page.move_button,page.undo_button,page.tree):
        assert widget.winfo_ismapped()
        assert widget.winfo_rooty()+widget.winfo_height()<=root.winfo_rooty()+root.winfo_height()
    viewport = page.layout.settings._parent_canvas
    for widget in (page.paired_switch, page.zero_switch, page.count_hint, *page.inputs):
        assert widget.winfo_rooty() >= viewport.winfo_rooty(), 'Folder setting above viewport'
        assert widget.winfo_rooty()+widget.winfo_height() <= viewport.winfo_rooty()+viewport.winfo_height(), 'Folder setting clipped below viewport'
        assert widget.winfo_rootx()+widget.winfo_width() <= viewport.winfo_rootx()+viewport.winfo_width(), 'Folder setting clipped horizontally'
    page.move_button.invoke(); wait()
    assert not (source/'Few').exists() and (target/'Few'/'scripts'/'work.pitch.funscript').is_file(), page.status.get()
    assert (source/'Many'/'one.mp4').is_file() and (source/'Notes'/'readme.txt').is_file()
    journal = next((page.state/'folder-moves').glob('folders-*.jsonl'))
    with patch('organizer.folder_groups_ui.filedialog.askopenfilename',return_value=str(journal)):
        page.undo_button.invoke()
    wait()
    assert (source/'Few'/'work.mp4').is_file() and not (target/'Few').exists(), page.status.get()
    page.scan_button.invoke(); wait()
    page.target.set(str(fixture/'other'))
    assert not page.rows and page.move_button.cget('state')=='disabled'
    assert shell.show('scripts') and shell.show('duplicates')
    assert shell.current=='scripts'
    for key, label in [('videos','视频'),('folders','文件夹组数'),('scripts','Funscript')]*4:
        current = shell.pages[shell.current].controller.kind_selector
        current._buttons_dict[label].invoke()
        assert shell.current == key
        assert shell.pages[key].controller.kind_selector.get() == label
    shell.show('folders'); page.busy = True
    try:
        page.kind_selector._buttons_dict['视频'].invoke()
        assert shell.current == 'folders' and page.kind_selector.get() == '文件夹组数'
    finally:
        page.busy = False


def exercise(root, folder, visible=False):
    app.STATE = workspace.STATE = folder / 'state'
    app.STATE.mkdir(exist_ok=True)
    failures = []
    root.report_callback_exception = lambda *args: failures.append(args)
    shell = workspace.Workspace(root)
    if not visible:
        root.attributes('-alpha', 0)
    root.update()
    for key in ('pair', 'scripts', 'videos', 'folders', 'downloads', 'author', 'pair'):
        assert shell.show(key)
        root.update()
        assert root.title() == workspace.APP_NAME
        assert sum(bool(page.winfo_ismapped()) for page in shell.pages.values()) == 1
    exercise_folder_groups(shell, root, folder)
    from .video_duplicate_check import exercise as exercise_video_duplicates
    shell.video_duplicate_validation = exercise_video_duplicates(shell, root, folder)
    assert shell.show('pair')
    assert shell.pair.auto_high.get() is False
    assert shell.pair.overwrite.get() is False
    assert shell.pair.operation_mode.get() == 'copy'
    exercise_pair_selection(shell, root, folder)
    from .pair_output_check import exercise as exercise_pair_output
    shell.pair_output_validation = exercise_pair_output(shell, root, folder)
    exercise_completion(shell, root, folder)
    from .resource_organize_check import exercise as exercise_organize
    shell.organize_validation = exercise_organize(shell, root, folder)
    from .search_scraper_check import exercise_ui as exercise_search
    shell.search_validation = exercise_search(shell, root, folder)
    from .search_thumbnail_check import exercise_ui as exercise_thumbnails
    shell.thumbnail_validation = exercise_thumbnails(shell, root, folder)
    exercise_download_upgrade(shell, root)
    assert shell.show('pair')
    root.geometry('1180x820')
    root.update()
    from .model import Candidate, FileGroup, Match
    sample_match = Match(FileGroup('batch-check', 'Synthetic author work'), '多作者待核对', [
        Candidate('Lower', 1, 'Synthetic author work', '', .90, '', verified=True, eligible=True),
        Candidate('Higher', 2, 'Synthetic author work', '', .95, '', verified=True, eligible=True)])
    confirmed = Match(FileGroup('confirmed', 'Confirmed work'), '可归类', [], 'Confirmed', True)
    checked_multi = Match(FileGroup('checked-multi', 'Already checked work'), '多作者待核对',
                          sample_match.candidates, 'Chosen', True)
    unchecked_manual = Match(FileGroup('unchecked-manual', 'Unchecked decision'), '人工指定',
                             sample_match.candidates, 'Manual', False, True)
    ordinary_review = Match(FileGroup('ordinary-review', 'Ordinary review'), '待核对', sample_match.candidates)
    tied = Match(FileGroup('tied', 'Tied work'), '多作者待核对', [
        Candidate('First', 3, '', '', .95, '', verified=True, eligible=True),
        Candidate('Second', 4, '', '', .95, '', verified=True, eligible=True)])
    preserved = [confirmed, checked_multi, unchecked_manual, ordinary_review]
    before = deepcopy(preserved)
    shell.author.matches = [sample_match, *preserved, tied]
    shell.author.filter_var.set('可归类')
    shell.author.render()
    assert shell.author.best_author_btn.cget('state') == 'disabled'
    shell.author.choose_best_authors()
    assert not sample_match.selected and preserved == before
    shell.author.filter_var.set('待核对')
    shell.author.render()
    shell.author.best_author_btn.invoke()
    assert sample_match.author == 'Higher' and sample_match.selected and sample_match.manual
    assert preserved == before and not tied.selected
    assert '新增勾选 1 组' in shell.author.log.get('1.0', 'end')
    assert '当前筛选剩余待核对 3 组' in shell.author.log.get('1.0', 'end')
    assert '待核对（多作者 2）' in shell.author.summary.get()
    shell.author.filter_var.set('全部')
    shell.author.render()
    root.update()
    shell.author.tree.selection_set('0')
    shell.author.details()
    root.update()
    assert len(shell.overlays) == 1
    shell.go_back()
    assert shell.show('pair')
    root.update()
    assert sum(bool(page.winfo_ismapped()) for page in shell.pages.values()) == 1
    for control in (shell.pair.start_button, shell.pair.process_button):
        assert control.winfo_ismapped()
        assert control.winfo_rooty() + control.winfo_height() <= root.winfo_rooty() + root.winfo_height(), 'Control clipped at minimum size'
    root.geometry('1380x940')
    root.update()
    shell.pair.intensity_settings()
    root.update()
    assert shell.pair.intensity_panel.winfo_manager() == 'pack'
    assert not shell.overlays
    shell.pair.intensity_settings()
    assert not shell.pair.intensity_panel.winfo_manager()
    assert not hasattr(shell.pair, 'open_heatmap')
    shell.pair.running = True
    assert not shell.show('author')
    shell.pair.running = False
    assert shell.show('collector')
    root.update()
    assert shell.collector.type_vars['gofile'].get()
    collector = shell.collector
    assert collector.title_keyword_entry.cget('state') == 'disabled'
    collector.title_filter_switch.toggle()
    assert collector.title_keyword_entry.cget('state') == 'normal'
    collector.title_keyword.set('[Multi Axis]'); collector.save()
    collector.title_keyword.set(''); collector.title_filter_enabled.set(False); collector._load()
    assert collector.title_keyword.get() == '[Multi Axis]' and collector.title_filter_enabled.get()
    collector.title_filter_enabled.set(False); collector.title_keyword.set(''); collector.update_title_controls()
    shell.collector.publish(dict(records=[dict(id=901, title='Queue fixture', date='', url='https://example.invalid/901',
        links={'gofile': ['https://gofile.io/d/QueueFixture']})], discovered=1, status='本机测试'))
    shell.collector.tree.selection_set('901')
    shell.collector.queue_downloads()
    assert shell.current == 'downloads' and len(shell.downloads.items) == 1
    assert shell.downloads.items[0].status == '待处理' and not shell.downloads.busy
    completed = shell.downloads.items[0]
    completed.status, completed.detail = '完成', 'Already verified'
    records = [dict(id=902, author='Fixture', title='Shared resources', date='', url='https://discuss.eroscripts.com/t/902',
        links={'hanime':['https://hanime1.me/watch?v=123'], 'iwara':['https://iwara.tv/video/Test'],
               'rule34':['https://rule34video.com/video/123/test/'], 'pixeldrain':['https://pixeldrain.com/u/Test'],
               'mega_file':['https://mega.nz/file/Test#FullSecret'], 'gofile':['https://gofile.io/d/QueueFixture']}),
        dict(id=903, author='Other', title='Other post', date='', url='https://discuss.eroscripts.com/t/903',
             links={'iwara':['https://www.iwara.tv/video/Test'], 'mediafire':['https://mediafire.com/file/Fixture']})]
    assert shell.show('collector')
    shell.collector.publish(dict(records=records, discovered=2, status='本机测试'))
    root.update()
    shell.collector.tree.selection_set('902', '903')
    shell.collector.queue_downloads()
    assert shell.current == 'downloads' and len(shell.downloads.items) == 6
    assert completed.status == '完成' and completed.detail == 'Already verified'
    assert len(completed.sources) == 2 and not shell.downloads.busy
    iwara = shell.downloads.by_url['https://iwara.tv/video/Test']
    assert [row['author'] for row in iwara.sources] == ['Fixture', 'Other']
    shell.downloads.tree.selection_set(iwara.key)
    shell.downloads.select()
    assert '来源：Fixture' in shell.downloads.detail.get()
    assert '1 个不支持的链接' in shell.downloads.status.get()
    assert shell.show('collector')
    shell.collector.queue_downloads(all_records=True)
    assert len(shell.downloads.items) == 6 and len(iwara.sources) == 2
    imported = folder / 'Fixture_Links_Data.json'
    imported.write_text(json.dumps(dict(records=records)), encoding='utf-8')
    with patch('organizer.downloads.ui.filedialog.askopenfilename', return_value=str(imported)):
        shell.downloads.import_txt()
    assert len(shell.downloads.items) == 6 and completed.status == '完成'
    from .downloads.model import load_queue
    restored, _ = load_queue(app.STATE / 'download-queue.json')
    assert restored == shell.downloads.items
    assert shell.show('pair')
    assert not any(isinstance(w, tk.Toplevel) for w in root.winfo_children())
    from organizer.video_fixture import make_video
    from organizer.local_tools.playback_review import position_at
    video = folder / 'sample.avi'
    make_video(video)
    script = folder / 'sample.funscript'
    script.write_text(json.dumps({'actions': [{'at': i*500, 'pos': 100*(i % 2)} for i in range(17)]}))
    from organizer.local_tools.playback_review import PlaybackReview
    player = PlaybackReview(shell.pages['pair'], str(script), str(video), shell.pair.accept_review)
    shell.open_manual(); root.update()
    assert shell.manual_panel.key == 'playback'
    shell.manual_panel.hide()
    process = player.player.process
    def wait_for(predicate, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            root.update()
            if predicate():
                return
            time.sleep(.015)
        raise AssertionError('Timed out: ' + player.status.get())
    try:
        wait_for(lambda: player.ready and abs(player.duration-8) < .2)
        from .split_layout_check import drag
        drag(root, player.playback_split, -30)
        assert player.video_area.winfo_ismapped() and player.curve.winfo_height() >= 50
        assert abs(position_at(player.script, .25)-50) < .01
        assert len(player.curve.find_all()) > 5
        player.toggle()
        wait_for(lambda: player.seconds > .7)
        player.toggle()
        wait_for(lambda: player.paused)
        player.seek_to(4)
        wait_for(lambda: abs(player.seconds-4) < .2)
        player.command('set_property', 'speed', .5)
        wait_for(lambda: player.actual_speed == .5)
        player.confirm()
        assert len(shell.pair.rows) == 1
        assert shell.pair.rows[0]['status'] == '人工确认'
        shell.pair.output_dir.set(str(folder / 'paired-output'))
        shell.pair.process_selected()
        wait_for(lambda: not shell.pair.running)
        row = shell.pair.rows[0]
        assert row['status'] == '已处理', row
        assert Path(row['output_path']).is_file() and video.is_file() and script.is_file()
        shell.go_back()
        wait_for(lambda: process.poll() is not None)
        assert not shell.overlays
        assert shell.show('author') and shell.show('pair')
        assert len(shell.pair.rows) == 1
        assert not failures, failures
        from organizer.local_tools.funscript_duplicates.core import scan_folders, report_text
        from organizer.local_tools.funscript_duplicates.report_io import load_report
        from organizer.local_tools.video_duplicates import backend as video_backend
        from organizer.local_tools.funscript_duplicates.organize import execute_plan
        import shutil
        source, reference = folder / 'duplicates-source', folder / 'duplicates-reference'
        source.mkdir()
        (reference / 'author' / 'series').mkdir(parents=True)
        shutil.copy2(script, source / 'renamed.funscript')
        shutil.copy2(script, reference / 'author' / 'series' / 'reference.funscript')
        shutil.copy2(video, source / 'renamed.avi')
        shutil.copy2(video, reference / 'author' / 'series' / 'reference.avi')
        for key, backend in (('scripts', None), ('videos', video_backend)):
            result = (backend.scan_folders if backend else scan_folders)([source, reference])
            assert len(result['exact_groups']) == 1
            report_file = folder / (key + '.txt')
            report_file.write_text((backend.report_text if backend else report_text)(result), encoding='utf-8')
            loaded = (backend.load_report if backend else load_report)(report_file)
            shell.show(key)
            duplicate = shell.pages[key].controller
            duplicate.result = loaded
            duplicate.open_organize()
            root.update()
            organize = shell.overlays[-1].controller
            shell.open_manual(); root.update()
            assert shell.manual_panel.key == 'duplicate_organize'
            shell.manual_panel.hide()
            organize.output.set(str(folder / (key + '-mirror')))
            organize.preview()
            wait_for(lambda: not organize.busy)
            assert len(organize.plan) == 1
            # Actual moves operate only on the synthetic fixture, preserving the reference.
            plan = organize.plan
            result_move = (backend.execute_plan if backend else execute_plan)(plan)
            assert result_move['moved'] == 1, result_move
            assert all(Path(record['path']).is_file() for record in loaded['exact_groups'][0] if 'duplicates-reference' in record['path'])
            shell.go_back()
        assert not failures, failures
        shell.save()
        assert (app.STATE / 'settings.json').is_file()
        assert (app.STATE / 'local-settings.json').is_file()
        return shell
    finally:
        if not player.closed:
            player.close()


def exercise_download_upgrade(shell, root):
    from .downloads.ui import QUALITY, MEGA_MODES
    from unittest.mock import patch
    assert shell.show('downloads')
    page=shell.downloads;root.geometry('1180x820');root.update()
    for widget in (page.selected_button,page.start_button,page.stop_button):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx()+widget.winfo_width()<=root.winfo_rootx()+root.winfo_width()
        assert widget.winfo_rooty()+widget.winfo_height()<=root.winfo_rooty()+root.winfo_height()
    canvas=page.layout.settings._parent_canvas
    for widget in (page.quality_control,page.proxy_control,page.rate_control,page.mega_control,page.connections_control):
        top=canvas.canvasy(0)+widget.winfo_rooty()-canvas.winfo_rooty()
        canvas.yview_moveto(max(0,top-12)/float(canvas.bbox('all')[3]));root.update()
        assert widget.winfo_rooty()>=canvas.winfo_rooty()
        assert widget.winfo_rooty()+widget.winfo_height()<=canvas.winfo_rooty()+canvas.winfo_height()
    page.quality.set(QUALITY['720']);page.rate_limit.set('2.5');page.mega_mode.set(MEGA_MODES['local'])
    page.mega_connections.set('2');page.proxy.set('http://127.0.0.1:7890')
    options=page.options();assert options['quality']=='720' and options['rateLimit']==2621440
    page.busy=True;page.controls(True)
    assert not shell.show('collector') and shell.current=='downloads'
    assert page.selected_button.cget('state')=='disabled'
    page.busy=False;page.controls(False)
    for _ in range(3):assert shell.show('collector') and shell.show('downloads')
    assert page.quality.get()==QUALITY['720']
    page.quality.set(QUALITY['best']);page.rate_limit.set('0');page.proxy.set('');page.save()


def run_local_check(folder):
    local = Path(folder) / 'local-integration'
    local.mkdir(exist_ok=True)
    root = tk.Tk()
    try:
        shell = exercise(root, local)
        from .manual_check import exercise as exercise_manual
        manual_validation = exercise_manual(shell, root)
        from .split_layout_check import exercise as exercise_splits
        split_validation = exercise_splits(shell, root, local)
        from .visual_layout_check import exercise as exercise_visual
        visual_validation = exercise_visual(shell, root)
        shell.close()
        return {'navigation': True, 'single_window': True, 'busy_guard': True, 'resizable_layout': split_validation,
                'contextual_manual': manual_validation,
                'visual_layout': visual_validation,
                'state_retained': True, 'mpv_actual_playback': True,
                'mpv_pause_seek_speed': True, 'manual_confirmation': True,
                'player_cleanup': True, 'dark_theme': True, 'copy_and_intensity': True,
                'duplicate_reports_and_guarded_moves': True, 'highest_author_button': True,
                'video_duration_name_and_information': shell.video_duplicate_validation,
                'highest_author_preserves_existing_choices': True,
                'highest_author_filtered_scope_and_counts': True,
                'collection_multi_select_and_all_to_downloads': True,
                'download_quality_proxy_rate_mega_and_selection_controls': True,
                'collected_resource_json_import_and_provenance': True,
                'folder_group_count_filter_move_undo': True,
                'duplicate_navigation_highlight_and_busy_guard': True,
                'collection_title_filter_controls_and_persistence': True,
                'existing_download_outcomes_preserved': True,
                'pair_auto_selection_and_manual_choice_protection': True,
                'pair_selection_setting_persistence': True, 'pair_group_output': shell.pair_output_validation,
                'completion_controls_navigation_and_local_scan': True,
                'resource_comprehensive_organize': shell.organize_validation,
                'search_scraper': shell.search_validation, 'search_thumbnails': shell.thumbnail_validation}
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def exercise_completion(shell, root, folder):
    assert shell.show('completion')
    page = shell.completion
    root.geometry('1180x820'); root.update()
    ordered = [shell.nav[key] for key in ('author','pair','duplicates','collector','completion','downloads')] + [shell.back]
    for first, second in zip(ordered, ordered[1:]):
        assert first.winfo_ismapped() and first.winfo_rootx()+first.winfo_width() <= second.winfo_rootx(), '导航被挤压或重叠'
    for control in (page.start_button,page.scan_button,page.open_button,page.export_button,page.links_button):
        assert control.winfo_ismapped()
        assert control.winfo_rootx()+control.winfo_width() <= root.winfo_rootx()+root.winfo_width()
    media = Path(folder)/'completion-ui-media'; media.mkdir(exist_ok=True)
    (media/'Aurora Harbor Midnight.mp4').write_bytes(b'fixture')
    (media/'Dawn Harbor Morning.pitch.funscript').write_text('{}')
    # Complete screenshot-style and extended channel families must not become
    # "missing video" rows; also cover axes without an unsuffixed main script.
    (media/'1080p60fps.mp4').write_bytes(b'fixture')
    for suffix in ('', '.pitch', '.roll', '.surge', '.sway', '.twist', '.valve'):
        (media/('1080p60fps'+suffix+'.funscript')).write_text('{}')
    (media/'Complete Channel Family.mp4').write_bytes(b'fixture')
    nested=media/'axes';nested.mkdir(exist_ok=True)
    for suffix in ('.L3','.A0','.A1','.A2','.V0','.V1','_suction',' [pump]','．ＶＡＬＶＥ'):
        (nested/('Complete Channel Family'+suffix+'.funscript')).write_text('{}')
    for suffix in ('(max).mp4','(v2).mp4','(max).funscript','.pitch(max).funscript','(v2).roll.funscript'):
        (nested/('Complete Channel Family'+suffix)).write_text('{}')
    for suffix in ('(max).funscript','.pitch(max).funscript','(v2).roll.funscript'):
        (nested/('Dawn Harbor Morning'+suffix)).write_text('{}')
    page.folder.set(str(media)); page.author.set('Fixture'); page.threshold.set('91'); page.save()
    page.author.set(''); page.threshold.set('85'); page._load()
    assert page.author.get()=='Fixture' and page.threshold.get()=='91'
    with patch('organizer.completion_ui.BrowserClient') as browser:
        page.scan_button.invoke()
        assert page.busy and not shell.show('author')
        assert shell.current=='completion'
        deadline=time.monotonic()+10
        while page.busy and time.monotonic()<deadline:
            root.update();time.sleep(.01)
        assert not page.busy
        browser.assert_not_called()
    root.update()
    assert len(page.rows)==2 and len(page.tree.get_children())==2
    assert page.layout.stat_values[0].get()=='4'
    assert all(row['title'] not in {'1080p60fps','Complete Channel Family'} for row in page.rows.values())
    page.tree.selection_set('0');page.select();assert '缺' in page.detail.get()
    variant_row=next(key for key,row in page.rows.items() if row['title']=='Dawn Harbor Morning')
    page.tree.selection_set(variant_row);page.select()
    assert 'max（2 文件）' in page.detail.get() and 'v2（1 文件）' in page.detail.get()
    for _ in range(4):
        assert shell.show('collector'); assert shell.show('completion');root.update()
        assert len(page.rows)==2 and shell.nav['completion'].cget('fg_color')==workspace.ACCENT
    report=Path(folder)/'completion-ui-report';report.mkdir(exist_ok=True)
    (report/'资源补齐报告.html').write_text('<html>synthetic</html>')
    page.current_output=str(report)
    with patch('organizer.completion_ui.os.startfile') as opened:
        page.open_button.invoke();opened.assert_called_once_with(report/'资源补齐报告.html')
    from .completion import build_rows
    from .forum import topic_from_json
    from .model import FileGroup, LocalFile
    topic=topic_from_json(dict(id=901,title='Aurora Harbor Midnight',post_stream=dict(posts=[dict(
        post_number=1,username='Fixture',cooked='<p>Free script <a href="https://gofile.io/d/CompletionExport">script</a></p>')])) )
    rows=build_rows([FileGroup('export-check','Aurora Harbor Midnight',[
        LocalFile(str(media/'Aurora Harbor Midnight.mp4'),7,1,'video')])],[topic],85)
    rows[0]['candidates'][0]['resources'] += [
        dict(kind='script',url='https://patreon.com/Fixture',scope='old report'),
        dict(kind='script',url='https://cdn.example.com/preview.png',scope='old report')]
    page.last_state=dict(author='Fixture',source=str(media),total=1,rows=rows,status='补齐报告完成',failed={},report_folder=str(report))
    page.export_scope.set('只导出缺脚本');page.export_downloads.set(True);page.include_review.set(False)
    with patch('organizer.completion_ui.BrowserClient') as browser:
        page.export_button.invoke()
        deadline=time.monotonic()+10
        while page.busy and time.monotonic()<deadline:
            root.update();time.sleep(.01)
        assert not page.busy
        browser.assert_not_called()
    exported=report/'下载用链接/gofile.io.txt'
    assert exported.read_text(encoding='utf-8').startswith('# 资源补齐')
    assert all('patreon' not in r['url'] and 'preview.png' not in r['url']
               for row in page.last_state['rows'] for c in row['candidates'] for r in c['resources'])
    assert not (report/'手动获取链接/patreon.com.txt').exists()
    with patch('organizer.completion_ui.os.startfile') as opened:
        page.links_button.invoke();opened.assert_called_once_with(report/'下载用链接')
    page.export_scope.set('全部缺失组');page.include_review.set(True);page.export_downloads.set(False)
    page._load()
    assert page.export_scope.get()=='只导出缺脚本' and page.export_downloads.get() and not page.include_review.get()
    assert shell.show('downloads')
    original_items = list(shell.downloads.items)
    try:
        with patch('organizer.downloads.ui.filedialog.askopenfilename',return_value=str(exported)):
            shell.downloads.import_txt()
        assert any(item.url=='https://gofile.io/d/CompletionExport' for item in shell.downloads.items)
    finally:
        shell.downloads.items = original_items
        shell.downloads.by_url = {item.url:item for item in original_items}
        shell.downloads.publish_all(); shell.downloads.stats(); shell.downloads.save()
    assert shell.show('completion')
    return True

