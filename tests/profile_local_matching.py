"""Read-only diagnosis; output counts/timing, not source names or forum bodies."""
import argparse
import cProfile
from dataclasses import asdict
import hashlib
from collections import Counter
import json
import os
from pathlib import Path
import pstats
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.model import FileGroup, Topic, TopicIndex
from organizer.evidence import candidate_for
from organizer.matching import MatchingSession
from organizer.forum import Cancelled


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default=str(Path(os.environ['LOCALAPPDATA']) / 'EroScriptsOrganizer'))
    parser.add_argument('--batch', action='store_true')
    parser.add_argument('--seconds', type=float, default=60)
    args = parser.parse_args()
    root = Path(args.data)
    reports = sorted((root / 'reports').glob('*/归类预览.json'))
    rows = json.loads(reports[-1].read_text(encoding='utf-8'))
    groups = [FileGroup(**{**r['group'], 'files': []}) for r in rows]
    with sqlite3.connect((root / '.state/catalog.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        topics = [Topic(**json.loads(row[0])) for row in db.execute('SELECT data FROM topics ORDER BY id')]
    lengths = sorted(len(t.body) for t in topics)
    print(json.dumps({'groups':len(groups), 'topics':len(topics),
                      'body_max':lengths[-1], 'body_p95':lengths[int(len(lengths)*.95)],
                      'hints_max':max(len(g.hints) for g in groups)}, indent=2), flush=True)
    if args.batch:
        cache = Path(__file__).resolve().parents[1] / 'build/stall-check-scores.sqlite3'
        matcher = MatchingSession(workers=4, cache_path=cache)
        matcher.update(topics)
        begun = time.monotonic()
        first_result = None
        latest = 0
        def emit(kind, value):
            nonlocal first_result, latest
            if kind == 'progress':
                latest = max(latest, value[0])
                if latest and first_result is None:
                    first_result = round(time.monotonic()-begun, 3)
        def stop():
            if time.monotonic()-begun > args.seconds:
                raise Cancelled('read-only benchmark time budget')
        complete = False
        try:
            output = matcher.refresh(groups, stop=stop, emit=emit)
            complete = True
        except Cancelled:
            output = [item[2] for item in matcher.results.values()]
        finally:
            matcher.dispose()
        report = {'groups':len(groups), 'topics':len(topics), 'finished_groups':len(output),
                  'all_finished':complete, 'seconds':round(time.monotonic()-begun,3),
                  'first_result_seconds':first_result, 'scored_pairs':matcher.scored_pairs,
                  'reused_pairs':matcher.reused_pairs, 'work':matcher.last_work,
                  'statuses':dict(Counter(result.status for result in output))}
        if complete:
            digest = lambda matches: hashlib.sha256(json.dumps([asdict(m) for m in matches], sort_keys=True).encode()).hexdigest()
            expected = digest(output)
            again = MatchingSession(workers=4, cache_path=cache)
            try:
                again.update(topics)
                reload_begin = time.monotonic()
                restored = again.refresh(groups)
                report['reopen_seconds'] = round(time.monotonic()-reload_begin, 3)
                report['reopen_new_scores'] = again.scored_pairs
                report['reopen_reused_pairs'] = again.reused_pairs
                report['reopen_identical_results'] = digest(restored) == expected
                assert report['reopen_identical_results'] and again.scored_pairs == 0
            finally:
                again.dispose()
        Path('build/stall-batch-after.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return
    index = TopicIndex(topics)
    started = time.monotonic()
    shortlists = [index.shortlist(group) for group in groups]
    print(json.dumps({'shortlisting_seconds':round(time.monotonic()-started,3),
                      'candidate_pairs':sum(map(len, shortlists))}), flush=True)
    profile = cProfile.Profile()
    measured = []
    for number in (0, len(groups)//4, len(groups)//2, len(groups)-1):
        group = groups[number]
        for topic in sorted(shortlists[number], key=lambda t: -len(t.body))[:3]:
            start = time.monotonic()
            def checkpoint():
                if time.monotonic()-start > 2:
                    raise TimeoutError('diagnostic sample budget')
            profile.enable()
            try:
                candidate_for(group, topic, checkpoint)
                done = True
            except TimeoutError:
                done = False
            finally:
                profile.disable()
            measured.append({'group_index':number, 'hints':len(group.hints), 'body_length':len(topic.body),
                             'seconds':round(time.monotonic()-start,3), 'completed':done})
    print(json.dumps(measured, indent=2), flush=True)
    pstats.Stats(profile).strip_dirs().sort_stats('cumtime').print_stats(18)


if __name__ == '__main__':
    main()
