"""Compare matching versions on an explicit, private metadata-only snapshot."""
import argparse
from collections import Counter
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from organizer.matching import MatchingSession
from organizer.model import FileGroup, Topic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    groups = [FileGroup(**{**r['group'], 'files': []}) for r in
              json.loads((args.folder / 'baseline.json').read_text(encoding='utf-8'))]
    topics = [Topic(**t) for t in json.loads((args.folder / 'topics.json').read_text(encoding='utf-8'))['topics']]
    matcher = MatchingSession(workers=args.workers, cache_path=args.folder / (args.label + '-scores.sqlite3'))
    report = {'groups': len(groups), 'topics': len(topics)}
    try:
        started = time.perf_counter()
        matcher.update(topics)
        report['index_seconds'] = time.perf_counter() - started
        started = time.perf_counter()
        matches = matcher.refresh(groups)
        report['cold_seconds'] = time.perf_counter() - started
        report['work'] = matcher.last_work
        report['statuses'] = dict(Counter(m.status for m in matches))
        (args.folder / (args.label + '-matches.json')).write_text(
            json.dumps([asdict(m) for m in matches], ensure_ascii=False), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
        before = matcher.scored_pairs
        started = time.perf_counter()
        matcher.refresh(groups)
        report['repeat_seconds'] = time.perf_counter() - started
        report['repeat_pairs'] = matcher.scored_pairs - before
        ids = list(dict.fromkeys(c.topic_id for m in matches for c in m.candidates if c.verified))[:8]
        report['incremental'] = []
        for identity in ids:
            topics = [replace(t, body=t.body + '\nOffline diagnostic appendix.') if t.id == identity else t for t in topics]
            before = matcher.scored_pairs
            started = time.perf_counter()
            matcher.update(topics)
            matcher.refresh(groups)
            report['incremental'].append({'seconds': time.perf_counter() - started,
                                          'pairs': matcher.scored_pairs - before})
        (args.folder / (args.label + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        matcher.dispose()


if __name__ == '__main__':
    main()
