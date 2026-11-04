"""Offline timing against saved local metadata; never opens media or a browser."""
import argparse
import cProfile
import hashlib
import json
import pstats
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from organizer.model import FileGroup, Topic, TopicIndex, match_group
from organizer.matching import MatchingSession


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--label', default='current')
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--incremental', action='store_true')
    parser.add_argument('--workers', type=int, default=0)
    args = parser.parse_args()
    report = sorted((ROOT / 'reports').glob('*/归类预览.json'))[-1]
    rows = json.loads(report.read_text(encoding='utf-8'))
    groups = [FileGroup(**{**r['group'], 'files': []}) for r in rows]
    with sqlite3.connect((ROOT / '.state/catalog.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        topics = [Topic(**json.loads(r[0])) for r in db.execute('SELECT data FROM topics ORDER BY id')]
    digest = hashlib.sha256(json.dumps([rows, [asdict(t) for t in topics]], sort_keys=True).encode()).hexdigest()
    if args.limit and args.limit < len(groups):
        groups = [groups[i * len(groups) // args.limit] for i in range(args.limit)]
    profiler = cProfile.Profile()
    started = time.perf_counter()
    if args.profile:
        profiler.enable()
    matcher = MatchingSession(workers=args.workers) if args.incremental else None
    if matcher:
        matcher.update(topics)
    indexer = matcher.index if matcher else TopicIndex(topics)
    parallel = matcher.refresh(groups) if matcher and args.workers > 1 else None
    results = []
    for number, group in enumerate(groups):
        result = parallel[number] if parallel else matcher.match(group) if matcher else match_group(group, indexer)
        results.append({'position': number, 'status': result.status, 'author': result.author,
                        'candidates': [[c.topic_id, round(c.score, 8), c.verified, c.eligible] for c in result.candidates]})
        if (number + 1) % 20 == 0:
            print('completed', number + 1, 'elapsed', round(time.perf_counter() - started, 2), flush=True)
    elapsed = time.perf_counter() - started
    if args.profile:
        profiler.disable()
        pstats.Stats(profiler).sort_stats('cumulative').print_stats(16)
    summary = {'input_sha256': digest, 'groups': len(groups), 'topics': len(topics), 'seconds': round(elapsed, 4),
               'profiled': args.profile, 'results': results}
    if matcher:
        scored = matcher.scored_pairs
        tick = time.perf_counter()
        matcher.refresh(groups)
        summary['repeat_seconds'] = round(time.perf_counter() - tick, 4)
        summary['repeat_scored_pairs'] = matcher.scored_pairs - scored
        extra = Topic(-987654321, 'Local benchmark added summary', 'Fixture', 'https://example.test/local-only')
        matcher.update(topics + [extra])
        tick = time.perf_counter()
        matcher.refresh(groups)
        summary['one_new_summary_seconds'] = round(time.perf_counter() - tick, 4)
        summary['one_new_summary_scored_pairs'] = matcher.scored_pairs - scored
        matcher.close()
    folder = ROOT / '.state/diagnostics'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'performance-{args.label}.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'results'}), flush=True)


if __name__ == '__main__':
    main()
