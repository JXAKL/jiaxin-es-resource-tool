"""Exercise the actual release after relocation, without developer PATH entries."""
import json
import os
import subprocess
import shutil
from pathlib import Path

from build_release import NAME, ROOT, VERSION, DEST


def main():
    folder = ROOT / 'build' / f'便携验证 Portable {VERSION}'
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copytree(DEST, folder / NAME, dirs_exist_ok=True)
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PLAYWRIGHT_NODEJS_PATH', 'ES_ORGANIZER_DATA_DIR'):
        env.pop(key, None)
    windows = Path(env.get('SYSTEMROOT', r'C:\Windows'))
    env['PATH'] = os.pathsep.join((str(windows / 'System32'), str(windows)))
    env['ES_ORGANIZER_DATA_DIR'] = str(folder / 'isolated-data')
    env['PLAYWRIGHT_BROWSERS_PATH'] = str(folder / 'absent-browser-cache')
    report = folder / 'packaged-selftest.json'
    report.unlink(missing_ok=True)
    result = subprocess.run([str(folder / NAME / f'{NAME}.exe'), '--self-test', str(report)],
                            env=env, cwd=folder, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    if report.exists():
        data = json.loads(report.read_text(encoding='utf-8'))
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        data = {}
    assert result.returncode == 0 and data.get('passed'), 'Packaged self-test failed; inspect isolated-data/.state/logs/startup.log'


if __name__ == '__main__':
    main()
