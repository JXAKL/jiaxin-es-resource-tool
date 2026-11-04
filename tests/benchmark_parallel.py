"""Sequentially compare worker counts against local metadata, without web access.

Run each invocation separately so worker CPU/memory measurements never compete.
Results contain hashes, counts and timings, never source filenames or post bodies.
"""
import argparse
import ctypes
import hashlib
import json
import sqlite3
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from organizer.matching import MatchingSession
from organizer.model import FileGroup, Topic
from organizer.parallel_check import parallel_fixture


def working_set(pid):
    if sys.platform != 'win32':
        return None
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                      'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                      'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]]
    kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        return None
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return round(counters.PeakWorkingSetSize / 1024 ** 2, 2)
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--groups', type=int, default=1)
    parser.add_argument('--fixture', action='store_true')
    args = parser.parse_args()
    if args.fixture:
        groups, topics = parallel_fixture()
        if args.groups > 1:
            groups = [replace(groups[0], key=f'parallel-probe-{i}') for i in range(args.groups)]
    else:
        report = sorted((ROOT / 'reports').glob('*/归类预览.json'))[-1]
        rows = json.loads(report.read_text(encoding='utf-8'))
        groups = [FileGroup(**{**r['group'], 'files': []}) for r in rows]
        if args.groups < len(groups):
            groups = [groups[i * len(groups) // args.groups] for i in range(args.groups)]
        with sqlite3.connect((ROOT / '.state/catalog.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            topics = [Topic(**json.loads(row[0])) for row in db.execute('SELECT data FROM topics ORDER BY id')]
    matcher = MatchingSession(workers=args.workers)
    result = {'workers': args.workers, 'groups': len(groups), 'topics': len(topics)}
    result['input_sha256'] = hashlib.sha256(json.dumps([asdict(g) for g in groups] + [asdict(t) for t in topics], sort_keys=True).encode()).hexdigest()
    try:
        started = time.perf_counter()
        matcher.update(topics)
        result['initial_index_seconds'] = round(time.perf_counter() - started, 4)
        started = time.perf_counter()
        outputs = matcher.refresh(groups)
        result['cold_seconds'] = round(time.perf_counter() - started, 4)
        result['work'] = matcher.last_work
        for pid, data in result['work']['processes'].items():
            data['peak_working_set_mib'] = working_set(pid)
        result['output_sha256'] = hashlib.sha256(json.dumps([asdict(o) for o in outputs], sort_keys=True).encode()).hexdigest()
        before = matcher.scored_pairs
        started = time.perf_counter()
        matcher.refresh(groups)
        result['repeat_seconds'] = round(time.perf_counter() - started, 4)
        result['repeat_scored_pairs'] = matcher.scored_pairs - before
        ids = {t.id for t in matcher.index.shortlist(groups[0])[:8]}
        changed = [replace(t, body=t.body + '\nOffline benchmark update.') if t.id in ids else t for t in topics]
        started = time.perf_counter()
        matcher.update(changed)
        result['incremental_index_seconds'] = round(time.perf_counter() - started, 4)
        started = time.perf_counter()
        matcher.refresh(groups)
        result['incremental_seconds'] = round(time.perf_counter() - started, 4)
        result['incremental_scored_pairs'] = matcher.scored_pairs - before
        result['incremental_work'] = matcher.last_work
    finally:
        matcher.close()
    path = ROOT / 'build' / f'parallel-{args.workers}w-{args.groups}g{"-fixture" if args.fixture else ""}.json'
    path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
