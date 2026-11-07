"""Source and packaged layouts; never write runtime data into bundled files."""
import os
import sys
from pathlib import Path

APP_NAME = '夹心的ES资源工具'
APP_VERSION = '2.1.27'
FROZEN = bool(getattr(sys, 'frozen', False))
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[1]
BUNDLE = Path(getattr(sys, '_MEIPASS', ROOT))


def data_root():
    override = os.environ.get('ES_ORGANIZER_DATA_DIR')
    if override:
        return Path(override).resolve()
    if FROZEN:
        local = os.environ.get('LOCALAPPDATA')
        return (Path(local) if local else Path.home() / 'AppData/Local') / 'EroScriptsOrganizer'
    return ROOT


DATA_ROOT = data_root()
STATE = DATA_ROOT / '.state'
REPORTS = DATA_ROOT / 'reports'
LEGACY = None if FROZEN else ROOT.parent / 'eroscripts_gofile/data/eroscripts_state.json'
DEFAULT_BROWSER = 'Chromium' if FROZEN and (BUNDLE / 'browsers').is_dir() else 'Chrome'


def configure_bundled_browser():
    if FROZEN and (BUNDLE / 'browsers').is_dir():
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(BUNDLE / 'browsers')
