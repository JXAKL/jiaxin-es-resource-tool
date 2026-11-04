"""Offline browser/transfer smoke check, also callable from a relocated EXE."""
import json
import os
from pathlib import Path
import subprocess

from .service import runtime_paths
from ..runtime import configure_bundled_browser


def run_download_check(folder):
    node, module, runner = runtime_paths()
    configure_bundled_browser()
    env = os.environ.copy()
    env['ES_RESOURCE_PLAYWRIGHT'] = str(module)
    env.pop('NODE_OPTIONS', None)
    work = Path(folder) / 'download-check'
    work.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([str(node), str(runner.with_name('check.js')), str(work)],
                            cwd=work, env=env, text=True, encoding='utf-8', errors='replace',
                            capture_output=True, timeout=70,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise AssertionError((result.stdout + result.stderr)[-7000:])
    line = next((x for x in result.stdout.splitlines() if x.startswith('DOWNLOAD_CHECK ')), None)
    assert line, result.stdout[-2000:]
    return json.loads(line.removeprefix('DOWNLOAD_CHECK '))
