"""Read existing local ES indexes, without network or changes to user data."""
import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from organizer.model import Topic
from organizer.resource_info import resource_links


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    args=parser.parse_args()
    paths=[ROOT/'.state/catalog.sqlite3', Path(os.environ['LOCALAPPDATA'])/'EroScriptsOrganizer/.state/catalog.sqlite3']
    total=Counter(); hosts=Counter(); shapes=Counter(); examples=defaultdict(list); raw_hosts=Counter(); raw_schemes=Counter(); routes=Counter()
    for path in paths:
        if not path.is_file():continue
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            extra={r[0]:json.loads(r[1]) for r in db.execute('SELECT id,data FROM topic_resources')} if 'topic_resources' in tables else {}
            for identity, data in db.execute('SELECT id,data FROM topics'):
                row=json.loads(data); row.update(extra.get(identity,{}));topic=Topic(**row)
                total['topics']+=1
                total['raw_links']+=len(topic.links)+len(topic.resource_links)
                for raw in topic.links+[r.get('url','') for r in topic.resource_links]:
                    try:
                        u=urlsplit(raw);raw_hosts[u.hostname or '(no host)']+=1;raw_schemes[u.scheme or '(no scheme)']+=1
                        host=(u.hostname or '').removeprefix('www.')
                        if host in {'eporner.com','mmdfans.net','faptap.net','pmvhaven.com','disk.yandex.com','spankbang.com','sexlikereal.com','hmvmania.com','hentaibar.com'}:
                            parts=u.path.split('/')[1:]
                            route='/'.join(p if p in {'video','videos','view','scenes','hd-porn','d','i','s','f','download','watch'} else '{id}' if p else '' for p in parts)
                            routes[host+' /'+route]+=1
                            if host in {'eporner.com','faptap.net','mmdfans.net','sexlikereal.com','pmvhaven.com'}:
                                head=parts[0] if parts else ''
                                if head.startswith('video-'):head='video-{id}'
                                elif len(head)>20:head='{slug}'
                                routes['route-prefix '+host+' /'+head]+=1
                    except ValueError:raw_schemes['invalid']+=1
                links=resource_links(topic)
                total['accepted']+=len(links)
                for link in links:
                    total['kind_'+link['kind']]+=1
                    u=urlsplit(link['url']);host=(u.hostname or '').lower()
                    hosts[host]+=1
                    p=unquote(u.path).lower()
                    suffix=Path(p).suffix
                    shape=host+' /'+('/'.join(p.split('/')[1:2]))+'/* '+suffix
                    shapes[shape]+=1
                    if suffix in {'.jpg','.jpeg','.png','.webp','.gif','.svg','.avif'}:total['accepted_images']+=1
                    if link['kind']=='purchase':total['accepted_purchase']+=1
                    if len(examples[host])<3:
                        # Report only path shape and post id; never share keys or signed queries.
                        examples[host].append(dict(topic=identity,shape=shape,kind=link['kind']))
    for key in ('accepted_images','accepted_purchase'):total.setdefault(key,0)
    report=dict(counts=dict(total),hosts=hosts.most_common(),shapes=shapes.most_common(),examples=dict(examples),
                raw_hosts=raw_hosts.most_common(),raw_schemes=dict(raw_schemes),routes=routes.most_common())
    output=ROOT/'build'/('resource-link-audit-'+args.label+'.json')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(counts=dict(total),hosts=hosts.most_common(35),raw_hosts=raw_hosts.most_common(100),raw_schemes=dict(raw_schemes)),ensure_ascii=False))


if __name__=='__main__':main()
