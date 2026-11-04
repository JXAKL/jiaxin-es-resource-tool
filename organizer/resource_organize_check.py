"""Exercise the new real Tk controls with isolated files and the shared index."""
import time
from pathlib import Path
from unittest.mock import patch

from .forum import topic_from_json
from .store import Store
from .resource_organize import MODE_LABELS, PAIR_LABELS
from .ui import ACCENT


def exercise(shell, root, folder):
    assert shell.show('resource_organize')
    page=shell.resource_organize
    root.geometry('1180x820');root.update()
    ordered=list(shell.nav.values())+[shell.back]
    for first,second in zip(ordered,ordered[1:]):
        assert first.winfo_ismapped() and first.winfo_rootx()+first.winfo_width()<=second.winfo_rootx(),'Navigation overlap'
    for widget in (page.start_button,page.local_button,page.execute_button,page.stop_button,page.use_button,page.report_button):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx()+widget.winfo_width()<=root.winfo_rootx()+root.winfo_width()
        assert widget.winfo_rooty()+widget.winfo_height()<=root.winfo_rooty()+root.winfo_height()
    base=Path(folder)/'resource-organize-ui';base.mkdir()
    mixed=base/'mixed';mixed.mkdir();single=base/'single';single.mkdir()
    title='Comprehensive Aurora Harbor'
    for suffix in ('.mp4','.pitch.funscript','.A0.funscript','(max).mp4',
                   '(max).funscript','.pitch(max).funscript','(v2).roll.funscript'):
        (mixed/(title+suffix)).write_bytes(b'{}')
    (single/'Comprehensive Dawn Morning.mp4').write_bytes(b'fixture')
    page.sources=[];page.add_source(str(mixed));root.update()
    page.add_source(str(single));root.update()
    page.source_mode.set(MODE_LABELS['videos']);page.source_pairing.set(PAIR_LABELS['external'])
    page.apply_button.invoke();root.update()
    assert page.sources[1]['mode']=='videos' and page.sources[1]['pairing']=='external'
    store=Store(page.state_dir/'catalog.sqlite3')
    for identity,name in [(99001,title),(99002,'Comprehensive Dawn Morning')]:
        store.upsert(topic_from_json(dict(id=identity,title=name,created_at='2023-12-31T18:00:00Z',
            post_stream=dict(posts=[dict(post_number=1,username='OrganizeFixture',
                cooked='<p>Free video and script. <a href="https://gofile.io/d/OrganizeFixture">script</a>'
                       '<a href="/uploads/preview.png">video script</a>'
                       '<a href="https://patreon.com/Fixture">video script</a></p>')]))))
    store.close()
    page.author.set('OrganizeFixture');page.output.set(str(base/'out'))
    def wait():
        end=time.monotonic()+20
        while page.busy and time.monotonic()<end:
            root.update();time.sleep(.01)
        assert not page.busy,page.status.get()
        root.update()
    with patch('organizer.resource_organize_ui.BrowserClient') as browser:
        page.local_button.invoke()
        assert page.busy and not shell.show('completion')
        assert shell.current=='resource_organize' and shell.nav['resource_organize'].cget('fg_color')==ACCENT
        wait();browser.assert_not_called()
    assert len(page.rows)==2,page.status.get()
    assert all(row['selected'] and row['month']=='2024/01' for row in page.rows.values()),page.rows
    assert [v.get() for v in page.layout.stat_values]==['2','2','0']
    page.tree.selection_set('0');page.select()
    assert 'max（2 文件）' in page.detail.get() and 'v2（1 文件）' in page.detail.get()
    assert len(page.rows['0']['files'])==7
    assert all(len(c['resources'])==1 for row in page.rows.values() for c in row['candidates'])
    page.rows['0']['candidates'][0]['resources'].append(dict(kind='video',url='https://cdn.example.com/preview.jpg'))
    page.reexport();wait()
    assert all(len(c['resources'])==1 for row in page.rows.values() for c in row['candidates'])
    assert all(row['selected'] for row in page.rows.values())
    page.tree.selection_set('1');page.select()
    assert '另一半在别处' in page.detail.get()
    page.toggle();page.select_button.invoke()
    assert not page.rows['1']['selected'],'Manual deselection overwritten'
    page.use_button.invoke();assert page.rows['1']['selected']
    for _ in range(3):
        assert shell.show('completion') and shell.show('resource_organize');root.update()
        assert len(page.rows)==2
    page.action.set('复制');page.execute_button.invoke();wait()
    assert all(row['status']=='复制完成' for row in page.rows.values()),page.status.get()
    assert len(list((base/'out/OrganizeFixture/2024/01').iterdir()))==8
    assert len(list(mixed.iterdir()))==7 and len(list(single.iterdir()))==1
    report=Path(page.current_output)
    with patch('organizer.resource_organize_ui.os.startfile') as opened:
        page.report_button.invoke();opened.assert_called_once_with(report/'资源综合整理报告.html')
    assert (report/'下载用链接/gofile.io.txt').is_file()
    page.save();page.sources=[];page._load()
    assert len(page.sources)==2 and page.sources[1]['pairing']=='external'
    videos=(base/'linked-videos').resolve();videos.mkdir();scripts=(base/'linked-scripts').resolve();scripts.mkdir()
    (videos/(title+'.mp4')).write_bytes(b'video')
    for name in (title+'.pitch.funscript',title+'.A0.funscript','Unrelated Other Work.funscript'):
        (scripts/name).write_bytes(b'{}')
    page.sources=[];page.render_sources()
    page.source_mode.set(MODE_LABELS['videos']);page.add_source(str(videos));root.update()
    page.source_mode.set(MODE_LABELS['scripts']);page.add_source(str(scripts));root.update()
    page.source_tree.selection_set('0');page.select_source();root.update()
    canvas=page.layout.settings._parent_canvas
    top=canvas.canvasy(0)+page.partner_control.winfo_rooty()-canvas.winfo_rooty()
    canvas.yview_moveto(max(0,top-12)/float(canvas.bbox('all')[3]));root.update()
    for widget in (page.partner_control,page.link_button,page.swap_button,page.unlink_button):
        assert widget.winfo_rooty()>=canvas.winfo_rooty()
        assert widget.winfo_rooty()+widget.winfo_height()<=canvas.winfo_rooty()+canvas.winfo_height()
        assert widget.winfo_rootx()+widget.winfo_width()<=canvas.winfo_rootx()+canvas.winfo_width()
    page.link_button.invoke();root.update()
    assert page.source_tree.get_children()==('0',)
    assert str(videos) in page.source_tree.item('0','values')[0] and str(scripts) in page.source_tree.item('0','values')[0]
    page.swap_button.invoke();root.update()
    assert page.source_tree.get_children()==('1',) and page.sources[1]['partner']==str(videos)
    page.execute_button.invoke();root.update()
    assert not page.busy and '重新生成预览' in page.status.get()
    page.swap_button.invoke();root.update()
    assert page.source_tree.get_children()==('0',) and page.sources[0]['partner']==str(scripts)
    page.unlink_button.invoke();root.update()
    assert len(page.source_tree.get_children())==2
    page.link_button.invoke();root.update()
    page.save();page.sources=[];page._load();page.render_sources();root.update()
    assert len(page.source_tree.get_children())==1 and page.sources[0]['partner']==str(scripts)
    page.output.set(str(base/'linked-out'))
    page.local_button.invoke()
    assert all(w.cget('state')=='disabled' for w in (page.link_button,page.swap_button,page.unlink_button))
    wait()
    assert len(page.rows)==1,page.status.get()
    linked=next(iter(page.rows.values()))
    assert linked['selected'] and len(linked['files'])==3,linked
    page.execute_button.invoke();wait()
    assert next(iter(page.rows.values()))['status']=='复制完成',page.status.get()
    assert len(list((base/'linked-out/OrganizeFixture/2024/01').iterdir()))==3
    assert (scripts/'Unrelated Other Work.funscript').is_file()
    page.source_tree.selection_set('0');page.remove_button.invoke();root.update()
    assert not page.sources and len(list(scripts.iterdir()))==3
    return dict(minimum_size=True,multi_source_settings=True,cached_preview=True,manual_choices=True,
                busy_navigation=True,dated_copy=True,all_axes_preserved=True,reports=True,settings_persisted=True,
                linked_sources=True,swap_primary=True,unlink=True,selective_counterparts=True)
