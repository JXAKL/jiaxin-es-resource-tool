"""Directional, unique local pairing; auxiliary files never define scope."""
from collections import defaultdict
from pathlib import Path
import time

from .local_tools.name_match import profile
from .local_tools.pairing import PairIndex, pair_candidate


def links_for(sources):
    roots = [Path(s.path).resolve() for s in sources]
    positions = {root: i for i,root in enumerate(roots)}
    links, used = {}, set()
    for i,source in enumerate(sources):
        if not source.partner:
            continue
        target = Path(source.partner).resolve()
        j = positions.get(target)
        if j is None or j == i:
            raise ValueError('关联目录必须是已添加的另一个目录。')
        if {source.mode,sources[j].mode} != {'videos','scripts'}:
            raise ValueError('只能关联一个仅视频目录与一个仅脚本目录。')
        if i in used or j in used or sources[j].partner:
            raise ValueError('一个目录只能属于一组关联，不能循环或重复关联。')
        links[i]=j;used.update((i,j))
    return links


def record(group):
    f=group.files[0]
    # Work-level pairing includes all versions. Playback pairing remains strict.
    name_profile=profile(group.title+Path(f.path).suffix)
    profiles=[name_profile]
    for hint in group.hints:
        p=profile(hint+'.funscript')
        if p['core'] and all(p['core']!=old['core'] for old in profiles):profiles.append(p)
    return dict(path=f.path,name=Path(f.path).name,duration=None,name_profile=name_profile,
                profiles=profiles,group_id=group.key, size=f.size)


def pair_groups(primary, auxiliary, mode, check=lambda:None):
    """Return only unique strong matches, using the existing local pair scorer.

    In script-primary mode evaluate video->script evidence too, so script
    metadata keeps its role. Retrieval queries every available primary hint.
    """
    records=[record(g) for g in auxiliary]
    by_path={r['path']:i for i,r in enumerate(records)}
    index=PairIndex(records)
    decisions={};notes={};claims=defaultdict(list)
    for i,group in enumerate(primary):
        if i%20==0:time.sleep(.001)
        check();main=record(group);found={}
        for p in main['profiles']:
            for candidate in index.shortlist(dict(main,name_profile=p),True,False):
                found[candidate['path']]=candidate
        scored=[]
        for other in found.values():
            check()
            evidence=pair_candidate(main,other,True,False) if mode=='videos' else pair_candidate(other,main,True,False)
            if evidence['name_conflict']:continue
            scored.append((evidence['name_score'],by_path[other['path']],evidence))
        scored.sort(key=lambda x:(-x[0],x[1]))
        if not scored:
            notes[i]='关联目录未找到可靠配套；辅助目录其余文件未纳入'
            continue
        best=scored[0];score,j,evidence=best
        strong=(evidence['strong_name'] and score>=.95) or evidence['exact']
        if not strong or (len(scored)>1 and score-scored[1][0]<.05-1e-9):
            notes[i]='配套证据不足或多个候选接近，未自动纳入辅助文件'
            continue
        decisions[i]=(j,score,evidence['name_reason'])
        claims[j].append(i)
    for j,owners in claims.items():
        if len(owners)<2:continue
        ordered=sorted(owners,key=lambda i:-decisions[i][1])
        winner=ordered[0] if decisions[ordered[0]][1]-decisions[ordered[1]][1]>=.05-1e-9 else None
        for i in owners:
            if i!=winner:
                del decisions[i]
                notes[i]='同一配套被多个主目录组竞争，未重复纳入'
    return decisions,notes
