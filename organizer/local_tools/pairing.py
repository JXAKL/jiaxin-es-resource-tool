"""Local pairing evidence and preview selection; never performs file operations."""
from collections import Counter, defaultdict
import heapq
import math
import os
from pathlib import Path
import time

from ..model import file_title
from ..script_axes import has_axis
from .name_match import compare_profiles, profile


def valid_duration(value):
    return isinstance(value, (float, int)) and math.isfinite(value) and value > 0


def duration_evidence(video, script):
    vd, sd = video.get('duration'), script.get('duration')
    if not valid_duration(vd) or not valid_duration(sd):
        return 0., False, '时长未读到；不作为零分拉低名称依据'
    gap = abs(vd - sd)
    score = max(0., 1. - gap / max(2., max(vd, sd) * .05))
    allowance = max(3., max(vd, sd) * .10)
    # Last action is a lower bound on video length, not the video's duration.
    conflict = sd > vd + allowance or (script.get('duration_source') == 'metadata' and gap > allowance)
    reason = f'视频 {vd:.1f}s / 脚本 {sd:.1f}s'
    if conflict:
        reason += '；时长差异明显，需播放核对'
    elif script.get('duration_source') == 'actions' and sd < vd - allowance:
        reason += '；脚本时长仅为末动作时间，允许视频尾部留白'
    return score, conflict, reason


def group_scripts(scripts):
    groups = defaultdict(list)
    for script in scripts:
        path = Path(script['path'])
        groups[(os.path.normcase(str(path.parent.resolve())), file_title(path).casefold())].append(script)
    result = []
    for identity, members in sorted(groups.items()):
        members.sort(key=lambda s: (has_axis(Path(s['path']).stem), s['path'].casefold()))
        representative = dict(members[0])
        representative['group_id'] = identity
        representative['members'] = members
        timed = [m for m in members if valid_duration(m.get('duration'))]
        if timed:
            longest = max(timed, key=lambda m: m['duration'])
            representative['duration'] = longest['duration']
            representative['duration_source'] = longest.get('duration_source')
        representative['script_stem'] = file_title(Path(representative['path']))
        representative['profiles'] = [representative['name_profile']]
        seen = {representative['name_profile']['core']}
        for member in members:
            for hint in (member.get('title'), member.get('original_name')):
                if not isinstance(hint, str) or not hint.strip():
                    continue
                item = profile(hint + '.funscript')
                if item['core'] and item['core'] not in seen:
                    representative['profiles'].append(item); seen.add(item['core'])
        result.append(representative)
    return result


class PairIndex:
    """Indexed fuzzy retrieval, retaining all exact/full-span rivals."""
    def __init__(self, scripts):
        self.scripts = scripts
        self.exact, self.grams, self.ids = defaultdict(set), defaultdict(set), defaultdict(set)
        self.sizes = {}
        for i, script in enumerate(scripts):
            grams = set()
            for p in script['profiles']:
                self.exact[p['core']].add(i)
                self.exact[tuple(sorted(p['tokens'].items()))].add(i)
                grams.update(p['grams'])
                for identity in p['source_ids']:
                    self.ids[identity].add(i)
            self.sizes[i] = len(grams)
            for gram in grams:
                self.grams[gram].add(i)

    def shortlist(self, video, use_name, use_duration):
        if not use_name:
            return self.scripts
        p = video['name_profile']
        exact = self.exact[p['core']] | self.exact[tuple(sorted(p['tokens'].items()))]
        for identity in p['source_ids']:
            exact = exact | self.ids.get(identity, set())
        counts = Counter()
        for gram in p['grams']:
            counts.update(self.grams.get(gram, ()))
        n = len(p['grams'])
        # Check full spans in either direction outside the fuzzy budget. Their
        # competition must never disappear just because many weak names exist.
        full = {i for i, hits in counts.items() if hits == min(n, self.sizes[i])}
        def rank(i):
            overlap = 2 * counts[i] / max(1, n + self.sizes[i])
            ds = duration_evidence(video, self.scripts[i])[0] if use_duration else 0.
            return -(overlap * .6 + ds * .4), i
        ids = exact | full | set(heapq.nsmallest(64, counts, key=rank))
        return [self.scripts[i] for i in sorted(ids)]


def pair_candidate(video, script, use_name, use_duration):
    evidence = compare_profiles(video['name_profile'], script['name_profile'])
    # Explicit filename conflicts cannot be erased by a convenient metadata title.
    if not evidence['conflict']:
        for hint in script['profiles'][1:]:
            hinted = compare_profiles(video['name_profile'], hint)
            if hinted['strong'] and hinted['score'] > evidence['score']:
                evidence = {**hinted, 'reason': '脚本元数据名称：' + hinted['reason']}
    ns = evidence['score']
    ds, dc, dr = duration_evidence(video, script) if use_duration else (0., False, '未启用时长匹配')
    available = valid_duration(video.get('duration')) and valid_duration(script.get('duration'))
    if use_name and use_duration and available:
        total = .40 * ns + .60 * ds
        if evidence['strong'] and not dc:
            total = max(total, ns)
    elif use_name:
        total = ns
    else:
        total = ds
    exact = video['name_profile']['raw'] == script['name_profile']['raw']
    return {'video': video, 'script': script, 'name_score': ns, 'duration_score': ds, 'total': total,
            'name_conflict': evidence['conflict'], 'strong_name': evidence['strong'],
            'duration_conflict': dc, 'duration_reason': dr, 'exact': exact,
            'name_reason': evidence['reason'] + ('；名称未参与评分，仅供参考' if not use_name else '')}


def match_records(videos, scripts, high=95, low=80, use_name=True, use_duration=True, progress=lambda *_: None):
    scripts = group_scripts(scripts)
    index = PairIndex(scripts)
    rows = []
    for position, video in enumerate(videos):
        candidates = [pair_candidate(video, script, use_name, use_duration)
                      for script in index.shortlist(video, use_name, use_duration)]
        exact = [c for c in candidates if c['exact']]
        if not candidates:
            continue
        candidates.sort(key=lambda c: (not c['exact'] if exact else False, -c['total'],
                                       -c['name_score'] if use_name else 0, c['script']['path'].casefold()))
        best = candidates[0]
        if best['total'] * 100 < low and not best['exact'] and not (use_name and best['strong_name']):
            continue
        family = (best['script']['group_id'][0], best['script']['name_profile']['variant_key'])
        family_members = [c for c in candidates if (c['script']['group_id'][0], c['script']['name_profile']['variant_key']) == family]
        relatives = [c for c in family_members if c['exact']] if exact else family_members
        # Preserve the existing largest intensity-variant choice, only within
        # one directory; unrelated same-name copies remain competing candidates.
        selected = max(relatives, key=lambda c: (c['script']['size'], c['total']))
        rivals = [c for c in candidates if c not in family_members and
                  (not exact or c['exact'] or c['strong_name'])]
        rivals.sort(key=lambda c: (-c['total'], -c['name_score'], c['script']['path'].casefold()))
        runner = rivals[0] if rivals else None
        margin = (selected['total'] - runner['total']) * 100 if runner else None
        ambiguous = bool(runner and margin < 5)
        script = selected['script']
        eligible = bool(use_name and selected['strong_name'] and selected['name_score'] * 100 >= high
                        and selected['total'] * 100 >= high and not selected['name_conflict']
                        and not selected['duration_conflict'] and not ambiguous)
        status = '高置信' if eligible else '待核对'
        if selected['exact']:
            status = '已同名（保留）'
        if selected['duration_conflict']:
            status = '时长冲突待核对'
        if selected['name_conflict']:
            status = '编号冲突待核对'
        if ambiguous:
            status = '多脚本候选待核对'
        reason = selected['name_reason']
        if len(relatives) > 1:
            reason += f'；同名核心变体 {len(relatives)} 份，按要求选取最大文件'
        if len(script['members']) > 1:
            reason += f'；已合并 {len(script["members"])} 个轴脚本，仅匹配一次'
        row = dict(funscript=script['name'], video=video['name'], funscript_path=script['path'], video_path=video['path'],
                   script_stem=script['script_stem'], video_stem=Path(video['path']).stem,
                   name_score=selected['name_score'] * 100, duration_score=selected['duration_score'] * 100,
                   total=selected['total'] * 100, video_duration=video['duration'], script_duration=script['duration'],
                   duration_available=use_duration and valid_duration(video.get('duration')) and valid_duration(script.get('duration')),
                   status=status, name_reason=reason, duration_reason=selected['duration_reason'],
                   duration_conflict=selected['duration_conflict'], name_conflict=selected['name_conflict'],
                   runner_up=runner['script']['path'] if runner else '', candidate_margin=margin,
                   auto_eligible=eligible, script_group=script['group_id'],
                   script_paths=[s['path'] for s in script['members']])
        rows.append(row)
        if position % 20 == 0:
            progress(position + 1, len(videos))
            time.sleep(.001)
    claims = defaultdict(list)
    for row in rows:
        claims[tuple(row['script_group'])].append(row)
    for group in claims.values():
        if len(group) > 1:
            leaders = [r for r in group if r['auto_eligible']]
            winner = leaders[0] if len(leaders) == 1 and all(
                leaders[0]['name_score'] - r['name_score'] >= 5 for r in group if r is not leaders[0]) else None
            for row in group:
                row['multi_match'] = True
                if row is winner:
                    row['name_reason'] += '；同脚本其他视频仅有弱名称依据，保留此唯一强对应'
                    continue
                row['auto_eligible'] = False
                if row['status'] == '高置信' or (use_name and row['status'] == '已同名（保留）'):
                    row['status'] = '多视频候选待核对'
    return sorted(rows, key=lambda row: (-row['total'], row['video_path'].casefold()))


def select_confident(rows):
    added = 0
    for row in rows:
        if (row.get('auto_eligible') and row.get('status') in {'高置信', '已同名（保留）'}
                and not row.get('selection_touched') and not row.get('selected')):
            row['selected'] = True
            added += 1
    return added
