"""Drive actual Tk sash drags using isolated preferences and synthetic pages."""
import json
import tkinter as tk
from pathlib import Path

from .split_layout import SplitPane


def drag(root, split, delta=28, index=0, use_grip=False):
    root.update()
    old = split.sash_coord(index)
    x, y = old
    if split.axis == 'horizontal': x += 1; y = max(30, split.winfo_height()//2)
    else: y += 1; x = max(30, split.winfo_width()//2)
    assert split.identify(x, y), (split.key, old, x, y)
    target = split
    if use_grip:
        target = split.grips[index]
        x, y = target.winfo_width()//2, target.winfo_height()//2
    target.event_generate('<ButtonPress-1>', x=x, y=y)
    target.event_generate('<B1-Motion>', x=x+(delta if split.axis=='horizontal' else 0),
                         y=y+(delta if split.axis=='vertical' else 0))
    target.event_generate('<ButtonRelease-1>', x=x+(delta if split.axis=='horizontal' else 0),
                         y=y+(delta if split.axis=='vertical' else 0))
    root.update()
    new = split.sash_coord(index)
    assert abs(new[0]-old[0])+abs(new[1]-old[1])>=10, (split.key, old, new)
    assert split.key in root._es_split_values


def exercise(shell, root, folder):
    errors=[]
    previous_handler=root.report_callback_exception
    root.report_callback_exception=lambda *error:errors.append(error)
    root.geometry('1380x940');root.update()
    covered=[]
    for key in ('author','pair','scripts','videos','folders','collector','completion','resource_organize','downloads','search_scraper'):
        assert shell.show(key);root.update()
        controller=shell.pages[key].controller
        layout=getattr(controller,'layout',controller)
        split=layout.main_split
        drag(root,split,use_grip=True)
        position=split.sash_coord(0)
        assert shell.show('author' if key!='author' else 'pair')
        assert shell.show(key);root.update()
        assert split.sash_coord(0)==position, 'Navigation overwrote a deliberate drag'
        for name in ('detail_split','log_split'):
            if hasattr(layout,name):drag(root,getattr(layout,name),-22)
        covered.append(key)
    page=shell.search_scraper
    page.thumbnails.set(True);page.preview_controls();root.update()
    split=page.preview.split
    drag(root,split,25,0);drag(root,split,-25,1)
    saved=dict(root._es_split_values[split.key])
    page.records={};page.thumbnails.set(False);page.preview_controls();root.update()
    assert len(split.panes())==2 and root._es_split_values[split.key]==saved
    page.thumbnails.set(True);page.preview_controls();root.update()
    assert len(split.panes())==3
    root.geometry('1180x820');root.update()
    for key in covered:
        assert shell.show(key);root.update()
        controller=shell.pages[key].controller
        tree=getattr(controller,'tree',None)
        if tree is not None:
            assert tree.winfo_ismapped() and tree.winfo_height()>50, (key,tree.winfo_geometry())
            assert tree.winfo_rootx()+tree.winfo_width()<=root.winfo_rootx()+root.winfo_width()
    assert shell.show('search_scraper');root.update()
    path=Path(root._es_layout_path)
    assert path.is_file() and json.loads(path.read_text(encoding='utf-8'))
    # Fresh Tk instance reads the saved file, like restarting the application.
    probe=tk.Toplevel(root);probe._es_layout_path=path;probe.geometry('1380x400')
    restored=SplitPane(probe,'作者归类/左右');restored.pack(fill='both',expand=True)
    restored.add_panel(tk.Frame(restored),'0',324,280)
    restored.add_panel(tk.Frame(restored),'1',None,420)
    root.update()
    expected=json.loads(path.read_text(encoding='utf-8'))['作者归类/左右']['0']
    assert abs(restored.sash_coord(0)[0]/1372-expected)<.02, (restored.sash_coord(0), expected)
    probe.destroy()
    shell.reset_panes();root.update()
    assert json.loads(path.read_text(encoding='utf-8'))=={}
    for _ in range(3):
        shell.show('collector');shell.show('search_scraper');root.update()
    page.busy=True
    try:
        assert not shell.show('pair') and shell.current=='search_scraper'
    finally:page.busy=False
    root.report_callback_exception=previous_handler
    assert not errors, errors
    return dict(native_drag_pages=covered,details_and_logs=True,search_three_columns=True,
                navigation_retains_sizes=True,restart_restores=True,preview_toggle=True,
                minimum_window=True,reset=True,busy_navigation=True,rounded_grip_drag=True)
