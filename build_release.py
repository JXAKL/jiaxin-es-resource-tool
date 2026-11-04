"""Build a ready-to-run Windows folder. Archiving is left to the user."""
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from organizer.runtime import APP_NAME, APP_VERSION

ROOT = Path(__file__).resolve().parent
VERSION = APP_VERSION
NAME = APP_NAME
BUILD_NAME = f'{NAME}-{VERSION}'
DEST = ROOT.parent / BUILD_NAME
BROWSERS = ROOT / '.build-assets' / 'browsers'
PLAYER = ROOT / '.build-assets' / 'media' / 'mpv.exe'


def browser_folders():
    return [p for p in BROWSERS.iterdir() if p.is_dir() and p.name.startswith(('chromium-', 'ffmpeg-', 'winldd-'))]


def windows_version(version):
    parts = tuple(int(part) for part in version.split('.'))
    if not 1 <= len(parts) <= 4 or any(not 0 <= part <= 65535 for part in parts):
        raise ValueError('Windows version needs one to four 16-bit components')
    return parts + (0,) * (4 - len(parts))


def trim_runtime(destination):
    """Trim only the generated release, never source runtimes or user state."""
    root = destination.resolve()
    internal = root / '_internal'
    candidates = []
    for browser in (internal / 'browsers').glob('chromium-*/chrome-win64'):
        candidates.extend(p for p in (browser / 'locales').glob('*.pak')
                          if p.stem not in {'en-US', 'en-GB', 'zh-CN', 'zh-TW'})
        candidates.extend(browser / name for name in ('setup.exe', 'elevation_service.exe',
                                                       'elevated_tracing_service.exe', 'chrome_pwa_launcher.exe'))
    # TypeScript declarations and map files are development inputs, not runtime.
    package = internal / 'playwright' / 'driver' / 'package'
    candidates.extend(package.rglob('*.d.ts'))
    candidates.extend(package.rglob('*.map'))
    # PyInstaller also collects a second Chrome dependency at bundle root.
    duplicate = internal / 'chrome_elf.dll'
    if duplicate.exists():
        import hashlib
        digest = hashlib.sha256(duplicate.read_bytes()).digest()
        if any(hashlib.sha256(p.read_bytes()).digest() == digest
               for p in (internal / 'browsers').glob('chromium-*/chrome-win64/chrome_elf.dll')):
            candidates.append(duplicate)
    removed = []
    for file in candidates:
        if not file.is_file():
            continue
        if file.is_symlink() or not file.resolve().is_relative_to(internal.resolve()):
            raise ValueError(f'Unexpected release path: {file}')
        removed.append({'file': file.relative_to(root).as_posix(), 'bytes': file.stat().st_size})
        file.unlink()
    return removed


def main():
    assert sys.platform == 'win32' and platform.machine().upper() in ('AMD64', 'X86_64'), 'Build on Windows x64'
    assert list(BROWSERS.glob('chromium-*/chrome-win64/chrome.exe')), 'Install Chromium into .build-assets/browsers first'
    assert PLAYER.is_file(), 'Prepare .build-assets/media/mpv.exe first'
    # Browser cache metadata contains developer-machine paths and is not shipped.
    browser_data = []
    for folder in browser_folders():
        browser_data += ['--add-data', f'{folder}:browsers/{folder.name}']
    version_file = ROOT / 'build' / 'windows-version.txt'
    version_file.parent.mkdir(exist_ok=True)
    version_tuple = windows_version(VERSION)
    version_file.write_text(f'''VSVersionInfo(
      ffi=FixedFileInfo(filevers={version_tuple!r}, prodvers={version_tuple!r}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)),
      kids=[StringFileInfo([StringTable('080404B0', [
        StringStruct('FileDescription', {APP_NAME!r}), StringStruct('ProductName', {APP_NAME!r}),
        StringStruct('FileVersion', {VERSION!r}), StringStruct('ProductVersion', {VERSION!r}),
        StringStruct('OriginalFilename', {f'{NAME}.exe'!r})])]),
        VarFileInfo([VarStruct('Translation', [2052, 1200])])])''', encoding='utf-8')
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
        '--name', BUILD_NAME, '--distpath', str(ROOT.parent), '--workpath', str(ROOT / 'build' / 'pyinstaller'),
        '--specpath', str(ROOT / 'build'), '--collect-all', 'playwright',
        '--collect-all', 'customtkinter', '--icon', str(ROOT / 'assets' / 'jiaxin.ico'),
        '--exclude-module', 'cv2', '--exclude-module', 'numpy',
        '--version-file', str(version_file),
        '--add-data', f'{ROOT / "assets"}:assets',
        '--add-binary', f'{PLAYER}:media',
        '--add-data', f'{ROOT / "organizer" / "downloads" / "js"}:organizer/downloads/js',
        *browser_data, str(ROOT / 'launch.pyw'),
    ], cwd=ROOT, check=True)
    (DEST / f'{BUILD_NAME}.exe').replace(DEST / f'{NAME}.exe')
    # User documentation is compiled in organizer.manual_content and shown per
    # module. Do not export separate manuals or release notes beside the app.
    licenses = DEST / 'ThirdPartyLicenses'
    licenses.mkdir(exist_ok=True)
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', licenses / 'Python-LICENSE.txt')
    packages = ['playwright', 'greenlet', 'pyee', 'typing_extensions', 'pyinstaller', 'customtkinter', 'Pillow', 'darkdetect', 'packaging']
    versions = {}
    for name in packages:
        dist = importlib.metadata.distribution(name)
        versions[name] = dist.version
        for entry in dist.files or []:
            if any(word in entry.name.upper() for word in ('LICENSE', 'NOTICE', 'COPYING')) and '.dist-info' in str(entry):
                shutil.copy2(dist.locate_file(entry), licenses / f'{name}-{entry.name}')
    shutil.copytree(ROOT / '.build-assets' / 'media-licenses', licenses / 'MPV', dirs_exist_ok=True)
    vendor=ROOT/'organizer/downloads/js/vendor'
    shutil.copy2(vendor/'THIRD_PARTY_LICENSES.txt',licenses/'Download-Libraries.txt')
    versions['download_libraries']=json.loads((vendor/'versions.json').read_text(encoding='utf-8'))
    removed = trim_runtime(DEST)
    manifest = {'version': VERSION, 'platform': 'Windows 10/11 x64', 'python': platform.python_version(),
                'dependencies': versions, 'browser_folders': [p.name for p in browser_folders()],
                'features': ['author-organizer', 'local-pairing', 'intensity', 'playback-review', 'link-collector', 'search-scraper', 'resource-completion', 'resource-comprehensive-organizer', 'script-duplicates', 'video-duplicates', 'folder-group-organizer', 'resource-downloads', 'contextual-offline-manual'],
                'mpv': 'v0.39.0-615-gca211b5b',
                'user_data_included': False,
                'size_optimization': {'removed_bytes': sum(row['bytes'] for row in removed),
                                      'removed_files': removed, 'browser_languages': ['en-US', 'en-GB', 'zh-CN', 'zh-TW']}}
    (DEST / 'build-info.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    forbidden = {'.state', 'reports', 'session.json', 'settings.json', 'ui-settings.json', 'folder-group-settings.json', 'local-settings.json', 'collector-settings.json', 'search-scraper-settings.json', 'search-scrapes', 'completion-settings.json', 'resource-organize-settings.json', 'resource-organize-scores.sqlite3', 'completion-categories.json', 'completion-collections', 'collections', 'catalog.sqlite3', '.links', 'download-queue.json', 'download-jobs', 'download-profiles', 'download-browser'}
    forbidden.add('pane-layout.json')
    assert not any((DEST/name).exists() for name in ('使用说明.txt', '功能说明', '本次更新.txt')), 'External manual in release'
    for path in DEST.rglob('*'):
        assert not forbidden.intersection(path.relative_to(DEST).parts), f'Private data in release: {path}'
    print(json.dumps({'folder': str(DEST), 'version': VERSION, 'archive_created': False}, indent=2))


if __name__ == '__main__':
    main()
