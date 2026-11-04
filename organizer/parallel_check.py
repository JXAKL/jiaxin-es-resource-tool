"""Real, offline process-pool check, also safe from the frozen windowed launcher."""
import random
import time
from dataclasses import asdict, replace

from .matching import MatchingSession
from .model import FileGroup, Topic


def parallel_fixture():
    rng = random.Random(8821)
    vocabulary = [''.join(rng.choices('abcdefghijklmnoprstuvw', k=8)) for _ in range(80)]
    topics = []
    for number in range(32):
        passages = [' '.join(rng.sample(vocabulary, 7)) for _ in range(160)]
        topics.append(Topic(number + 1, 'Aurora Harbor Performance', 'Fixture', '',
                            body='\n'.join(passages), details_loaded=True))
    group = FileGroup('parallel-probe', 'Aurora Harbor Performance Extended')
    return [group], topics


def run_parallel_check():
    groups, topics = parallel_fixture()
    report, outputs = {}, []
    for workers in (1, 2):
        matcher = MatchingSession(workers=workers)
        try:
            matcher.update(topics)
            started = time.perf_counter()
            # Deliberately use match(), with one group: this was always serial.
            output = matcher.match(groups[0])
            elapsed = time.perf_counter() - started
            usage = dict(matcher.last_work)
            outputs.append(asdict(output))
            assert sum(row['pairs'] for row in usage['processes'].values()) == 32
            if workers == 2:
                assert len(usage['processes']) == 2, usage
                assert all(row['cpu_seconds'] > 0 for row in usage['processes'].values()), usage
                before = matcher.scored_pairs
                matcher.refresh(groups)
                matcher.refresh(groups)
                assert matcher.scored_pairs == before
                # Changing non-evidence metadata must not rebuild or rescore.
                assert not matcher.update([replace(t, views=99, links=['https://example.invalid/fixture']) for t in topics])
            report[str(workers)] = {'seconds': round(elapsed, 4), **usage}
        finally:
            matcher.close()
    assert outputs[0] == outputs[1], 'Parallel scores differ from serial scores'
    report['identical_results'] = True
    report['unchanged_rescored_pairs'] = 0
    return report
