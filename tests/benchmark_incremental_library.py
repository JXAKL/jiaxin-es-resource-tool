"""Offline reproduction using an explicit private local diagnostic snapshot."""
import cProfile
import json
import os
from pathlib import Path
import pstats
import sqlite3
import sys
import time
from dataclasses import replace, asdict
import hashlib
import argparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from organizer.model import FileGroup
from organizer.matching import MatchingSession
from organizer.store import Store

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshot',action='store_true')
    parser.add_argument('--label',default='baseline')
    args=parser.parse_args()
    folder=ROOT/'build/performance-2.1.3'; folder.mkdir(parents=True,exist_ok=True)
    if args.snapshot:
        user=Path(os.environ['LOCALAPPDATA'])/'EroScriptsOrganizer'
        report=max(user.joinpath('reports').glob('*/归类预览.json'),key=lambda p:p.stat().st_mtime_ns)
        rows=json.loads(report.read_text(encoding='utf-8'))
        groups=[{**r['group'],'files':[],'search_queries':[],'search_hits':{}} for r in rows]
        (folder/'groups.json').write_text(json.dumps(groups,ensure_ascii=False),encoding='utf-8')
        for name in ('catalog.sqlite3','local-scores.sqlite3'):
            with sqlite3.connect((user/'.state'/name).as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(folder/name) as dst:
                src.backup(dst)
        print(json.dumps({'snapshot_groups':len(groups)})); return
    groups=[FileGroup(**r) for r in json.loads((folder/'groups.json').read_text(encoding='utf-8'))]
    store=Store(folder/'catalog.sqlite3')
    matcher=MatchingSession(workers=8,cache_path=folder/'local-scores.sqlite3')
    report={'groups':len(groups)}
    try:
        start=time.perf_counter(); matcher.sync(store); report['index_seconds']=time.perf_counter()-start
        topics=list(matcher.index.topics.values()); report['topics']=len(topics)
        start=time.perf_counter(); first=matcher.refresh(groups); report['warm_refresh_seconds']=time.perf_counter()-start
        print(json.dumps({**report,'phase':'initial_complete'}),flush=True)
        identity=next(c.topic_id for m in first for c in m.candidates if c.verified)
        changed=[replace(t,body=t.body+'\nOffline benchmark appendix.') if t.id==identity else t for t in topics]
        start=time.perf_counter(); matcher.update(changed); report['update_seconds']=time.perf_counter()-start
        score_before=matcher.scored_pairs; profile=cProfile.Profile(); profile.enable()
        start=time.perf_counter(); results=matcher.refresh(groups); report['incremental_seconds']=time.perf_counter()-start
        profile.disable(); report['scored_pairs']=matcher.scored_pairs-score_before
        report['work']=matcher.last_work
        report['decisions_sha256']=hashlib.sha256(json.dumps([asdict(m) for m in results],sort_keys=True).encode()).hexdigest()
        with (folder/(args.label+'-profile.txt')).open('w',encoding='utf-8') as stream:
            pstats.Stats(profile,stream=stream).sort_stats('cumulative').print_stats(22)
        (folder/(args.label+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report),flush=True)
    finally: matcher.dispose(); store.close()

if __name__=='__main__': main()
