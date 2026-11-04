"""Real Tk verification of module-only help, navigation and nonblocking reading."""
from .manual_content import MANUALS, manual


def exercise(shell, root):
    root.geometry('1180x820'); root.update()
    covered = []
    for key in ('author', 'pair', 'scripts', 'videos', 'folders', 'collector',
                'search_scraper', 'completion', 'resource_organize', 'downloads'):
        assert shell.show(key)
        shell.open_manual(); root.update()
        pane = shell.manual_panel
        title, sections = manual(key)
        assert pane.visible and pane.key == key
        assert pane.title.cget('text') == title+' · 使用说明'
        expected = ''.join(heading+'\n'+body.strip()+'\n\n' for heading, body in sections)
        assert pane.document.get('1.0','end-1c') == expected
        assert pane.document.cget('state') == 'disabled'
        assert sum(page.winfo_ismapped() for page in shell.pages.values()) == 1
        assert not shell.overlays, 'Reading help must not push a task surface'
        for widget in (shell.manual_button, pane.frame, pane.close_button, pane.search,
                       pane.find_button, pane.toc, pane.text):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx() >= root.winfo_rootx(), key
            assert widget.winfo_rootx()+widget.winfo_width() <= root.winfo_rootx()+root.winfo_width(), key
            assert widget.winfo_rooty()+widget.winfo_height() <= root.winfo_rooty()+root.winfo_height(), key
        pane.section.set(next(reversed(pane.anchors)))
        pane.jump(pane.section.get()); root.update()
        assert pane.document.yview()[0] > 0
        pane.query.set('模块'); pane.find_next(); root.update()
        assert pane.matches and '1 /' in pane.note.get()
        pane.find_next()
        assert pane.match_index == 1 % len(pane.matches)
        pane.query.set('ThisExactTextDoesNotExist2123'); pane.find_next()
        assert '未找到' in pane.note.get()
        covered.append(key)
    page = shell.downloads
    page.busy = True
    try:
        previous = shell.displayed
        assert not shell.show('pair')
        assert shell.manual_panel.key == 'downloads'
        shell.manual_button.invoke(); root.update()
        assert not shell.manual_panel.visible and shell.displayed is previous
        shell.manual_button.invoke(); root.update()
        assert shell.manual_panel.visible and page.busy
        assert shell.dismiss_manual() == 'break'
        assert not shell.manual_panel.visible and page.busy
    finally:
        page.busy = False
    for _ in range(3):
        shell.show('pair'); shell.open_manual(); root.update()
        shell.show('collector'); root.update()
        assert shell.manual_panel.key == 'collector'
        assert shell.nav['collector'].cget('fg_color') == '#c3efd6'
        shell.manual_panel.close_button.invoke(); root.update()
    assert not shell.manual_panel.visible and not shell.overlays
    return dict(module_only_pages=covered,manuals=len(MANUALS),read_only=True,
                chapter_jump=True,in_page_search=True,minimum_window=True,
                busy_reading=True,rejected_navigation_keeps_context=True,single_window=True)
